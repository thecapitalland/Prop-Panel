#!/usr/bin/env python3
"""Sync MT5 OHLC bars into local History parquet cache.

Attach-only: reads active terminal from dashboard/terminals.json.
Never starts or stops MetaTrader 5.

Usage:
  python tools/sync_history.py           # full ~240-day sync
  python tools/sync_history.py --update  # incremental from last cached bar
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import MetaTrader5 as mt5
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_DASHBOARD = os.path.join(_ROOT, "dashboard")
_TERMINALS_FILE = os.path.join(_DASHBOARD, "terminals.json")
_HISTORY_DIR = os.path.join(_ROOT, "History")
_META_FILE = os.path.join(_HISTORY_DIR, "meta.json")

SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD", "EURCAD"]
TIMEFRAMES = {
    "M5": mt5.TIMEFRAME_M5,
    "H1": mt5.TIMEFRAME_H1,
}
# Expected bar spacing (seconds) used for gap detection
TF_SECONDS = {"M5": 300, "H1": 3600}
DEFAULT_DAYS = 240
# Gaps larger than this multiple of TF period are flagged (weekends ignored via absolute gap)
GAP_MULTIPLIER = 3
# Weekend-friendly absolute gap thresholds (seconds).
# FX weekend close→open is ~48h; use >60h / >84h so only real holes flag.
GAP_ABS_SECONDS = {"M5": 60 * 60 * 60, "H1": 60 * 60 * 84}


def _active_terminal() -> dict[str, Any]:
    """Read selected terminal from terminals.json (never launches MT5)."""
    fallback = {
        "id": "moneta_funded",
        "label": "Moneta Funded MT5 Terminal",
        "path": r"C:\Program Files\Moneta Funded MT5 Terminal\terminal64.exe",
    }
    try:
        with open(_TERMINALS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        active = cfg.get("active_id")
        for t in cfg.get("terminals", []):
            if t.get("id") == active:
                return {
                    "id": t.get("id"),
                    "label": t.get("label"),
                    "path": t.get("path") or fallback["path"],
                }
    except Exception as exc:
        print(f"WARN: could not read terminals.json ({exc}); using fallback path")
    return fallback


def _parquet_path(symbol: str, tf: str) -> str:
    return os.path.join(_HISTORY_DIR, symbol, f"{tf}.parquet")


def _ensure_dirs() -> None:
    os.makedirs(_HISTORY_DIR, exist_ok=True)
    for sym in SYMBOLS:
        os.makedirs(os.path.join(_HISTORY_DIR, sym), exist_ok=True)


def _rates_to_df(rates) -> pd.DataFrame:
    df = pd.DataFrame(rates)
    if df.empty:
        return df
    df["time_dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    # Keep stable column order for parquet
    cols = [
        "time",
        "time_dt",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = 0
    return df[cols]


def _detect_gaps(df: pd.DataFrame, tf: str) -> list[dict[str, Any]]:
    """Flag large gaps between consecutive bars (broker depth / missing history)."""
    gaps: list[dict[str, Any]] = []
    if df is None or len(df) < 2:
        return gaps
    times = df["time"].astype("int64").to_numpy()
    threshold = GAP_ABS_SECONDS.get(tf, TF_SECONDS[tf] * GAP_MULTIPLIER)
    for i in range(1, len(times)):
        delta = int(times[i] - times[i - 1])
        if delta > threshold:
            gaps.append(
                {
                    "from": datetime.fromtimestamp(int(times[i - 1]), tz=timezone.utc).isoformat(),
                    "to": datetime.fromtimestamp(int(times[i]), tz=timezone.utc).isoformat(),
                    "gap_seconds": delta,
                    "gap_hours": round(delta / 3600.0, 2),
                }
            )
    return gaps


def _load_existing(symbol: str, tf: str) -> pd.DataFrame | None:
    path = _parquet_path(symbol, tf)
    if not os.path.isfile(path):
        return None
    try:
        df = pd.read_parquet(path)
        if "time_dt" not in df.columns and "time" in df.columns:
            df["time_dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df
    except Exception as exc:
        print(f"WARN: failed reading {path}: {exc}")
        return None


def _save_parquet(symbol: str, tf: str, df: pd.DataFrame) -> str:
    path = _parquet_path(symbol, tf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = df.sort_values("time").drop_duplicates(subset=["time"], keep="last").reset_index(drop=True)
    out.to_parquet(path, index=False)
    return path


def _fetch_range(symbol: str, tf_const: int, from_dt: datetime, to_dt: datetime):
    rates = mt5.copy_rates_range(symbol, tf_const, from_dt, to_dt)
    if rates is None:
        err = mt5.last_error()
        print(f"  ERROR copy_rates_range {symbol}: {err}")
        return None
    return rates


def sync_symbol_tf(
    symbol: str,
    tf: str,
    *,
    update: bool,
    window_days: int,
    now: datetime,
) -> dict[str, Any]:
    tf_const = TIMEFRAMES[tf]
    to_dt = now
    from_dt = now - timedelta(days=window_days)
    existing = _load_existing(symbol, tf) if update else None
    mode = "full"

    if update and existing is not None and not existing.empty:
        last_ts = int(existing["time"].max())
        from_dt = datetime.fromtimestamp(last_ts, tz=timezone.utc) - timedelta(minutes=5)
        mode = "update"

    print(f"  {symbol}/{tf} [{mode}] {from_dt.isoformat()} -> {to_dt.isoformat()}")
    rates = _fetch_range(symbol, tf_const, from_dt, to_dt)
    if rates is None or len(rates) == 0:
        if existing is not None and not existing.empty:
            gaps = _detect_gaps(existing, tf)
            return {
                "symbol": symbol,
                "tf": tf,
                "mode": mode,
                "bars": int(len(existing)),
                "from": existing["time_dt"].iloc[0].isoformat(),
                "to": existing["time_dt"].iloc[-1].isoformat(),
                "path": _parquet_path(symbol, tf),
                "gaps": gaps,
                "gap_count": len(gaps),
                "ok": True,
                "note": "no new bars; kept existing cache",
            }
        return {
            "symbol": symbol,
            "tf": tf,
            "mode": mode,
            "bars": 0,
            "from": None,
            "to": None,
            "path": _parquet_path(symbol, tf),
            "gaps": [],
            "gap_count": 0,
            "ok": False,
            "note": "no rates returned",
        }

    new_df = _rates_to_df(rates)
    if existing is not None and not existing.empty and mode == "update":
        # Normalize time_dt timezone for concat
        if existing["time_dt"].dt.tz is None:
            existing = existing.copy()
            existing["time_dt"] = pd.to_datetime(existing["time_dt"], utc=True)
        merged = pd.concat([existing, new_df], ignore_index=True)
    else:
        merged = new_df

    path = _save_parquet(symbol, tf, merged)
    saved = pd.read_parquet(path)
    if "time_dt" not in saved.columns:
        saved["time_dt"] = pd.to_datetime(saved["time"], unit="s", utc=True)
    elif saved["time_dt"].dt.tz is None:
        saved["time_dt"] = pd.to_datetime(saved["time_dt"], utc=True)

    gaps = _detect_gaps(saved, tf)
    requested_from = (now - timedelta(days=window_days)).replace(tzinfo=timezone.utc)
    first = saved["time_dt"].iloc[0]
    depth_gap = None
    if first > requested_from + timedelta(days=2):
        depth_gap = {
            "type": "insufficient_history",
            "requested_from": requested_from.isoformat(),
            "actual_from": first.isoformat(),
            "missing_days": round((first - requested_from).total_seconds() / 86400.0, 1),
        }

    all_gaps = gaps
    note_parts = []
    if depth_gap:
        all_gaps = [depth_gap] + gaps
        note_parts.append(
            f"broker history starts {depth_gap['missing_days']}d after requested window"
        )

    return {
        "symbol": symbol,
        "tf": tf,
        "mode": mode,
        "bars": int(len(saved)),
        "from": first.isoformat(),
        "to": saved["time_dt"].iloc[-1].isoformat(),
        "path": path,
        "gaps": all_gaps,
        "gap_count": len(all_gaps),
        "ok": True,
        "note": "; ".join(note_parts) if note_parts else None,
    }


def build_meta(terminal: dict[str, Any], account: dict[str, Any] | None, series: list[dict]) -> dict:
    synced_at = datetime.now(timezone.utc).isoformat()
    symbols_meta: dict[str, Any] = {}
    for row in series:
        sym = row["symbol"]
        symbols_meta.setdefault(sym, {})
        symbols_meta[sym][row["tf"]] = {
            "bars": row["bars"],
            "from": row["from"],
            "to": row["to"],
            "path": row["path"],
            "gaps": row["gaps"],
            "gap_count": row["gap_count"],
            "ok": row["ok"],
            "mode": row["mode"],
            "note": row.get("note"),
        }

    return {
        "synced_at": synced_at,
        "window_days": DEFAULT_DAYS,
        "symbols": SYMBOLS,
        "timeframes": list(TIMEFRAMES.keys()),
        "terminal": {
            "id": terminal.get("id"),
            "label": terminal.get("label"),
            "path": terminal.get("path"),
            "login": account.get("login") if account else None,
            "server": account.get("server") if account else None,
            "name": account.get("name") if account else None,
            "company": account.get("company") if account else None,
        },
        "series": symbols_meta,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync MT5 OHLC into History/*.parquet")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Incremental sync from last cached bar (default: full window)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Calendar days for full sync window (default {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Optional subset of symbols",
    )
    args = parser.parse_args(argv)

    symbols = args.symbols or SYMBOLS
    for s in symbols:
        if s not in SYMBOLS:
            print(f"ERROR: unsupported symbol {s}; allowed: {SYMBOLS}")
            return 2

    _ensure_dirs()
    terminal = _active_terminal()
    path = terminal["path"]
    print(f"Attaching MT5 (read-only init): {path}")
    print(f"Mode: {'update' if args.update else 'full'} | days={args.days}")

    if not mt5.initialize(path=path):
        err = mt5.last_error()
        print("FATAL: mt5.initialize failed — terminal must already be open (attach-only).")
        print(f"  last_error={err}")
        print("Retry: open Moneta Funded MT5, then re-run:")
        print("  python tools/sync_history.py")
        print("  python tools/sync_history.py --update")
        return 1

    account = None
    try:
        info = mt5.account_info()
        if info is not None:
            account = {
                "login": info.login,
                "server": info.server,
                "name": info.name,
                "company": info.company,
            }
            print(f"Account: login={info.login} server={info.server}")
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    series: list[dict] = []
    try:
        for symbol in symbols:
            # Ensure symbol is selected in Market Watch for history access
            if not mt5.symbol_select(symbol, True):
                print(f"WARN: symbol_select({symbol}) failed: {mt5.last_error()}")
            for tf in TIMEFRAMES:
                row = sync_symbol_tf(
                    symbol,
                    tf,
                    update=args.update,
                    window_days=args.days,
                    now=now,
                )
                series.append(row)
                status = "OK" if row["ok"] else "FAIL"
                print(
                    f"    -> {status} bars={row['bars']} gaps={row['gap_count']}"
                    + (f" note={row['note']}" if row.get("note") else "")
                )
    finally:
        mt5.shutdown()

    # Merge with previous meta when doing partial symbol sync
    prev = {}
    if os.path.isfile(_META_FILE):
        try:
            with open(_META_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {}

    meta = build_meta(terminal, account, series)
    if prev.get("series") and args.symbols:
        merged_series = dict(prev.get("series") or {})
        for sym, tfs in meta["series"].items():
            merged_series.setdefault(sym, {}).update(tfs)
        meta["series"] = merged_series
        meta["symbols"] = sorted(merged_series.keys())

    with open(_META_FILE, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"\nWrote {_META_FILE}")
    ok_n = sum(1 for r in series if r["ok"])
    fail_n = len(series) - ok_n
    total_gaps = sum(r["gap_count"] for r in series)
    print(f"Summary: {ok_n} ok, {fail_n} fail, total_gap_flags={total_gaps}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
