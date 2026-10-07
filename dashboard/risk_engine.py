"""Pure advisory prop-risk calculations. No Flask or MetaTrader dependency."""
from __future__ import annotations

from typing import Any, Dict, Optional


def _bucket(used_pct: Optional[float], warning: float, high: float) -> str:
    if used_pct is None:
        return "UNAVAILABLE"
    if used_pct >= 100.0:
        return "BREACH"
    if used_pct >= high:
        return "HIGH_RISK"
    if used_pct >= warning:
        return "WARNING"
    return "SAFE"


def _limit_view(current_loss: float, projected_loss: float, limit: float) -> Dict[str, Any]:
    if limit <= 0:
        return {
            "available": False,
            "limit_usd": float(limit),
            "current_loss_usd": round(current_loss, 2),
            "projected_loss_usd": round(projected_loss, 2),
            "current_usage_pct": None,
            "projected_usage_pct": None,
            "current_buffer_usd": None,
            "projected_buffer_usd": None,
        }
    return {
        "available": True,
        "limit_usd": round(limit, 2),
        "current_loss_usd": round(current_loss, 2),
        "projected_loss_usd": round(projected_loss, 2),
        "current_usage_pct": round(current_loss / limit * 100.0, 2),
        "projected_usage_pct": round(projected_loss / limit * 100.0, 2),
        "current_buffer_usd": round(max(0.0, limit - current_loss), 2),
        "projected_buffer_usd": round(max(0.0, limit - projected_loss), 2),
    }


def evaluate_risk(
    *,
    account: Dict[str, Any],
    profile: Dict[str, Any],
    open_sl_risk_usd: float,
    proposed_trade_risk_usd: float = 0.0,
    unbounded_positions: int = 0,
    personal_policy: Optional[Dict[str, Any]] = None,
    day_start_reference: Optional[float] = None,
) -> Dict[str, Any]:
    open_risk = float(open_sl_risk_usd)
    proposed_risk = float(proposed_trade_risk_usd)
    if open_risk < 0 or proposed_risk < 0:
        raise ValueError("risk exposure cannot be negative")
    if int(unbounded_positions) < 0:
        raise ValueError("unbounded_positions cannot be negative")

    equity = float(account.get("equity") or 0.0)
    initial = float(profile.get("initial_balance") or 0.0)
    daily_limit = float(profile.get("daily_loss_limit_usd") or 0.0)
    max_limit = float(profile.get("max_loss_limit_usd") or 0.0)
    reference = float(day_start_reference if day_start_reference is not None else account.get("balance") or equity)

    known_downside = open_risk + proposed_risk
    projected_equity = equity - known_downside

    current_daily_loss = max(0.0, reference - equity)
    projected_daily_loss = max(0.0, reference - projected_equity)
    current_overall_loss = max(0.0, initial - equity)
    projected_overall_loss = max(0.0, initial - projected_equity)

    daily = _limit_view(current_daily_loss, projected_daily_loss, daily_limit)
    overall = _limit_view(current_overall_loss, projected_overall_loss, max_limit)

    policy = {
        "warning_threshold_pct": 70.0,
        "high_risk_threshold_pct": 85.0,
        **(personal_policy or {}),
    }
    warning = float(policy["warning_threshold_pct"])
    high = float(policy["high_risk_threshold_pct"])

    statuses = []
    for view in (daily, overall):
        if view["available"]:
            statuses.append(_bucket(view["projected_usage_pct"], warning, high))
    rank = {"UNAVAILABLE": 0, "SAFE": 1, "WARNING": 2, "HIGH_RISK": 3, "BREACH": 4}
    provider_status = max(statuses, key=lambda s: rank[s]) if statuses else "UNAVAILABLE"

    has_unbounded = int(unbounded_positions) > 0
    if has_unbounded:
        provider_status = "UNBOUNDED_RISK"

    remaining = [
        v["projected_buffer_usd"]
        for v in (daily, overall)
        if v["available"] and v["projected_buffer_usd"] is not None
    ]
    safe_additional = min(remaining) if remaining else 0.0
    if has_unbounded:
        safe_additional = 0.0

    personal_status = "NOT_CONFIGURED"
    personal_limit = None
    personal_pct = policy.get("personal_daily_stop_pct")
    if personal_pct is not None and initial > 0:
        personal_limit = initial * float(personal_pct) / 100.0
        personal_status = "STOP" if projected_daily_loss >= personal_limit else "OK"

    return {
        "advisory_only": True,
        "profile_id": profile.get("profile_id"),
        "provider": profile.get("provider"),
        "equity": round(equity, 2),
        "day_start_reference": round(reference, 2),
        "open_sl_risk_usd": round(open_risk, 2),
        "proposed_trade_risk_usd": round(proposed_risk, 2),
        "known_downside_usd": round(known_downside, 2),
        "projected_worst_case_equity": round(projected_equity, 2),
        "daily": daily,
        "overall": overall,
        "provider_status": provider_status,
        "has_unbounded_risk": has_unbounded,
        "unbounded_positions": int(unbounded_positions),
        "safe_additional_risk_usd": round(max(0.0, safe_additional), 2),
        "personal_status": personal_status,
        "personal_daily_stop_usd": round(personal_limit, 2) if personal_limit is not None else None,
        "thresholds": {"warning_pct": warning, "high_risk_pct": high, "breach_pct": 100.0},
    }
