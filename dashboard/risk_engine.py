"""Pure advisory prop-risk calculations. No Flask or MetaTrader dependency."""
from __future__ import annotations

from typing import Any, Dict, Optional


_RANK = {"UNAVAILABLE": 0, "SAFE": 1, "WARNING": 2, "HIGH_RISK": 3, "BREACH": 4}


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


def _basis_amount(rule: Dict[str, Any], account: Dict[str, Any], profile: Dict[str, Any]) -> float:
    basis = str(rule.get("basis") or "initial_balance")
    if basis == "current_balance":
        return float(account.get("balance") or 0.0)
    if basis == "current_equity":
        return float(account.get("equity") or 0.0)
    return float(profile.get("initial_balance") or 0.0)


def _constraint_view(
    *,
    current_risk: float,
    projected_risk: float,
    rule: Dict[str, Any],
    account: Dict[str, Any],
    profile: Dict[str, Any],
    warning: float,
    high: float,
) -> Dict[str, Any]:
    if not rule or not bool(rule.get("enabled", False)):
        return {"available": False, "status": "UNAVAILABLE"}
    limit_pct = rule.get("limit_pct")
    if limit_pct is None:
        return {
            "available": False,
            "status": "UNAVAILABLE",
            "reason": rule.get("unavailable_reason") or "constraint limit unavailable",
        }
    basis_usd = _basis_amount(rule, account, profile)
    limit_usd = basis_usd * float(limit_pct) / 100.0
    view = _limit_view(float(current_risk), float(projected_risk), limit_usd)
    raw_pct = (float(projected_risk) / limit_usd * 100.0) if limit_usd > 0 else None
    view.update({
        "status": _bucket(raw_pct, warning, high),
        "limit_pct": float(limit_pct),
        "basis": rule.get("basis") or "initial_balance",
        "basis_usd": round(basis_usd, 2),
        "source_type": rule.get("source_type"),
        "source_url": rule.get("source_url"),
    })
    return view


def evaluate_risk(
    *,
    account: Dict[str, Any],
    profile: Dict[str, Any],
    open_sl_risk_usd: float,
    proposed_trade_risk_usd: float = 0.0,
    unbounded_positions: int = 0,
    personal_policy: Optional[Dict[str, Any]] = None,
    day_start_reference: Optional[float] = None,
    open_concurrent_risk_usd: Optional[float] = None,
    open_risk_by_symbol: Optional[Dict[str, float]] = None,
    current_concurrent_loss_usd: Optional[float] = None,
    current_loss_by_symbol: Optional[Dict[str, float]] = None,
    proposed_symbol: Optional[str] = None,
    proposed_symbol_risk_usd: Optional[float] = None,
) -> Dict[str, Any]:
    open_risk = float(open_sl_risk_usd)
    proposed_risk = float(proposed_trade_risk_usd)
    concurrent_open = float(
        open_concurrent_risk_usd if open_concurrent_risk_usd is not None else open_risk
    )
    symbol_risk = {
        str(k): float(v) for k, v in (open_risk_by_symbol or {}).items()
    }
    current_concurrent_loss = float(current_concurrent_loss_usd or 0.0)
    current_symbol_loss = {
        str(k): float(v) for k, v in (current_loss_by_symbol or {}).items()
    }
    proposed_symbol_risk = float(
        proposed_symbol_risk_usd
        if proposed_symbol_risk_usd is not None
        else proposed_risk
    )
    if min(
        open_risk,
        proposed_risk,
        concurrent_open,
        current_concurrent_loss,
        proposed_symbol_risk,
    ) < 0:
        raise ValueError("risk exposure cannot be negative")
    if any(v < 0 for v in symbol_risk.values()) or any(
        v < 0 for v in current_symbol_loss.values()
    ):
        raise ValueError("symbol risk exposure cannot be negative")
    if int(unbounded_positions) < 0:
        raise ValueError("unbounded_positions cannot be negative")

    equity = float(account.get("equity") or 0.0)
    initial = float(profile.get("initial_balance") or 0.0)
    daily_limit = float(profile.get("daily_loss_limit_usd") or 0.0)
    max_limit = float(profile.get("max_loss_limit_usd") or 0.0)
    reference = float(
        day_start_reference
        if day_start_reference is not None
        else account.get("balance") or equity
    )

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
            raw_used_pct = (
                float(view["projected_loss_usd"]) / float(view["limit_usd"]) * 100.0
                if float(view["limit_usd"]) > 0
                else None
            )
            statuses.append(_bucket(raw_used_pct, warning, high))

    constraints_cfg = profile.get("risk_constraints") or {}
    aggregate_rule = constraints_cfg.get("aggregate_open_risk") or {}
    aggregate_open_projection = max(concurrent_open, current_concurrent_loss)
    aggregate = _constraint_view(
        current_risk=current_concurrent_loss,
        projected_risk=aggregate_open_projection + proposed_symbol_risk,
        rule=aggregate_rule,
        account=account,
        profile=profile,
        warning=warning,
        high=high,
    )
    if aggregate.get("available"):
        statuses.append(str(aggregate["status"]))

    per_symbol_rule = constraints_cfg.get("per_symbol_open_risk") or {}
    per_symbol_by_symbol: Dict[str, Dict[str, Any]] = {}
    if per_symbol_rule.get("enabled"):
        symbols = set(symbol_risk) | set(current_symbol_loss)
        if proposed_symbol:
            symbols.add(str(proposed_symbol))
        for symbol in sorted(symbols):
            current = float(current_symbol_loss.get(symbol, 0.0))
            planned = max(current, float(symbol_risk.get(symbol, 0.0)))
            projected = planned + (
                proposed_symbol_risk if proposed_symbol and symbol == str(proposed_symbol) else 0.0
            )
            per_symbol_by_symbol[symbol] = _constraint_view(
                current_risk=current,
                projected_risk=projected,
                rule=per_symbol_rule,
                account=account,
                profile=profile,
                warning=warning,
                high=high,
            )

    per_symbol = {"available": False, "status": "UNAVAILABLE", "by_symbol": per_symbol_by_symbol}
    if per_symbol_by_symbol:
        if proposed_symbol and str(proposed_symbol) in per_symbol_by_symbol:
            chosen_symbol = str(proposed_symbol)
        else:
            chosen_symbol = max(
                per_symbol_by_symbol,
                key=lambda s: _RANK.get(
                    str(per_symbol_by_symbol[s].get("status") or "UNAVAILABLE"), 0
                ),
            )
        per_symbol = {
            **per_symbol_by_symbol[chosen_symbol],
            "symbol": chosen_symbol,
            "by_symbol": per_symbol_by_symbol,
        }
        statuses.extend(
            str(v["status"])
            for v in per_symbol_by_symbol.values()
            if v.get("available")
        )

    provider_status = (
        max(statuses, key=lambda s: _RANK.get(s, 0))
        if statuses
        else "UNAVAILABLE"
    )

    has_unbounded = int(unbounded_positions) > 0
    exposure_status = "UNBOUNDED_RISK" if has_unbounded else "BOUNDED"

    provider_buffers = [
        v["projected_buffer_usd"]
        for v in (daily, overall, aggregate, per_symbol)
        if v.get("available") and v.get("projected_buffer_usd") is not None
    ]
    remaining_to_breach = min(provider_buffers) if provider_buffers else 0.0

    # "Safe additional risk" is the amount available from the CURRENT state.
    # Drawdown budgets stop at the personal warning threshold; provider-specific
    # concurrent-risk rules use their explicit hard limits.
    safe_candidates = []
    for code, view in (("daily_drawdown", daily), ("max_drawdown", overall)):
        if view["available"]:
            warning_limit = float(view["limit_usd"]) * warning / 100.0
            room = max(0.0, warning_limit - float(view["projected_loss_usd"]))
            safe_candidates.append((code, room))

    if aggregate.get("available"):
        safe_candidates.append((
            "aggregate_open_risk",
            max(0.0, float(aggregate["limit_usd"]) - float(aggregate["projected_loss_usd"])),
        ))

    if per_symbol.get("available"):
        safe_candidates.append((
            "per_symbol_open_risk",
            max(
                0.0,
                float(per_symbol["limit_usd"])
                - float(per_symbol["projected_loss_usd"]),
            ),
        ))

    personal_status = "NOT_CONFIGURED"
    personal_limit = None
    personal_pct = policy.get("personal_daily_stop_pct")
    if personal_pct is not None and initial > 0:
        personal_limit = initial * float(personal_pct) / 100.0
        personal_status = "STOP" if projected_daily_loss >= personal_limit else "OK"
        safe_candidates.append((
            "personal_daily_stop",
            max(0.0, personal_limit - projected_daily_loss),
        ))

    if safe_candidates:
        binding_code, safe_additional = min(safe_candidates, key=lambda item: item[1])
        binding_constraint = {
            "code": binding_code,
            "remaining_usd": round(float(safe_additional), 2),
        }
    else:
        safe_additional = 0.0
        binding_constraint = {"code": "unavailable", "remaining_usd": 0.0}

    if provider_status == "BREACH":
        safe_additional = 0.0
        binding_constraint = {
            "code": "existing_provider_breach",
            "remaining_usd": 0.0,
        }
    if has_unbounded:
        safe_additional = 0.0

    if has_unbounded:
        recommendation_status = "CANNOT_ASSERT_SAFE"
    elif provider_status == "BREACH":
        recommendation_status = "NOT_COMPLIANT"
    elif personal_status == "STOP":
        recommendation_status = "PERSONAL_STOP"
    else:
        recommendation_status = provider_status

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
        "constraints": {
            "aggregate": aggregate,
            "per_symbol": per_symbol,
        },
        "provider_status": provider_status,
        "exposure_status": exposure_status,
        "recommendation_status": recommendation_status,
        "has_unbounded_risk": has_unbounded,
        "unbounded_positions": int(unbounded_positions),
        "safe_additional_risk_usd": round(max(0.0, safe_additional), 2),
        "remaining_to_breach_usd": round(max(0.0, remaining_to_breach), 2),
        "binding_constraint": binding_constraint,
        "personal_status": personal_status,
        "personal_daily_stop_usd": (
            round(personal_limit, 2) if personal_limit is not None else None
        ),
        "thresholds": {
            "warning_pct": warning,
            "high_risk_pct": high,
            "breach_pct": 100.0,
        },
    }
