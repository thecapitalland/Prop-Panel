"""Parse MetaTrader 5 Strategy Tester HTML/HTM reports into JSON rows."""
from __future__ import annotations

import os
import re
from html import unescape
from typing import Any, Dict, List, Optional, Tuple


def _strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s)
    return unescape(s).replace("\xa0", " ").strip()


def _bold_cells(html: str) -> List[str]:
    return [_strip_tags(m) for m in re.findall(r"<b>(.*?)</b>", html, flags=re.I | re.S)]


def _label_value_map(html: str) -> Dict[str, str]:
    """Map 'Label:' → first bold value in the same table row (MT5 report layout)."""
    out: Dict[str, str] = {}
    for m in re.finditer(
        r"<tr[^>]*>(.*?)</tr>", html, flags=re.I | re.S
    ):
        row = m.group(1)
        labels = re.findall(
            r">\s*([^<:][^<:]{0,80}?):\s*<", row, flags=re.I
        )
        values = _bold_cells(row)
        if not labels or not values:
            continue
        # Pair labels with values left-to-right (MT5 packs 1–3 metrics per row)
        for i, lab in enumerate(labels):
            key = lab.strip()
            if i < len(values) and key and key not in out:
                out[key] = values[i].strip()
    return out


def _parse_pct_pair(s: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse '275.73 (0.55%)' or '0.55% (275.73)' → (abs, pct)."""
    if not s:
        return None, None
    pct_m = re.search(r"([\d.,]+)\s*%", s)
    num_m = re.search(r"([\d.,]+)", s.replace(",", ""))
    pct = float(pct_m.group(1).replace(",", "")) if pct_m else None
    # Prefer parenthetical absolute when present
    paren = re.search(r"\(([\d.,]+)\)", s)
    abs_v = float(paren.group(1).replace(",", "")) if paren else (
        float(num_m.group(1).replace(",", "")) if num_m and pct is None else None
    )
    if pct is not None and "(" in s and paren:
        # '275.73 (0.55%)' → abs first
        lead = re.match(r"^\s*([\d.,]+)", s.replace(",", ""))
        if lead and "%" not in s[: s.find("(")]:
            abs_v = float(lead.group(1))
    return abs_v, pct


def _num(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    # Drop trailing units like '%'
    s = re.sub(r"%$", "", s)
    m = re.match(r"^[+-]?[\d.]+", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _win_rate_from_profit_trades(s: Optional[str]) -> Optional[float]:
    """'4 (44.44%)' → 44.44"""
    if not s:
        return None
    m = re.search(r"\(([\d.]+)\s*%\)", s)
    if m:
        return float(m.group(1))
    return None


def _extract_inputs(html: str) -> Dict[str, str]:
    inputs: Dict[str, str] = {}
    # Rows like <b>default_risk_percent=0.3</b>
    for m in re.finditer(r"<b>\s*([A-Za-z_][\w]*)\s*=\s*([^<]+?)\s*</b>", html, flags=re.I):
        inputs[m.group(1)] = m.group(2).strip()
    return inputs


def _strategy_label(inputs: Dict[str, str], symbol: str) -> str:
    mode = inputs.get("strategy_mode", "")
    if mode == "0" or "XAU" in symbol.upper() or "GOLD" in symbol.upper():
        return "NY ORB"
    if mode == "1":
        return "Asian Sweep"
    return f"mode={mode}" if mode else "OLC"


def compute_score(net_pct: float, profit_factor: float, max_dd_pct: float) -> float:
    return round((net_pct * profit_factor) / (max_dd_pct + 0.1), 2)


def _read_html(path: str) -> str:
    """MT5 tester reports are often UTF-16 LE with BOM; fall back to UTF-8."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    # Heuristic: lots of NUL bytes → UTF-16 without reliable BOM handling
    if raw[:200].count(b"\x00") > 40:
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def parse_tester_report(path: str, deposit: float = 50000.0) -> Dict[str, Any]:
    """Parse an MT5 Strategy Tester .htm/.html report into a normalized result dict."""
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    html = _read_html(path)

    kv = _label_value_map(html)
    inputs = _extract_inputs(html)

    symbol = kv.get("Symbol", "")
    expert = kv.get("Expert", "")
    period = kv.get("Period", "")

    net = _num(kv.get("Total Net Profit"))
    pf = _num(kv.get("Profit Factor")) or 0.0
    trades = int(_num(kv.get("Total Trades")) or 0)
    wr = _win_rate_from_profit_trades(kv.get("Profit Trades (% of total)"))
    if wr is None:
        wr = 0.0

    _, dd_pct = _parse_pct_pair(kv.get("Equity Drawdown Maximal", "") or "")
    if dd_pct is None:
        _, dd_pct = _parse_pct_pair(kv.get("Balance Drawdown Maximal", "") or "")
    if dd_pct is None:
        dd_pct = 0.0

    if net is None:
        net = 0.0
    net_pct = round((net / deposit) * 100, 2) if deposit else 0.0

    risk = _num(inputs.get("default_risk_percent") or inputs.get("gold_risk_percent"))
    profit_x = _num(
        inputs.get("default_profit_x")
        or inputs.get("gold_profit_x")
        or inputs.get("eur_profit_x")
    )
    sl_gap = _num(
        inputs.get("default_sl_gap") or inputs.get("gold_sl_gap") or inputs.get("eur_sl_gap")
    )
    level_gap = _num(
        inputs.get("default_level_gap")
        or inputs.get("gold_level_gap")
        or inputs.get("eur_level_gap")
    )

    day_filter = "All Days"
    if inputs.get("h1_ema_period"):
        day_filter = f"EMA{inputs['h1_ema_period']}"

    score = compute_score(net_pct, pf, dd_pct)

    row = {
        "symbol": symbol,
        "strategy": _strategy_label(inputs, symbol),
        "risk_pct": risk if risk is not None else "",
        "profit_x": profit_x if profit_x is not None else "",
        "sl_gap": sl_gap if sl_gap is not None else "",
        "level_gap": level_gap if level_gap is not None else "",
        "day_filter": day_filter,
        "total_trades": trades,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "net_profit_usd": round(net, 2),
        "net_profit_pct": net_pct,
        "max_drawdown_pct": round(dd_pct, 2),
        "score": score,
        "source": "mt5_tester",
        "expert": expert,
        "period": period,
        "report_path": os.path.abspath(path),
        "inputs": inputs,
        "raw": {
            "Total Net Profit": kv.get("Total Net Profit"),
            "Profit Factor": kv.get("Profit Factor"),
            "Total Trades": kv.get("Total Trades"),
            "Equity Drawdown Maximal": kv.get("Equity Drawdown Maximal"),
            "Balance Drawdown Maximal": kv.get("Balance Drawdown Maximal"),
            "Profit Trades (% of total)": kv.get("Profit Trades (% of total)"),
        },
    }
    return row
