"""Read MonetaDashboardBridge EA JSON from MT5 Common\\Files (no MT5 Python API)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


DEFAULT_BRIDGE_NAME = "moneta_bridge.json"
DEFAULT_MAX_AGE_SEC = 45


def common_files_dir() -> str:
    appdata = os.environ.get("APPDATA") or ""
    return os.path.join(appdata, "MetaQuotes", "Terminal", "Common", "Files")


def bridge_path(filename: str = DEFAULT_BRIDGE_NAME) -> str:
    return os.path.join(common_files_dir(), filename)


def load_bridge(
    filename: str = DEFAULT_BRIDGE_NAME,
    max_age_sec: int = DEFAULT_MAX_AGE_SEC,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return (payload, error). payload None = not usable."""
    path = bridge_path(filename)
    if not os.path.isfile(path):
        return None, f"bridge file missing: {path}"

    try:
        age = time.time() - os.path.getmtime(path)
    except OSError as exc:
        return None, f"bridge mtime failed: {exc}"

    if age > max_age_sec:
        return None, f"bridge stale ({age:.0f}s > {max_age_sec}s): {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
    except Exception as exc:
        return None, f"bridge parse failed: {exc}"

    if not isinstance(data, dict) or not data.get("account"):
        return None, "bridge JSON missing account block"

    data["_bridge_path"] = path
    data["_bridge_age_sec"] = round(age, 1)
    data["_bridge_mtime"] = datetime.fromtimestamp(os.path.getmtime(path)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return data, None


def account_from_bridge(data: Dict[str, Any]) -> Dict[str, Any]:
    acc = data.get("account") or {}
    return {
        "login": int(acc.get("login") or 0),
        "name": str(acc.get("name") or ""),
        "server": str(acc.get("server") or ""),
        "company": str(acc.get("company") or ""),
        "currency": str(acc.get("currency") or ""),
        "balance": float(acc.get("balance") or 0.0),
        "equity": float(acc.get("equity") or 0.0),
        "profit": float(acc.get("profit") or 0.0),
        "margin": float(acc.get("margin") or 0.0),
        "margin_free": float(acc.get("margin_free") or 0.0),
        "margin_level": float(acc.get("margin_level") or 0.0),
        "leverage": int(acc.get("leverage") or 0),
    }


def deals_from_bridge(data: Dict[str, Any]) -> list:
    out = []
    for d in data.get("deals") or []:
        if not isinstance(d, dict):
            continue
        out.append({
            "ticket": int(d.get("ticket") or 0),
            "order": int(d.get("order") or 0),
            "position_id": int(d.get("position_id") or 0),
            "time": int(d.get("time") or 0),
            "time_msc": int(d.get("time_msc") or 0),
            "type": int(d.get("type") or 0),
            "entry": int(d.get("entry") or 0),
            "symbol": str(d.get("symbol") or ""),
            "volume": float(d.get("volume") or 0.0),
            "price": float(d.get("price") or 0.0),
            "profit": float(d.get("profit") or 0.0),
            "swap": float(d.get("swap") or 0.0),
            "commission": float(d.get("commission") or 0.0),
            "comment": str(d.get("comment") or ""),
        })
    return out


def positions_from_bridge(data: Dict[str, Any]) -> list:
    out = []
    for p in data.get("positions") or []:
        if not isinstance(p, dict):
            continue
        out.append({
            "ticket": int(p.get("ticket") or 0),
            "symbol": str(p.get("symbol") or ""),
            "type": str(p.get("type") or "BUY"),
            "volume": float(p.get("volume") or 0.0),
            "price_open": float(p.get("price_open") or 0.0),
            "price_current": float(p.get("price_current") or 0.0),
            "sl": float(p.get("sl") or 0.0),
            "tp": float(p.get("tp") or 0.0),
            "profit": float(p.get("profit") or 0.0),
            "duration_sec": int(p.get("duration_sec") or 0),
            "comment": str(p.get("comment") or ""),
        })
    return out
