"""
OLC Strategy Tester run orchestration.

Generates MT5 tester .ini from existing presets, optionally launches
terminal64.exe /config: when that terminal is NOT already running
(protects live sessions), otherwise returns a manual command and watches
for the HTML report.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tester_report import parse_tester_report

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TESTER_DIR = os.path.join(ROOT, "tester")
RESULTS_DIR = os.path.join(HERE, "tester_results")
RUNS_DIR = os.path.join(HERE, "tester_runs")
RESULTS_INDEX = os.path.join(RESULTS_DIR, "index.json")
HISTORY_META = os.path.join(ROOT, "History", "meta.json")
TERMINALS_FILE = os.path.join(HERE, "terminals.json")

# Presets reuse existing .set / smoke .ini patterns
PRESETS: List[Dict[str, Any]] = [
    {
        "id": "ny_orb_gold",
        "label": "NY ORB — XAUUSD",
        "symbol": "XAUUSD",
        "strategy": "NY ORB",
        "set_file": "olc_ny_orb_gold.set",
        "template_ini": "olc_smoke_gold.ini",
    },
    {
        "id": "asian_eurusd",
        "label": "Asian Sweep — EURUSD",
        "symbol": "EURUSD",
        "strategy": "Asian Sweep",
        "set_file": "olc_asian_eurusd.set",
        "template_ini": "olc_smoke_eurusd.ini",
    },
    {
        "id": "asian_gbpusd",
        "label": "Asian Sweep — GBPUSD",
        "symbol": "GBPUSD",
        "strategy": "Asian Sweep",
        "set_file": "olc_asian_gbpusd.set",
        "template_ini": "olc_smoke_eurusd.ini",
    },
    {
        "id": "asian_eurcad",
        "label": "Asian Sweep — EURCAD",
        "symbol": "EURCAD",
        "strategy": "Asian Sweep",
        "set_file": "olc_asian_eurusd.set",
        "template_ini": "olc_smoke_eurusd.ini",
        "note": "Uses Asian EURUSD .set parameters; symbol overridden to EURCAD.",
    },
]

_lock = threading.RLock()
_runs: Dict[str, Dict[str, Any]] = {}
_watcher_started = False


def _ensure_dirs() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(RUNS_DIR, exist_ok=True)


def load_history_status() -> Dict[str, Any]:
    """Summarize local History parquet cache for the Optimizer UI."""
    if not os.path.isfile(HISTORY_META):
        return {
            "ok": False,
            "error": "History/meta.json missing — run tools/sync_history.py",
            "symbols": [],
        }
    with open(HISTORY_META, "r", encoding="utf-8") as f:
        meta = json.load(f)
    symbols_out = []
    series = meta.get("series") or {}
    for sym in meta.get("symbols") or list(series.keys()):
        m5 = (series.get(sym) or {}).get("M5") or {}
        h1 = (series.get(sym) or {}).get("H1") or {}
        symbols_out.append({
            "symbol": sym,
            "m5_bars": m5.get("bars"),
            "h1_bars": h1.get("bars"),
            "from": m5.get("from") or h1.get("from"),
            "to": m5.get("to") or h1.get("to"),
            "ok": bool(m5.get("ok") and h1.get("ok")),
            "gap_count": m5.get("gap_count", 0),
        })
    return {
        "ok": True,
        "synced_at": meta.get("synced_at"),
        "window_days": meta.get("window_days"),
        "terminal": meta.get("terminal"),
        "symbols": symbols_out,
        "default_from": _iso_to_mt5_date(
            next((s["from"] for s in symbols_out if s.get("from")), None)
        ),
        "default_to": _iso_to_mt5_date(
            next((s["to"] for s in symbols_out if s.get("to")), None)
        ),
    }


def _iso_to_mt5_date(iso: Optional[str]) -> Optional[str]:
    if not iso:
        return None
    try:
        # 2025-12-07T23:00:00+00:00 → 2025.12.07
        d = iso[:10]
        y, m, day = d.split("-")
        return f"{y}.{m}.{day}"
    except Exception:
        return None


def _default_params_for_symbol(symbol: str) -> Dict[str, float]:
    """UI defaults for Risk / TP / SL / Gap (price units)."""
    sym = (symbol or "").upper()
    if "XAU" in sym or "GOLD" in sym:
        return {"risk_pct": 0.30, "profit_x": 10.0, "sl_gap": 7.0, "level_gap": 5.0}
    return {"risk_pct": 0.50, "profit_x": 0.002, "sl_gap": 0.002, "level_gap": 0.001}


def list_presets() -> List[Dict[str, Any]]:
    out = []
    for p in PRESETS:
        set_path = os.path.join(TESTER_DIR, p["set_file"])
        item = {
            "id": p["id"],
            "label": p["label"],
            "symbol": p["symbol"],
            "strategy": p["strategy"],
            "set_file": p["set_file"],
            "set_exists": os.path.isfile(set_path),
            "defaults": _default_params_for_symbol(p["symbol"]),
        }
        if p.get("note"):
            item["note"] = p["note"]
        out.append(item)
    return out


def get_preset(preset_id: str) -> Optional[Dict[str, Any]]:
    for p in PRESETS:
        if p["id"] == preset_id:
            return p
    return None


def resolve_preset(symbol: str, strategy: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pick preset by symbol (+ optional strategy label/id fragment)."""
    sym = (symbol or "").upper().strip()
    strat = (strategy or "").strip().lower()
    candidates = [p for p in PRESETS if p["symbol"].upper() == sym]
    if not candidates:
        return None
    if strat:
        for p in candidates:
            if strat in p["id"].lower() or strat in p["strategy"].lower():
                return p
        # NY ORB vs Asian heuristics
        if "ny" in strat or "orb" in strat:
            for p in candidates:
                if "ny" in p["id"] or "ORB" in p["strategy"]:
                    return p
        if "asian" in strat:
            for p in candidates:
                if "asian" in p["id"]:
                    return p
    return candidates[0]


def _set_kv_lines(text: str, updates: Dict[str, Any]) -> str:
    """Update or append key=value lines in an MT5 .set file (comments preserved)."""
    lines = text.splitlines()
    done = set()
    out: List[str] = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith(";") or "=" not in raw:
            out.append(line)
            continue
        key, _, _val = raw.partition("=")
        key = key.strip()
        if key in updates:
            val = updates[key]
            if isinstance(val, bool):
                out.append(f"{key}={'true' if val else 'false'}")
            elif isinstance(val, float):
                # Keep compact floats (avoid 0.0020000000001)
                out.append(f"{key}={val:g}")
            else:
                out.append(f"{key}={val}")
            done.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key in done:
            continue
        if isinstance(val, float):
            out.append(f"{key}={val:g}")
        else:
            out.append(f"{key}={val}")
    return "\n".join(out) + "\n"


def apply_set_param_overrides(
    set_path: str,
    symbol: str,
    *,
    risk_pct: Optional[float] = None,
    profit_x: Optional[float] = None,
    sl_gap: Optional[float] = None,
    level_gap: Optional[float] = None,
    strategy_mode: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Patch a copied .set so Risk/TP/SL/Gap match UI values for the symbol preset.
    Returns the dict of keys written (for result table metadata).
    """
    sym = (symbol or "").upper()
    with open(set_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    updates: Dict[str, Any] = {}
    meta = {
        "risk_pct": risk_pct,
        "profit_x": profit_x,
        "sl_gap": sl_gap,
        "level_gap": level_gap,
    }

    if strategy_mode is not None:
        updates["strategy_mode"] = int(strategy_mode)

    if "XAU" in sym or "GOLD" in sym:
        updates["symbol_preset"] = 1  # PRESET_GOLD
        if risk_pct is not None:
            updates["gold_risk_percent"] = float(risk_pct)
            updates["default_risk_percent"] = float(risk_pct)
        if profit_x is not None:
            updates["gold_profit_x"] = float(profit_x)
            updates["default_profit_x"] = float(profit_x)
        if sl_gap is not None:
            updates["gold_sl_gap"] = float(sl_gap)
            updates["default_sl_gap"] = float(sl_gap)
        if level_gap is not None:
            updates["gold_level_gap"] = float(level_gap)
            updates["default_level_gap"] = float(level_gap)
    elif sym == "EURUSD":
        updates["symbol_preset"] = 2
        if risk_pct is not None:
            updates["eur_risk_percent"] = float(risk_pct)
            updates["default_risk_percent"] = float(risk_pct)
        if profit_x is not None:
            updates["eur_profit_x"] = float(profit_x)
            updates["default_profit_x"] = float(profit_x)
        if sl_gap is not None:
            updates["eur_sl_gap"] = float(sl_gap)
            updates["default_sl_gap"] = float(sl_gap)
        if level_gap is not None:
            updates["eur_level_gap"] = float(level_gap)
            updates["default_level_gap"] = float(level_gap)
    elif sym == "GBPUSD":
        updates["symbol_preset"] = 3
        if risk_pct is not None:
            updates["gbp_risk_percent"] = float(risk_pct)
            updates["default_risk_percent"] = float(risk_pct)
        if profit_x is not None:
            updates["gbp_profit_x"] = float(profit_x)
            updates["default_profit_x"] = float(profit_x)
        if sl_gap is not None:
            updates["gbp_sl_gap"] = float(sl_gap)
            updates["default_sl_gap"] = float(sl_gap)
        if level_gap is not None:
            updates["gbp_level_gap"] = float(level_gap)
            updates["default_level_gap"] = float(level_gap)
    else:
        # EURCAD and others → CUSTOM basket defaults
        updates["symbol_preset"] = 4
        if risk_pct is not None:
            updates["default_risk_percent"] = float(risk_pct)
        if profit_x is not None:
            updates["default_profit_x"] = float(profit_x)
        if sl_gap is not None:
            updates["default_sl_gap"] = float(sl_gap)
        if level_gap is not None:
            updates["default_level_gap"] = float(level_gap)

    new_text = _set_kv_lines(text, updates)
    with open(set_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new_text)
    meta["set_keys_written"] = sorted(updates.keys())
    return meta


def _active_terminal() -> Dict[str, Any]:
    fallback = {
        "id": "moneta_funded",
        "label": "Moneta Funded",
        "path": r"C:\Program Files\Moneta Funded MT5 Terminal\terminal64.exe",
    }
    try:
        with open(TERMINALS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        active = cfg.get("active_id")
        for t in cfg.get("terminals", []):
            if t.get("id") == active:
                return t
        if cfg.get("terminals"):
            return cfg["terminals"][0]
    except Exception:
        pass
    return fallback


def _is_terminal_running(exe_path: str) -> bool:
    """Read-only check — never starts/stops MetaTrader."""
    target = os.path.normcase(os.path.abspath(exe_path))
    create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
    except Exception:
        pass
    return False


def _mt5_date(s: str) -> str:
    s = (s or "").strip().replace("-", ".")
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}.{int(mo):02d}.{int(d):02d}"
    return s


def generate_ini(
    preset: Dict[str, Any],
    from_date: str,
    to_date: str,
    report_abs: str,
    set_abs: str,
    deposit: float = 50000.0,
) -> str:
    """Build a [Tester] ini matching existing olc_smoke_*.ini pattern."""
    report_stem = os.path.splitext(report_abs)[0]
    # Prefer absolute report path without extension (MT5 appends .htm)
    lines = [
        "[Tester]",
        "Expert=OrderLevelControl_Prop_EA",
        f"ExpertParameters={set_abs}",
        f"Symbol={preset['symbol']}",
        "Period=M5",
        "Model=1",
        "Optimization=0",
        f"FromDate={_mt5_date(from_date)}",
        f"ToDate={_mt5_date(to_date)}",
        "ForwardMode=0",
        f"Deposit={int(deposit)}",
        "Currency=USD",
        "Leverage=100",
        "Visual=0",
        f"Report={report_stem}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "",
    ]
    return "\n".join(lines)


def _load_index() -> List[Dict[str, Any]]:
    _ensure_dirs()
    if not os.path.isfile(RESULTS_INDEX):
        return []
    try:
        with open(RESULTS_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("results", [])
    except Exception:
        return []


def _save_index(rows: List[Dict[str, Any]]) -> None:
    _ensure_dirs()
    with open(RESULTS_INDEX, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def list_tester_results() -> Dict[str, Any]:
    """Return results keyed by symbol (optimizer-compatible) + flat list."""
    rows = _load_index()
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sym = r.get("symbol") or "UNKNOWN"
        by_sym.setdefault(sym, []).append(r)
    for sym in by_sym:
        by_sym[sym] = sorted(by_sym[sym], key=lambda x: x.get("score") or 0, reverse=True)
    return {"by_symbol": by_sym, "results": rows, "count": len(rows)}


def _ingest_report(run: Dict[str, Any], report_path: str) -> Dict[str, Any]:
    deposit = float(run.get("deposit") or 50000)
    row = parse_tester_report(report_path, deposit=deposit)
    row["run_id"] = run["id"]
    row["preset_id"] = run.get("preset_id")
    row["preset_label"] = run.get("preset_label")
    row["strategy"] = run.get("strategy") or row.get("strategy")
    row["symbol"] = run.get("symbol") or row.get("symbol")
    # Prefer UI-chosen params (report HTML often omits input values)
    if run.get("risk_pct") is not None:
        row["risk_pct"] = run["risk_pct"]
    if run.get("profit_x") is not None:
        row["profit_x"] = run["profit_x"]
    if run.get("sl_gap") is not None:
        row["sl_gap"] = run["sl_gap"]
    if run.get("level_gap") is not None:
        row["level_gap"] = run["level_gap"]
    row["from_date"] = run.get("from_date")
    row["to_date"] = run.get("to_date")
    row["finished_at"] = datetime.now(timezone.utc).isoformat()
    row["launch_mode"] = run.get("launch_mode")

    # Persist JSON next to report
    json_path = os.path.splitext(report_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(row, f, indent=2)
    row["json_path"] = json_path

    with _lock:
        idx = _load_index()
        idx = [r for r in idx if r.get("run_id") != run["id"]]
        idx.insert(0, row)
        _save_index(idx[:100])
        run["status"] = "completed"
        run["result"] = row
        run["error"] = None
    return row


def _poll_run(run_id: str, timeout_sec: int = 7200, interval: float = 3.0) -> None:
    deadline = time.time() + timeout_sec
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return
        report_path = run["report_path"]
        candidates = [
            report_path,
            report_path + ".htm",
            report_path + ".html",
            os.path.splitext(report_path)[0] + ".htm",
            os.path.splitext(report_path)[0] + ".html",
        ]
        # Also watch project tester/ for relative Report= names
        stem = os.path.basename(os.path.splitext(report_path)[0])
        candidates.extend([
            os.path.join(TESTER_DIR, stem + ".htm"),
            os.path.join(TESTER_DIR, stem + ".html"),
            os.path.join(ROOT, stem + ".htm"),
            os.path.join(ROOT, stem + ".html"),
        ])

    while time.time() < deadline:
        with _lock:
            run = _runs.get(run_id)
            if not run or run.get("status") in ("completed", "failed", "cancelled"):
                return
            run["status"] = "waiting_report"
            run["updated_at"] = datetime.now(timezone.utc).isoformat()

        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 500:
                # Wait briefly for MT5 to finish writing
                time.sleep(1.0)
                try:
                    # Copy into canonical results path if needed
                    with _lock:
                        run = _runs.get(run_id)
                        if not run:
                            return
                        dest = run["report_path"]
                        if not dest.endswith((".htm", ".html")):
                            dest = dest + ".htm"
                    if os.path.normcase(os.path.abspath(c)) != os.path.normcase(
                        os.path.abspath(dest)
                    ):
                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                        with open(c, "rb") as src, open(dest, "wb") as dst:
                            dst.write(src.read())
                        c = dest
                    _ingest_report(run, c)
                    return
                except Exception as e:
                    with _lock:
                        run = _runs.get(run_id)
                        if run:
                            run["status"] = "failed"
                            run["error"] = f"parse failed: {e}"
                    return
        time.sleep(interval)

    with _lock:
        run = _runs.get(run_id)
        if run and run.get("status") not in ("completed", "failed"):
            run["status"] = "timeout"
            run["error"] = (
                "Report not found before timeout. "
                "Confirm Strategy Tester finished and Report path is writable."
            )


def start_tester_run(
    preset_id: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    launch: bool = False,
    deposit: float = 50000.0,
    terminal_id: Optional[str] = None,
    symbol: Optional[str] = None,
    strategy: Optional[str] = None,
    risk_pct: Optional[float] = None,
    profit_x: Optional[float] = None,
    sl_gap: Optional[float] = None,
    level_gap: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Create ini + start watch. If launch=True and selected terminal is NOT
    already running, spawn terminal64 /config:ini (headless tester).
    If terminal IS running, never kill it — fall back to manual command.

    Optional risk_pct / profit_x / sl_gap / level_gap patch the copied .set
    so the UI can tune params without editing files by hand.
    """
    _ensure_dirs()
    preset = get_preset(preset_id) if preset_id else None
    if preset is None and symbol:
        preset = resolve_preset(symbol, strategy)
    if not preset:
        return {"ok": False, "error": f"unknown preset: {preset_id or symbol}"}

    # Allow overriding symbol on a base preset (e.g. EURCAD via asian set)
    if symbol:
        preset = {**preset, "symbol": symbol.upper().strip()}

    set_src = os.path.join(TESTER_DIR, preset["set_file"])
    if not os.path.isfile(set_src):
        return {"ok": False, "error": f"missing set file: {set_src}"}

    hist = load_history_status()
    from_date = from_date or hist.get("default_from") or "2025.12.07"
    to_date = to_date or hist.get("default_to") or "2026.08.04"

    term = _active_terminal()
    if terminal_id:
        try:
            with open(TERMINALS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for t in cfg.get("terminals", []):
                if t.get("id") == terminal_id:
                    term = t
                    break
        except Exception:
            pass

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    run_dir = os.path.join(RUNS_DIR, run_id)
    os.makedirs(run_dir, exist_ok=True)

    set_abs = os.path.abspath(os.path.join(run_dir, preset["set_file"]))
    with open(set_src, "rb") as src, open(set_abs, "wb") as dst:
        dst.write(src.read())

    # Strategy mode: 0 = NY ORB, 1 = Asian Sweep
    strat_label = preset.get("strategy") or ""
    if strategy:
        strat_label = strategy
    strategy_mode = 0 if ("ORB" in strat_label.upper() or "NY" in strat_label.upper()) else 1
    if "asian" in (strategy or "").lower():
        strategy_mode = 1
        strat_label = "Asian Sweep"
    if "orb" in (strategy or "").lower() or (strategy or "").lower() == "ny":
        strategy_mode = 0
        strat_label = "NY ORB"

    defaults = _default_params_for_symbol(preset["symbol"])
    risk_v = float(risk_pct) if risk_pct is not None else defaults["risk_pct"]
    tp_v = float(profit_x) if profit_x is not None else defaults["profit_x"]
    sl_v = float(sl_gap) if sl_gap is not None else defaults["sl_gap"]
    gap_v = float(level_gap) if level_gap is not None else defaults["level_gap"]
    param_meta = apply_set_param_overrides(
        set_abs,
        preset["symbol"],
        risk_pct=risk_v,
        profit_x=tp_v,
        sl_gap=sl_v,
        level_gap=gap_v,
        strategy_mode=strategy_mode,
    )

    report_stem = os.path.abspath(os.path.join(RESULTS_DIR, run_id))
    report_path = report_stem + ".htm"
    ini_path = os.path.abspath(os.path.join(run_dir, "tester.ini"))
    ini_body = generate_ini(preset, from_date, to_date, report_stem, set_abs, deposit)
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(ini_body)

    exe = term.get("path") or ""
    running = bool(exe and os.path.isfile(exe) and _is_terminal_running(exe))
    launch_mode = "watch"
    launch_error = None
    manual_cmd = f'"{exe}" /config:"{ini_path}"'

    if launch:
        if not exe or not os.path.isfile(exe):
            launch_error = f"Terminal exe missing: {exe}"
            launch_mode = "watch"
        elif running:
            # Protect live session — do not start second /config on same path
            launch_error = (
                "Selected terminal is already running (live). "
                "Dashboard will not launch /config against it. "
                "Use a second terminal (e.g. Moneta2) closed, or run the "
                "manual command below / open Strategy Tester yourself."
            )
            launch_mode = "watch"
        else:
            try:
                create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    [exe, f"/config:{ini_path}"],
                    cwd=os.path.dirname(exe),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=create_no_window,
                )
                launch_mode = "headless_config"
            except Exception as e:
                launch_error = f"launch failed: {e}"
                launch_mode = "watch"

    run = {
        "id": run_id,
        "ok": True,
        "status": "started" if launch_mode == "headless_config" else "awaiting_manual",
        "preset_id": preset["id"],
        "preset_label": f"{strat_label} — {preset['symbol']}",
        "symbol": preset["symbol"],
        "strategy": strat_label,
        "risk_pct": risk_v,
        "profit_x": tp_v,
        "sl_gap": sl_v,
        "level_gap": gap_v,
        "params": param_meta,
        "from_date": _mt5_date(from_date),
        "to_date": _mt5_date(to_date),
        "deposit": deposit,
        "ini_path": ini_path,
        "set_path": set_abs,
        "report_path": report_path,
        "report_stem": report_stem,
        "terminal_id": term.get("id"),
        "terminal_path": exe,
        "terminal_was_running": running,
        "launch_mode": launch_mode,
        "launch_error": launch_error,
        "manual_cmd": manual_cmd,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
        "note": (
            "Real EA backtest needs MT5 Strategy Tester (not History parquet). "
            "Ensure OrderLevelControl_Prop_EA.ex5 is installed in that terminal's MQL5/Experts."
        ),
    }
    with _lock:
        _runs[run_id] = run

    t = threading.Thread(target=_poll_run, args=(run_id,), daemon=True)
    t.start()
    return dict(run)


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        r = _runs.get(run_id)
        return dict(r) if r else None


def import_existing_report(path: str, deposit: float = 50000.0) -> Dict[str, Any]:
    """Parse a report file on disk into tester_results (smoke / manual import)."""
    _ensure_dirs()
    abs_path = os.path.abspath(path)
    row = parse_tester_report(abs_path, deposit=deposit)
    row["run_id"] = "import_" + uuid.uuid4().hex[:8]
    row["finished_at"] = datetime.now(timezone.utc).isoformat()
    row["launch_mode"] = "import"
    dest = os.path.join(RESULTS_DIR, row["run_id"] + ".htm")
    if os.path.normcase(abs_path) != os.path.normcase(dest):
        with open(abs_path, "rb") as src, open(dest, "wb") as dst:
            dst.write(src.read())
        row["report_path"] = dest
    json_path = os.path.splitext(dest)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(row, f, indent=2)
    row["json_path"] = json_path
    idx = _load_index()
    idx.insert(0, row)
    _save_index(idx[:100])
    return row
