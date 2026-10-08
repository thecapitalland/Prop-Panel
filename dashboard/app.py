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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request

import MetaTrader5 as mt5

from prop_metrics import DEFAULT_PROFILE, build_prop_payload
from provider_profiles import get_profile, list_profiles, normalize_profile, normalize_symbol
from risk_engine import evaluate_risk
from mt5_risk_adapter import aggregate_open_sl_exposure, proposed_trade_exposure, max_volume_for_risk
from bridge_reader import (
    account_from_bridge,
    deals_from_bridge,
    load_bridge,
    positions_from_bridge,
)

HERE = os.path.dirname(os.path.abspath(__file__))
TERMINALS_FILE = os.path.join(HERE, "terminals.json")
TERMINALS_EXAMPLE_FILE = os.path.join(HERE, "terminals.example.json")
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
    """Load local runtime config, falling back to the committed sanitized example."""
    source = TERMINALS_FILE if os.path.exists(TERMINALS_FILE) else TERMINALS_EXAMPLE_FILE
    if os.path.exists(source):
        with open(source, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "active_id": None,
        "profile_id": "moneta_2step_phase1_5_10",
        "personal_risk_policy": {
            "warning_threshold_pct": 70,
            "high_risk_threshold_pct": 85,
            "personal_daily_stop_pct": 2.0,
        },
        "bridge": {
            "prefer": True,
            "filename": "moneta_bridge.json",
            "max_age_sec": 45,
            "mt5_fallback": True,
        },
        "terminals": [],
    }


def active_profile(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve registry profile, optional account-size overrides, or legacy flat config."""
    overrides = cfg.get("prop_profile")
    if overrides is None:
        overrides = cfg.get("profile_overrides")
    return normalize_profile(
        profile=overrides,
        profile_id=cfg.get("profile_id"),
    )


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


def _mt5_profit_at_close(
    symbol: str,
    side: str,
    volume: float,
    price_open: float,
    price_close: float,
) -> float:
    """Use MT5's own contract semantics; never guess pip/tick value math."""
    order_type = mt5.ORDER_TYPE_BUY if str(side).upper() == "BUY" else mt5.ORDER_TYPE_SELL
    value = mt5.order_calc_profit(
        order_type,
        str(symbol),
        float(volume),
        float(price_open),
        float(price_close),
    )
    if value is None:
        raise RuntimeError(f"MT5 order_calc_profit unavailable for {symbol}: {mt5.last_error()}")
    return float(value)


def _risk_guard_payload(
    *,
    payload: Dict[str, Any],
    profile: Dict[str, Any],
    positions: List[Dict[str, Any]],
    calculator=None,
    personal_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build advisory live risk summary. Fail closed when exposure cannot be calculated."""
    if calculator is None and positions:
        return {
            "available": False,
            "advisory_only": True,
            "profile_id": profile.get("profile_id"),
            "provider": profile.get("provider"),
            "reason": "Open-position SL exposure needs MT5 contract calculation; use preview or MT5 API fallback.",
        }
    try:
        risk_profile = copy.deepcopy(profile)
        if not bool((payload.get("daily_drawdown") or {}).get("available", True)):
            risk_profile["daily_loss_limit_usd"] = 0.0
        exposure = (
            aggregate_open_sl_exposure(
                positions,
                calculator,
                symbol_normalizer=lambda s: normalize_symbol(s, profile),
            )
            if positions
            else {
                "known_risk_usd": 0.0,
                "entry_risk_usd": 0.0,
                "current_loss_usd": 0.0,
                "risk_by_symbol": {},
                "current_loss_by_symbol": {},
                "unbounded_positions": 0,
                "positions": [],
            }
        )
        risk = evaluate_risk(
            account=payload.get("account") or {},
            profile=risk_profile,
            open_sl_risk_usd=float(exposure["known_risk_usd"]),
            proposed_trade_risk_usd=0.0,
            unbounded_positions=int(exposure["unbounded_positions"]),
            personal_policy=personal_policy,
            day_start_reference=float((payload.get("daily_drawdown") or {}).get("starting_balance") or 0.0),
            open_concurrent_risk_usd=float(exposure.get("entry_risk_usd") or 0.0),
            open_risk_by_symbol=exposure.get("risk_by_symbol") or {},
            current_concurrent_loss_usd=float(exposure.get("current_loss_usd") or 0.0),
            current_loss_by_symbol=exposure.get("current_loss_by_symbol") or {},
        )
        return {
            "available": True,
            **risk,
            "day_reference_quality": (payload.get("daily_drawdown") or {}).get("reference_quality"),
            "existing_exposure": exposure,
        }
    except Exception as exc:
        return {
            "available": False,
            "advisory_only": True,
            "profile_id": profile.get("profile_id"),
            "provider": profile.get("provider"),
            "reason": str(exc),
        }


def _attach_utc_deal_times(
    deals: List[Dict[str, Any]],
    server_utc_offset_seconds: Optional[int],
) -> List[Dict[str, Any]]:
    """Keep MT5 server timestamps and attach normalized UTC copies for rule math."""
    if server_utc_offset_seconds is None:
        return [dict(d) for d in deals]
    offset = int(server_utc_offset_seconds)
    out: List[Dict[str, Any]] = []
    for deal in deals:
        item = dict(deal)
        if item.get("time") is not None:
            item["time_utc"] = int(item["time"]) - offset
        if item.get("time_msc") is not None:
            item["time_msc_utc"] = int(item["time_msc"]) - offset * 1000
        out.append(item)
    return out


def _server_utc_offset_from_bridge(raw: Dict[str, Any]) -> Optional[int]:
    """Return broker-server offset from the bridge's same-snapshot clocks."""
    try:
        server_time = int(raw.get("server_time"))
        utc_time = int(raw.get("ts"))
    except (TypeError, ValueError):
        return None
    offset = server_time - utc_time
    # Reject implausible clock deltas rather than poisoning provider-day bucketing.
    if abs(offset) > 15 * 3600:
        return None
    return offset


def _bridge_day_reference_for_profile(
    prop_snapshot: Dict[str, Any],
    profile: Dict[str, Any],
) -> Tuple[Optional[float], str]:
    """Use bridge reset reference only when its semantics match the active profile."""
    rule = profile.get("daily_loss") or {}
    if rule.get("reference") != "max_balance_equity_at_reset":
        return None, "profile_reconstructed"

    expected_reset = rule.get("reset_hour_utc")
    bridge_reset = prop_snapshot.get("day_reset_utc")
    if expected_reset is None or bridge_reset is None:
        return None, "profile_reconstructed"
    try:
        if int(expected_reset) != int(bridge_reset):
            return None, "profile_reconstructed"
        value = float(prop_snapshot.get("day_reference") or 0.0)
    except (TypeError, ValueError):
        return None, "profile_reconstructed"
    if value <= 0:
        return None, "profile_reconstructed"

    captured = bool(prop_snapshot.get("day_reference_captured", False))
    return value, ("bridge_captured_reset" if captured else "bridge_estimated")


def _validate_preview_body(body: Dict[str, Any]) -> Optional[str]:
    symbol = str(body.get("symbol") or "").strip()
    side = str(body.get("side") or "").upper()
    if not symbol:
        return "symbol is required"
    if side not in ("BUY", "SELL"):
        return "side must be BUY or SELL"
    try:
        entry = float(body.get("entry"))
        stop = float(body.get("stop_loss"))
        volume = float(body.get("volume"))
    except (TypeError, ValueError):
        return "entry, stop_loss and volume must be numeric"
    if entry <= 0 or stop <= 0 or volume <= 0:
        return "entry, stop_loss and volume must be positive"
    if side == "BUY" and stop >= entry:
        return "BUY stop_loss must be below entry"
    if side == "SELL" and stop <= entry:
        return "SELL stop_loss must be above entry"
    return None


def get_live_payload() -> Dict[str, Any]:
    cfg = load_config()
    profile = active_profile(cfg)
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
            raw_deals = deals_from_bridge(raw)
            positions = positions_from_bridge(raw)
            term_meta = {
                "id": (term or {}).get("id") or "ea_bridge",
                "label": (term or {}).get("label") or "EA Bridge",
                "path": (term or {}).get("path") or raw.get("_bridge_path"),
                "active": True,
            }
            prop_snapshot = raw.get("prop") or {}
            bridge_day_ref, bridge_day_quality = _bridge_day_reference_for_profile(
                prop_snapshot, profile
            )
            server_utc_offset_seconds = _server_utc_offset_from_bridge(raw)
            deals = _attach_utc_deal_times(raw_deals, server_utc_offset_seconds)
            payload = build_prop_payload(
                account=account,
                deal_list=deals,
                open_positions=positions,
                profile=profile,
                now=datetime.now(timezone.utc),
                terminal=term_meta,
                day_start_reference=bridge_day_ref,
                day_reference_quality=bridge_day_quality,
                server_utc_offset_seconds=server_utc_offset_seconds,
                deal_times_are_utc=(server_utc_offset_seconds is not None),
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
                "Provider rules/portal remain the source of truth for pass/fail."
            )
            # Bridge-first polling stays non-invasive: do not attach MT5 just to calculate exposure.
            # If no positions are open, risk is still fully calculable; otherwise preview can attach on demand.
            payload["risk_guard"] = _risk_guard_payload(
                payload=payload,
                profile=profile,
                positions=positions,
                calculator=None,
                personal_policy=cfg.get("personal_risk_policy"),
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

    configured_server_offset = term.get("server_utc_offset_seconds")
    normalized_deals = _attach_utc_deal_times(
        deals or [],
        configured_server_offset,
    )
    payload = build_prop_payload(
        account=account,
        deal_list=normalized_deals,
        open_positions=positions or [],
        profile=profile,
        now=datetime.now(timezone.utc),
        terminal={
            "id": term.get("id"),
            "label": term.get("label"),
            "path": path,
            "active": True,
        },
        server_utc_offset_seconds=configured_server_offset,
        deal_times_are_utc=(configured_server_offset is not None),
    )
    payload["ok"] = True
    payload["terminals"] = list_terminals(cfg)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["data_source"] = "mt5_api"
    payload["bridge_error"] = bridge_err
    payload["disclaimer"] = (
        "Calculations are approximate from MT5 Python API history. "
        "For stabler reads attach MonetaDashboardBridge.mq5. "
        "Provider rules/portal remain the source of truth for pass/fail."
    )
    payload["risk_guard"] = _risk_guard_payload(
        payload=payload,
        profile=profile,
        positions=positions or [],
        calculator=_mt5_profit_at_close,
        personal_policy=cfg.get("personal_risk_policy"),
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


@app.route("/api/profiles")
def api_profiles():
    cfg = load_config()
    return jsonify({
        "active_profile_id": active_profile(cfg).get("profile_id"),
        "profiles": list_profiles(),
    })


@app.route("/api/profiles/select", methods=["POST"])
def api_profiles_select():
    body = request.get_json(silent=True) or {}
    profile_id = str(body.get("profile_id") or "").strip()
    if not profile_id:
        return jsonify({"ok": False, "error": "profile_id required"}), 400
    try:
        get_profile(profile_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    cfg = load_config()
    cfg["profile_id"] = profile_id
    cfg.pop("prop_profile", None)
    save_config(cfg)
    _live_cache["key"] = None
    _live_cache["payload"] = None
    return jsonify({"ok": True, "profile": get_profile(profile_id)})


@app.route("/api/risk/preview", methods=["POST"])
def api_risk_preview():
    """Advisory-only preview. Never sends, modifies, or blocks an order."""
    body = request.get_json(silent=True) or {}
    validation_error = _validate_preview_body(body)
    if validation_error:
        return jsonify({"ok": False, "error": validation_error}), 400

    live = get_live_payload()
    if not live.get("ok"):
        return jsonify({"ok": False, "error": live.get("error") or "live account unavailable"}), 503

    cfg = load_config()
    profile = active_profile(cfg)
    active_id = cfg.get("active_id")
    term = get_terminal_by_id(cfg, active_id) if active_id else None
    if term is None:
        return jsonify({"ok": False, "error": "no active MT5 terminal configured"}), 503
    path = str(term.get("path") or "")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": f"terminal executable not found: {path}"}), 503
    if not ensure_mt5(path):
        return jsonify({"ok": False, "error": _state.get("last_error") or "MT5 calculation unavailable"}), 503

    try:
        # Risk preview is a deliberate user action, so refresh account/positions from
        # the already-running terminal instead of relying on the bridge poll cache.
        acc = mt5.account_info()
        if acc is None:
            raise RuntimeError(f"MT5 account_info unavailable: {mt5.last_error()}")
        fresh_account = {
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
            "margin_level": float(acc.margin_level) if float(acc.margin) > 0 else 0.0,
            "leverage": int(acc.leverage),
        }
        fresh_positions, pos_err = fetch_positions()
        if pos_err:
            raise RuntimeError(pos_err)

        symbol_normalizer = lambda s: normalize_symbol(s, profile)
        exposure = aggregate_open_sl_exposure(
            fresh_positions or [],
            _mt5_profit_at_close,
            symbol_normalizer=symbol_normalizer,
        )
        trade = proposed_trade_exposure(body, _mt5_profit_at_close)
        symbol_group = symbol_normalizer(trade["symbol"])
        policy = cfg.get("personal_risk_policy") or {}
        day_reference = float(
            (live.get("daily_drawdown") or {}).get("starting_balance") or 0.0
        )

        preview_profile = copy.deepcopy(profile)
        if not bool((live.get("daily_drawdown") or {}).get("available", True)):
            preview_profile["daily_loss_limit_usd"] = 0.0

        base_risk = evaluate_risk(
            account=fresh_account,
            profile=preview_profile,
            open_sl_risk_usd=float(exposure["known_risk_usd"]),
            proposed_trade_risk_usd=0.0,
            unbounded_positions=int(exposure["unbounded_positions"]),
            personal_policy=policy,
            day_start_reference=day_reference,
            open_concurrent_risk_usd=float(exposure.get("entry_risk_usd") or 0.0),
            open_risk_by_symbol=exposure.get("risk_by_symbol") or {},
            current_concurrent_loss_usd=float(exposure.get("current_loss_usd") or 0.0),
            current_loss_by_symbol=exposure.get("current_loss_by_symbol") or {},
            proposed_symbol=symbol_group,
            proposed_symbol_risk_usd=0.0,
        )
        risk = evaluate_risk(
            account=fresh_account,
            profile=preview_profile,
            open_sl_risk_usd=float(exposure["known_risk_usd"]),
            proposed_trade_risk_usd=float(trade["risk_usd"]),
            unbounded_positions=int(exposure["unbounded_positions"]),
            personal_policy=policy,
            day_start_reference=day_reference,
            open_concurrent_risk_usd=float(exposure.get("entry_risk_usd") or 0.0),
            open_risk_by_symbol=exposure.get("risk_by_symbol") or {},
            current_concurrent_loss_usd=float(exposure.get("current_loss_usd") or 0.0),
            current_loss_by_symbol=exposure.get("current_loss_by_symbol") or {},
            proposed_symbol=symbol_group,
            proposed_symbol_risk_usd=float(trade["risk_usd"]),
        )

        symbol_info = mt5.symbol_info(str(trade["symbol"]))
        execution_buffer_pct = max(
            0.0, min(50.0, float(policy.get("execution_buffer_pct") or 0.0))
        )
        sizing = {
            "available": False,
            "max_safe_volume": None,
            "binding_constraint": base_risk.get("binding_constraint"),
            "execution_buffer_pct": execution_buffer_pct,
        }
        if symbol_info is not None:
            risk_per_lot = abs(
                _mt5_profit_at_close(
                    trade["symbol"],
                    trade["side"],
                    1.0,
                    float(trade["entry"]),
                    float(trade["stop_loss"]),
                )
            )
            raw_budget = float(base_risk.get("safe_additional_risk_usd") or 0.0)
            buffered_budget = raw_budget * (1.0 - execution_buffer_pct / 100.0)
            max_safe_volume = max_volume_for_risk(
                risk_budget_usd=buffered_budget,
                risk_per_lot_usd=risk_per_lot,
                volume_min=float(symbol_info.volume_min),
                volume_max=float(symbol_info.volume_max),
                volume_step=float(symbol_info.volume_step),
            )
            sizing = {
                "available": True,
                "risk_budget_usd": round(buffered_budget, 2),
                "raw_risk_budget_usd": round(raw_budget, 2),
                "risk_per_lot_usd": round(risk_per_lot, 2),
                "max_safe_volume": max_safe_volume,
                "volume_min": float(symbol_info.volume_min),
                "volume_max": float(symbol_info.volume_max),
                "volume_step": float(symbol_info.volume_step),
                "binding_constraint": base_risk.get("binding_constraint"),
                "execution_buffer_pct": execution_buffer_pct,
            }
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 503

    return jsonify({
        "ok": True,
        "advisory_only": True,
        "trade": trade,
        "existing_exposure": exposure,
        "sizing": sizing,
        "risk": {
            **risk,
            "day_reference_quality": (live.get("daily_drawdown") or {}).get("reference_quality"),
        },
    })


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
