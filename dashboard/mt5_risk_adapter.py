"""Convert MT5 position/trade inputs into USD downside exposure.

The caller injects the profit calculator so this module stays unit-testable.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List

ProfitCalculator = Callable[[str, str, float, float, float], float]


def _side(value: Any) -> str:
    side = str(value or "").upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    return side


def _positive(value: Any, name: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a positive number")
    if n <= 0:
        raise ValueError(f"{name} must be a positive number")
    return n


def position_sl_exposure(position: Dict[str, Any], profit_calculator: ProfitCalculator) -> Dict[str, Any]:
    symbol = str(position.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("position symbol is required")
    side = _side(position.get("type") or position.get("side"))
    volume = _positive(position.get("volume"), "volume")
    current = _positive(position.get("price_current"), "price_current")
    sl = float(position.get("sl") or 0.0)
    if sl <= 0:
        return {
            "ticket": position.get("ticket"),
            "symbol": symbol,
            "side": side,
            "risk_usd": None,
            "unbounded": True,
            "reason": "missing_stop_loss",
        }

    pnl = float(profit_calculator(symbol, side, volume, current, sl))
    return {
        "ticket": position.get("ticket"),
        "symbol": symbol,
        "side": side,
        "risk_usd": round(max(0.0, -pnl), 2),
        "unbounded": False,
        "reason": None,
    }


def aggregate_open_sl_exposure(positions: List[Dict[str, Any]], profit_calculator: ProfitCalculator) -> Dict[str, Any]:
    items = [position_sl_exposure(p, profit_calculator) for p in positions]
    known = sum(float(i["risk_usd"] or 0.0) for i in items if not i["unbounded"])
    unbounded = sum(1 for i in items if i["unbounded"])
    return {
        "known_risk_usd": round(known, 2),
        "unbounded_positions": unbounded,
        "positions": items,
    }


def proposed_trade_exposure(trade: Dict[str, Any], profit_calculator: ProfitCalculator) -> Dict[str, Any]:
    symbol = str(trade.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("symbol is required")
    side = _side(trade.get("side"))
    volume = _positive(trade.get("volume"), "volume")
    entry = _positive(trade.get("entry"), "entry")
    sl = _positive(trade.get("stop_loss"), "stop_loss")

    if side == "BUY" and sl >= entry:
        raise ValueError("BUY stop_loss must be below entry")
    if side == "SELL" and sl <= entry:
        raise ValueError("SELL stop_loss must be above entry")

    pnl = float(profit_calculator(symbol, side, volume, entry, sl))
    risk = max(0.0, -pnl)
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop_loss": sl,
        "volume": volume,
        "risk_usd": round(risk, 2),
    }
