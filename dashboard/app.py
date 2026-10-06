"""
Unified Prop Account Dashboard (Flask).
Wave 1+2: stable MT5 session, multi-terminal picker, prop metrics + charts.

Does NOT start/stop MetaTrader processes — only attaches via MetaTrader5 API
to a terminal the user already has open.
"""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request

import MetaTrader5 as mt5

from prop_metrics import DEFAULT_PROFILE, build_prop_payload
from bridge_reader import (
    account_from_bridge,
    deals_from_bridge,
    load_bridge,
    positions_from_bridge,
)

HERE = os.path.dirname(os.path.abspath(__file__))
TERMINALS_FILE = os.path.join(HERE, "terminals.json")
RESULTS_FILE = os.path.join(HERE, "optimization_results.json")

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"))

_lock = threading.RLock()
_state = {
    "active_id": None,
    "connected_path": None,
    "last_error": None,
}
# Avoid rebuilding prop metrics on every 3s poll when bridge file unchanged
_live_cache: Dict[str, Any] = {"key": None, "payload": None, "ts": 0.0}
_OPT_JOB_STATUS = os.path.join(HERE, "opt_job_status.json")
_opt_proc_lock = threading.Lock()
_opt_proc: Optional[subprocess.Popen] = None


def load_config() -> Dict[str, Any]:
    if not os.path.exists(TERMINALS_FILE):
        return {
            "active_id": "moneta_funded",
            "prop_profile": dict(DEFAULT_PROFILE),
            "terminals": [],
        }
    with open(TERMINALS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: Dict[str, Any]) -> None:
    with open(TERMINALS_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def list_terminals(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    cfg = cfg or load_config()
    out = []
    for t in cfg.get("terminals", []):
        path = t.get("path", "")
        out.append({
            "id": t.get("id"),
            "label": t.get("label"),
            "path": path,
            "enabled": bool(t.get("enabled", True)),
            "exists": os.path.isfile(path),
            "active": t.get("id") == cfg.get("active_id"),
        })
    return out


def get_terminal_by_id(cfg: Dict[str, Any], tid: str) -> Optional[Dict[str, Any]]:
    for t in cfg.get("terminals", []):
        if t.get("id") == tid:
            return t
    return None


def is_terminal_process_running(exe_path: str) -> bool:
    """True only if that exact terminal64.exe path is already running (read-only check).

    Windows 11 often has no `wmic`; prefer CIM via PowerShell, then ctypes Toolhelp.
    """
    target = os.path.normcase(os.path.abspath(exe_path))
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # 1) PowerShell / CIM (wmic is gone on many Win11 installs)
    try:
        out = subprocess.check_output(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" "
                "| Select-Object -ExpandProperty ExecutablePath",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            creationflags=create_no_window,
        )
        for line in out.splitlines():
            p = line.strip().strip('"')
            if p and os.path.normcase(os.path.abspath(p)) == target:
                return True
        return False
    except Exception:
        pass

    # 2) ctypes Toolhelp + QueryFullProcessImageName
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == INVALID_HANDLE_VALUE:
            return False
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            more = kernel32.Process32FirstW(snap, ctypes.byref(pe))
            while more:
                if pe.szExeFile.lower() == "terminal64.exe":
                    h = kernel32.OpenProcess(
                        PROCESS_QUERY_LIMITED_INFORMATION, False, pe.th32ProcessID
                    )
                    if h:
                        try:
                            buf = ctypes.create_unicode_buffer(512)
                            size = wintypes.DWORD(512)
                            if kernel32.QueryFullProcessImageNameW(
                                h, 0, buf, ctypes.byref(size)
                            ):
                                if os.path.normcase(os.path.abspath(buf.value)) == target:
                                    return True
                        finally:
                            kernel32.CloseHandle(h)
                more = kernel32.Process32NextW(snap, ctypes.byref(pe))
        finally:
            kernel32.CloseHandle(snap)
        return False
    except Exception:
        return False


def ensure_mt5(path: str) -> bool:
    """Attach to an ALREADY RUNNING terminal. Never initialize if process is down
    (mt5.initialize can auto-launch terminal.exe — we forbid that)."""
    with _lock:
        if _state["connected_path"] == path and mt5.terminal_info() is not None:
            _state["last_error"] = None
            return True

        if not is_terminal_process_running(path):
            _state["connected_path"] = None
            _state["last_error"] = (
                f"Terminal is not running: {path}. "
                "Open and log into that MetaTrader yourself, then select it here. "
                "Dashboard will not launch MetaTrader."
            )
            return False

        try:
            mt5.shutdown()
        except Exception:
            pass

        ok = mt5.initialize(path=path)
        if not ok:
            err = mt5.last_error()
            _state["connected_path"] = None
            _state["last_error"] = f"MT5 initialize failed for {path}: {err}"
            return False

        _state["connected_path"] = path
        _state["last_error"] = None
        return True


def fetch_deals() -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    """Returns (deals, error). None deals means API failure (not empty history).

    Requests the widest range MT5 accepts (All History). Whatever comes back is
    limited by what the terminal has synced — not by a monthly filter in our code.
    """
    # 1970 can throw on some builds; 2000-01-01 is safe and covers all prop accounts.
    from_date = datetime(2000, 1, 1)
    to_date = datetime.now() + timedelta(days=1)
    deals = mt5.history_deals_get(from_date, to_date)
    if deals is None:
        return None, f"history_deals_get failed: {mt5.last_error()}"
    deal_list: List[Dict[str, Any]] = []
    for d in deals:
        deal_list.append({
            "ticket": int(d.ticket),
            "order": int(d.order),
            "position_id": int(d.position_id),
            "time": int(d.time),
            "time_msc": int(d.time_msc),
            "type": int(d.type),
            "entry": int(d.entry),
            "symbol": str(d.symbol),
            "volume": float(d.volume),
            "price": float(d.price),
            "profit": float(d.profit),
            "swap": float(d.swap),
            "commission": float(d.commission),
            "comment": str(d.comment),
        })
    return deal_list, None


def fetch_positions() -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    positions = mt5.positions_get()
    if positions is None:
        return None, f"positions_get failed: {mt5.last_error()}"
    open_positions = []
    for p in positions:
        open_positions.append({
            "ticket": int(p.ticket),
            "symbol": str(p.symbol),
            "type": "BUY" if p.type == 0 else "SELL",
            "volume": float(p.volume),
            "price_open": float(p.price_open),
            "price_current": float(p.price_current),
            "sl": float(p.sl),
            "tp": float(p.tp),
            "profit": round(float(p.profit + p.swap), 2),
            "duration_sec": int((datetime.now() - datetime.fromtimestamp(p.time)).total_seconds()),
            "comment": str(p.comment),
        })
    return open_positions, None


def get_live_payload() -> Dict[str, Any]:
    cfg = load_config()
    active_id = cfg.get("active_id")
    term = get_terminal_by_id(cfg, active_id) if active_id else None
    if term is None:
        terms = cfg.get("terminals") or []
        term = terms[0] if terms else None

    bridge_cfg = cfg.get("bridge") or {}
    prefer_bridge = bool(bridge_cfg.get("prefer", True))
    bridge_file = str(bridge_cfg.get("filename") or "moneta_bridge.json")
    max_age = int(bridge_cfg.get("max_age_sec") or 45)
    allow_mt5_fallback = bool(bridge_cfg.get("mt5_fallback", True))

    # --- Preferred path: EA file bridge (no Python MT5 attach) ---
    bridge_err = None
    if prefer_bridge:
        raw, bridge_err = load_bridge(bridge_file, max_age)
        if raw is not None:
            cache_key = (
                raw.get("_bridge_mtime"),
                raw.get("write_count"),
                raw.get("deals_count"),
                float(raw.get("account", {}).get("equity") or 0),
                len(raw.get("positions") or []),
            )
            now_ts = time.time()
            # Reuse heavy metrics for a few seconds if bridge snapshot unchanged
            if (
                _live_cache.get("key") == cache_key
                and _live_cache.get("payload") is not None
                and (now_ts - float(_live_cache.get("ts") or 0)) < 8.0
            ):
                payload = copy.deepcopy(_live_cache["payload"])
                payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                payload["bridge"] = {
                    **(payload.get("bridge") or {}),
                    "age_sec": raw.get("_bridge_age_sec"),
                    "cached": True,
                }
                return payload

            account = account_from_bridge(raw)
            deals = deals_from_bridge(raw)
            positions = positions_from_bridge(raw)
            term_meta = {
                "id": (term or {}).get("id") or "ea_bridge",
                "label": (term or {}).get("label") or "EA Bridge",
                "path": (term or {}).get("path") or raw.get("_bridge_path"),
                "active": True,
            }
            payload = build_prop_payload(
                account=account,
                deal_list=deals,
                open_positions=positions,
                profile=cfg.get("prop_profile") or DEFAULT_PROFILE,
                now=datetime.now(),
                terminal=term_meta,
            )
            payload["ok"] = True
            payload["terminals"] = list_terminals(cfg)
            payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            payload["data_source"] = "ea_bridge"
            payload["quotes"] = raw.get("quotes") or {}
            payload["bridge"] = {
                "path": raw.get("_bridge_path"),
                "age_sec": raw.get("_bridge_age_sec"),
                "mtime": raw.get("_bridge_mtime"),
                "deals_count": raw.get("deals_count"),
                "history_from": raw.get("history_from"),
                "history_to": raw.get("history_to"),
                "write_count": raw.get("write_count"),
                "prop_ea": raw.get("prop"),
                "cached": False,
            }
            payload["disclaimer"] = (
                "Live data from MonetaDashboardBridge EA (All History file). "
                "Official Moneta portal remains the source of truth for challenge pass/fail."
            )
            _live_cache["key"] = cache_key
            _live_cache["payload"] = payload
            _live_cache["ts"] = now_ts
            return copy.deepcopy(payload)

    if not allow_mt5_fallback:
        return {
            "ok": False,
            "error": bridge_err or "EA bridge unavailable",
            "data_source": "none",
            "hint": (
                "Attach MonetaDashboardBridge.mq5 on a chart (Algo Trading ON). "
                "It writes Common\\Files\\moneta_bridge.json every few seconds."
            ),
            "terminals": list_terminals(cfg),
        }

    # --- Fallback: Python MetaTrader5 API ---
    if term is None:
        return {
            "ok": False,
            "error": "No terminals configured in terminals.json",
            "data_source": "none",
            "bridge_error": bridge_err,
            "terminals": list_terminals(cfg),
        }

    path = term.get("path", "")
    if not os.path.isfile(path):
        return {
            "ok": False,
            "error": f"Terminal executable not found: {path}",
            "data_source": "none",
            "bridge_error": bridge_err,
            "terminals": list_terminals(cfg),
            "terminal": {"id": term.get("id"), "label": term.get("label"), "path": path},
        }

    with _lock:
        if not ensure_mt5(path):
            return {
                "ok": False,
                "error": _state["last_error"] or "MT5 connection failed",
                "data_source": "none",
                "bridge_error": bridge_err,
                "terminals": list_terminals(cfg),
                "terminal": {"id": term.get("id"), "label": term.get("label"), "path": path},
                "hint": (
                    "Prefer attaching MonetaDashboardBridge.mq5 (file bridge). "
                    "Or open that MetaTrader terminal and log in for API fallback."
                ),
            }

        acc = mt5.account_info()
        if acc is None:
            return {
                "ok": False,
                "error": f"Connected to terminal but no account_info (logged in?). last_error={mt5.last_error()}",
                "data_source": "none",
                "bridge_error": bridge_err,
                "terminals": list_terminals(cfg),
                "terminal": {"id": term.get("id"), "label": term.get("label"), "path": path},
            }

        account = {
            "login": int(acc.login),
            "name": str(acc.name),
            "server": str(acc.server),
            "company": str(acc.company),
            "currency": str(acc.currency),
            "balance": float(acc.balance),
            "equity": float(acc.equity),
            "profit": float(acc.profit),
            "margin": float(acc.margin),
            "margin_free": float(acc.margin_free),
            "margin_level": float(acc.margin_level) if acc.margin > 0 else 0.0,
            "leverage": int(acc.leverage),
        }

        deals, deals_err = fetch_deals()
        if deals_err:
            return {
                "ok": False,
                "error": deals_err,
                "data_source": "none",
                "bridge_error": bridge_err,
                "terminals": list_terminals(cfg),
                "terminal": {"id": term.get("id"), "label": term.get("label"), "path": path},
            }
        positions, pos_err = fetch_positions()
        if pos_err:
            return {
                "ok": False,
                "error": pos_err,
                "data_source": "none",
                "bridge_error": bridge_err,
                "terminals": list_terminals(cfg),
                "terminal": {"id": term.get("id"), "label": term.get("label"), "path": path},
            }

    payload = build_prop_payload(
        account=account,
        deal_list=deals or [],
        open_positions=positions or [],
        profile=cfg.get("prop_profile") or DEFAULT_PROFILE,
        now=datetime.now(),
        terminal={
            "id": term.get("id"),
            "label": term.get("label"),
            "path": path,
            "active": True,
        },
    )
    payload["ok"] = True
    payload["terminals"] = list_terminals(cfg)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["data_source"] = "mt5_api"
    payload["bridge_error"] = bridge_err
    payload["disclaimer"] = (
        "Calculations are approximate from MT5 Python API history. "
        "For stabler reads attach MonetaDashboardBridge.mq5. "
        "Official Moneta portal remains the source of truth for challenge pass/fail."
    )
    return payload


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/terminals", methods=["GET"])
def api_terminals():
    cfg = load_config()
    return jsonify({
        "active_id": cfg.get("active_id"),
        "terminals": list_terminals(cfg),
        "prop_profile": cfg.get("prop_profile") or DEFAULT_PROFILE,
        "connected_path": _state.get("connected_path"),
        "last_error": _state.get("last_error"),
    })


@app.route("/api/terminals/select", methods=["POST"])
def api_terminals_select():
    body = request.get_json(silent=True) or {}
    tid = body.get("id")
    if not tid:
        return jsonify({"ok": False, "error": "missing id"}), 400
    cfg = load_config()
    term = get_terminal_by_id(cfg, tid)
    if term is None:
        return jsonify({"ok": False, "error": f"unknown terminal id: {tid}"}), 404
    if not term.get("enabled", True):
        return jsonify({"ok": False, "error": "terminal disabled"}), 400
    cfg["active_id"] = tid
    save_config(cfg)
    with _lock:
        # force reconnect on next data poll
        try:
            mt5.shutdown()
        except Exception:
            pass
        _state["connected_path"] = None
    return jsonify({"ok": True, "active_id": tid, "terminals": list_terminals(cfg)})


@app.route("/api/terminals/add", methods=["POST"])
def api_terminals_add():
    """Add another terminal path for later selection (does not launch it)."""
    body = request.get_json(silent=True) or {}
    tid = (body.get("id") or "").strip()
    label = (body.get("label") or tid).strip()
    path = (body.get("path") or "").strip()
    if not tid or not path:
        return jsonify({"ok": False, "error": "id and path required"}), 400
    cfg = load_config()
    if get_terminal_by_id(cfg, tid):
        return jsonify({"ok": False, "error": "id already exists"}), 409
    cfg.setdefault("terminals", []).append({
        "id": tid,
        "label": label,
        "path": path,
        "enabled": True,
    })
    save_config(cfg)
    return jsonify({"ok": True, "terminals": list_terminals(cfg)})


@app.route("/api/data")
def api_data():
    data = get_live_payload()
    # Always 200 so UI can show soft errors without fetch throw
    return jsonify(data)


@app.route("/api/optimization/results")
def api_opt_results():
    if os.path.exists(RESULTS_FILE):
        with open(RESULTS_FILE, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"error": "No optimization results available yet. Run optimization first."})


@app.route("/api/optimization/status")
def api_opt_status():
    """Status of the detached optimizer subprocess (does not block Live)."""
    global _opt_proc
    st = {"status": "idle", "symbol": None, "error": None, "pid": None}
    if os.path.isfile(_OPT_JOB_STATUS):
        try:
            with open(_OPT_JOB_STATUS, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            pass
    with _opt_proc_lock:
        proc = _opt_proc
        if proc is not None and proc.poll() is not None:
            # process ended — refresh from status file
            _opt_proc = None
            if st.get("status") == "running":
                st["status"] = "done" if proc.returncode == 0 else "failed"
                if proc.returncode != 0 and not st.get("error"):
                    st["error"] = f"exit code {proc.returncode}"
        running = proc is not None and proc.poll() is None
    st["process_running"] = running
    return jsonify(st)


@app.route("/api/optimization/run", methods=["POST"])
def api_opt_run():
    """Start optimizer in a separate process so Live Monitor stays responsive."""
    global _opt_proc
    body = request.get_json(silent=True) or {}
    symbol = (body.get("symbol") or "").strip().upper()

    def _parse_floats(key, default=None):
        raw = body.get(key)
        if raw is None or raw == "":
            return default
        if isinstance(raw, (list, tuple)):
            return [float(x) for x in raw]
        nums = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", str(raw))
        if not nums:
            return default
        return [float(n) for n in nums]

    top_n = body.get("top_n")
    try:
        top_n = int(top_n) if top_n not in (None, "") else 50
    except (TypeError, ValueError):
        top_n = 50
    top_n = max(10, min(top_n, 500))

    with _opt_proc_lock:
        if _opt_proc is not None and _opt_proc.poll() is None:
            return jsonify({
                "status": "busy",
                "ok": False,
                "error": "Optimizer already running — wait for it to finish (separate process).",
                "pid": _opt_proc.pid,
            }), 409

        script = os.path.join(HERE, "optimizer.py")
        cmd = [sys.executable, script]
        if symbol:
            risks = _parse_floats("risk_pcts") or []
            tps = _parse_floats("profit_xs") or []
            sls = _parse_floats("sl_gaps") or []
            gaps = _parse_floats("level_gaps") or []
            est = max(1, len(risks)) * max(1, len(tps) or 3) * max(1, len(sls) or 2) * max(1, len(gaps) or 1) * 2
            cmd += ["--symbol", symbol, "--top-n", str(top_n)]
            if risks:
                cmd += ["--risk", ",".join(str(x) for x in risks)]
            if tps:
                cmd += ["--tp", ",".join(str(x) for x in tps)]
            if sls:
                cmd += ["--sl", ",".join(str(x) for x in sls)]
            if gaps:
                cmd += ["--gap", ",".join(str(x) for x in gaps)]
            msg = f"Optimizer subprocess for {symbol} (~{est} combos, top {top_n})"
        else:
            est = None
            msg = "Optimizer subprocess: all symbols"

        # Mark running immediately so UI can poll
        with open(_OPT_JOB_STATUS, "w", encoding="utf-8") as f:
            json.dump({
                "status": "starting",
                "symbol": symbol or "ALL",
                "pid": None,
                "error": None,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, indent=2)

        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _opt_proc = subprocess.Popen(
            cmd,
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=create_no_window,
        )
        pid = _opt_proc.pid

    return jsonify({
        "ok": True,
        "status": "started",
        "message": msg,
        "symbol": symbol or None,
        "estimated_combos": est,
        "top_n": top_n if symbol else 10,
        "pid": pid,
        "detached": True,
    })


# ── History cache + MT5 Strategy Tester (real EA) ──────────────────────────

@app.route("/api/history/status")
def api_history_status():
    from tester_runner import load_history_status
    return jsonify(load_history_status())


@app.route("/api/tester/presets")
def api_tester_presets():
    from tester_runner import list_presets
    return jsonify({"presets": list_presets()})


@app.route("/api/tester/run", methods=["POST"])
def api_tester_run():
    """Start a real EA Strategy Tester job (ini + optional /config launch + report watch)."""
    from tester_runner import start_tester_run
    body = request.get_json(silent=True) or {}
    preset_id = (body.get("preset_id") or "").strip() or None
    symbol = (body.get("symbol") or "").strip() or None
    if not preset_id and not symbol:
        return jsonify({"ok": False, "error": "preset_id or symbol required"}), 400

    def _f(key):
        v = body.get(key)
        if v is None or v == "":
            return None
        return float(v)

    result = start_tester_run(
        preset_id=preset_id,
        symbol=symbol,
        strategy=body.get("strategy"),
        from_date=body.get("from_date"),
        to_date=body.get("to_date"),
        launch=bool(body.get("launch", False)),
        deposit=float(body.get("deposit") or 50000),
        terminal_id=body.get("terminal_id"),
        risk_pct=_f("risk_pct"),
        profit_x=_f("profit_x"),
        sl_gap=_f("sl_gap"),
        level_gap=_f("level_gap"),
    )
    code = 200 if result.get("ok") else 400
    return jsonify(result), code


@app.route("/api/tester/status/<run_id>")
def api_tester_status(run_id: str):
    from tester_runner import get_run
    run = get_run(run_id)
    if not run:
        return jsonify({"ok": False, "error": "unknown run_id"}), 404
    return jsonify(run)


@app.route("/api/tester/results")
def api_tester_results():
    from tester_runner import list_tester_results
    return jsonify(list_tester_results())


@app.route("/api/tester/import", methods=["POST"])
def api_tester_import():
    """Import/parse an existing tester HTML report into results store."""
    from tester_runner import import_existing_report
    body = request.get_json(silent=True) or {}
    path = (body.get("path") or "").strip()
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        row = import_existing_report(path, deposit=float(body.get("deposit") or 50000))
        return jsonify({"ok": True, "result": row})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


if __name__ == "__main__":
    # threaded=True: Live polls must not wait behind each other
    app.run(host="127.0.0.1", port=63000, debug=False, use_reloader=False, threaded=True)
