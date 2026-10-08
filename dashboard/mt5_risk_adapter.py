"""Convert MT5 position/trade inputs into USD downside exposure.

The caller injects the profit calculator so this module stays unit-testable.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from typing import Any, Callable, Dict, List, Optional

ProfitCalculator = Callable[[str, str, float, float, float], float]
SymbolNormalizer = Callable[[str], str]


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


def _risk_at_price(
    profit_calculator: ProfitCalculator,
    symbol: str,
    side: str,
    volume: float,
    price_from: float,
    price_to: float,
) -> float:
    pnl = float(profit_calculator(symbol, side, volume, price_from, price_to))
    return round(max(0.0, -pnl), 2)


def position_sl_exposure(
    position: Dict[str, Any],
    profit_calculator: ProfitCalculator,
    symbol_normalizer: Optional[SymbolNormalizer] = None,
) -> Dict[str, Any]:
    symbol = str(position.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("position symbol is required")
    side = _side(position.get("type") or position.get("side"))
    volume = _positive(position.get("volume"), "volume")
    current = _positive(position.get("price_current"), "price_current")
    price_open = float(position.get("price_open") or current)
    if price_open <= 0:
        price_open = current
    sl = float(position.get("sl") or 0.0)
    group = symbol_normalizer(symbol) if symbol_normalizer else symbol.upper()
    current_loss = max(0.0, -float(position.get("profit") or 0.0))

    if sl <= 0:
        return {
            "ticket": position.get("ticket"),
            "symbol": symbol,
            "symbol_group": group,
            "side": side,
            "risk_usd": None,
            "remaining_risk_usd": None,
            "entry_risk_usd": None,
            "current_loss_usd": round(current_loss, 2),
            "unbounded": True,
            "reason": "missing_stop_loss",
        }

    remaining_risk = _risk_at_price(
        profit_calculator, symbol, side, volume, current, sl
    )
    entry_risk = _risk_at_price(
        profit_calculator, symbol, side, volume, price_open, sl
    )
    return {
        "ticket": position.get("ticket"),
        "symbol": symbol,
        "symbol_group": group,
        "side": side,
        "risk_usd": remaining_risk,
        "remaining_risk_usd": remaining_risk,
        "entry_risk_usd": entry_risk,
        "current_loss_usd": round(current_loss, 2),
        "unbounded": False,
        "reason": None,
    }


def aggregate_open_sl_exposure(
    positions: List[Dict[str, Any]],
    profit_calculator: ProfitCalculator,
    symbol_normalizer: Optional[SymbolNormalizer] = None,
) -> Dict[str, Any]:
    items = [
        position_sl_exposure(p, profit_calculator, symbol_normalizer)
        for p in positions
    ]
    known_remaining = sum(
        float(i["remaining_risk_usd"] or 0.0) for i in items if not i["unbounded"]
    )
    entry_risk = sum(
        float(i["entry_risk_usd"] or 0.0) for i in items if not i["unbounded"]
    )
    current_loss = sum(float(i["current_loss_usd"] or 0.0) for i in items)
    unbounded = sum(1 for i in items if i["unbounded"])

    risk_by_symbol: Dict[str, float] = {}
    current_loss_by_symbol: Dict[str, float] = {}
    for item in items:
        group = str(item["symbol_group"])
        current_loss_by_symbol[group] = (
            current_loss_by_symbol.get(group, 0.0)
            + float(item["current_loss_usd"] or 0.0)
        )
        if not item["unbounded"]:
            risk_by_symbol[group] = (
                risk_by_symbol.get(group, 0.0)
                + float(item["entry_risk_usd"] or 0.0)
            )

    return {
        "known_risk_usd": round(known_remaining, 2),
        "entry_risk_usd": round(entry_risk, 2),
        "current_loss_usd": round(current_loss, 2),
        "risk_by_symbol": {k: round(v, 2) for k, v in risk_by_symbol.items()},
        "current_loss_by_symbol": {
            k: round(v, 2) for k, v in current_loss_by_symbol.items()
        },
        "unbounded_positions": unbounded,
        "positions": items,
    }


def proposed_trade_exposure(
    trade: Dict[str, Any],
    profit_calculator: ProfitCalculator,
) -> Dict[str, Any]:
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

    risk = _risk_at_price(profit_calculator, symbol, side, volume, entry, sl)
    return {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop_loss": sl,
        "volume": volume,
        "risk_usd": risk,
    }


def max_volume_for_risk(
    *,
    risk_budget_usd: float,
    risk_per_lot_usd: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> float:
    """Largest broker-valid volume that does not exceed the supplied risk budget."""
    budget = Decimal(str(max(0.0, float(risk_budget_usd))))
    per_lot = Decimal(str(float(risk_per_lot_usd)))
    vmin = Decimal(str(float(volume_min)))
    vmax = Decimal(str(float(volume_max)))
    step = Decimal(str(float(volume_step)))
    if per_lot <= 0 or step <= 0 or vmax <= 0:
        return 0.0

    raw = min(budget / per_lot, vmax)
    if raw < vmin:
        return 0.0
    steps = (raw / step).to_integral_value(rounding=ROUND_FLOOR)
    volume = min(steps * step, vmax)
    if volume < vmin:
        return 0.0
    return float(volume)
