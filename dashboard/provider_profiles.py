"""Provider rule profiles and backward-compatible normalization.

Provider rules are data. Core risk math must not branch on provider names.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional


def _profile(
    *,
    profile_id: str,
    provider: str,
    program: str,
    phase: int,
    name: str,
    initial_balance: float,
    profit_target_pct: float,
    daily_loss_pct: float,
    max_loss_pct: float,
    min_trading_days: int = 0,
    min_day_profit_pct: float = 0.0,
    consistency_cap_pct: float = 100.0,
    daily_reference: str = "day_start_balance",
    reset_hour_server: int = 0,
    reset_hour_utc: Optional[int] = None,
    reset_basis: Optional[str] = None,
    rules_version: str = "2026-10",
    source_url: Optional[str] = None,
    risk_constraints: Optional[Dict[str, Any]] = None,
    symbol_normalization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "profile_id": profile_id,
        "provider": provider,
        "program": program,
        "phase": phase,
        "name": name,
        "initial_balance": float(initial_balance),
        "profit_target_pct": float(profit_target_pct),
        "daily_loss_limit_pct": float(daily_loss_pct),
        "max_loss_limit_pct": float(max_loss_pct),
        "profit_target_usd": round(initial_balance * profit_target_pct / 100.0, 2),
        "daily_loss_limit_usd": round(initial_balance * daily_loss_pct / 100.0, 2),
        "max_loss_limit_usd": round(initial_balance * max_loss_pct / 100.0, 2),
        "min_trading_days": int(min_trading_days),
        "min_day_profit_pct": float(min_day_profit_pct),
        "day_reset_hour_server": int(reset_hour_server),
        "consistency_cap_pct": float(consistency_cap_pct),
        "daily_loss": {
            "reference": daily_reference,
            "includes_floating": True,
            "reset_basis": reset_basis or ("utc" if reset_hour_utc is not None else "server_local"),
            "reset_hour_server": int(reset_hour_server),
            "reset_hour_utc": reset_hour_utc,
        },
        "risk_constraints": deepcopy(risk_constraints or {}),
        "symbol_normalization": deepcopy(symbol_normalization or {}),
        "rules_version": rules_version,
        "source_url": source_url,
        "verified_at": "2026-10-08",
    }


_SGB_RISK_CONSTRAINTS = {
    "aggregate_open_risk": {
        "enabled": True,
        "basis": "current_balance",
        "tiers": [
            {
                "initial_balances": [1000.0, 2500.0, 5000.0, 10000.0, 25000.0, 50000.0],
                "limit_pct": 3.0,
            },
            {
                "initial_balances": [100000.0, 200000.0],
                "limit_pct": 2.0,
            },
        ],
        "source_type": "public_provider_rule",
        "source_url": "https://sarmayegozarebartar.com/select-the-rules/",
    },
    "per_symbol_open_risk": {
        "enabled": True,
        "basis": "current_balance",
        "limit_pct": 2.0,
        "source_type": "user_supplied_account_rule",
        "source_url": None,
        "note": "Concurrent loss/risk on one normalized symbol must not exceed 2%.",
    },
}


_PROFILES: Dict[str, Dict[str, Any]] = {
    "moneta_2step_phase1_5_10": _profile(
        profile_id="moneta_2step_phase1_5_10",
        provider="moneta_funded",
        program="2-step",
        phase=1,
        name="Moneta Funded 2-Step - Phase I (5% / 10%)",
        initial_balance=50000.0,
        profit_target_pct=5.0,
        daily_loss_pct=5.0,
        max_loss_pct=10.0,
        min_trading_days=3,
        min_day_profit_pct=0.5,
        consistency_cap_pct=20.0,
        daily_reference="max_balance_equity_at_reset",
        reset_hour_server=0,
        reset_hour_utc=22,
        reset_basis="utc",
        source_url="https://www.monetafunded.com/general-rules/",
    ),
    "moneta_2step_phase1_4_8": _profile(
        profile_id="moneta_2step_phase1_4_8",
        provider="moneta_funded",
        program="2-step",
        phase=1,
        name="Moneta Funded 2-Step - Phase I (4% / 8%)",
        initial_balance=50000.0,
        profit_target_pct=5.0,
        daily_loss_pct=4.0,
        max_loss_pct=8.0,
        min_trading_days=3,
        min_day_profit_pct=0.5,
        consistency_cap_pct=20.0,
        daily_reference="max_balance_equity_at_reset",
        reset_hour_server=0,
        reset_hour_utc=22,
        reset_basis="utc",
        source_url="https://www.monetafunded.com/general-rules/",
    ),
    "sgb_plan_a_phase1": _profile(
        profile_id="sgb_plan_a_phase1",
        provider="sgb",
        program="plan-a",
        phase=1,
        name="SGB Plan A - Phase I",
        initial_balance=50000.0,
        profit_target_pct=8.0,
        daily_loss_pct=5.0,
        max_loss_pct=12.0,
        daily_reference="day_start_balance",
        reset_hour_server=0,
        reset_hour_utc=None,
        reset_basis="broker_server",
        source_url="https://sarmayegozarebartar.com/select-the-rules/",
        risk_constraints=_SGB_RISK_CONSTRAINTS,
        symbol_normalization={"strip_suffixes": [".X"]},
    ),
    "sgb_plan_b_phase1": _profile(
        profile_id="sgb_plan_b_phase1",
        provider="sgb",
        program="plan-b",
        phase=1,
        name="SGB Plan B - Phase I",
        initial_balance=50000.0,
        profit_target_pct=10.0,
        daily_loss_pct=5.0,
        max_loss_pct=12.0,
        daily_reference="day_start_balance",
        reset_hour_server=0,
        reset_hour_utc=None,
        reset_basis="broker_server",
        source_url="https://sarmayegozarebartar.com/select-the-rules/",
        risk_constraints=_SGB_RISK_CONSTRAINTS,
        symbol_normalization={"strip_suffixes": [".X"]},
    ),
    "custom": _profile(
        profile_id="custom",
        provider="custom",
        program="custom",
        phase=1,
        name="Custom Prop Profile",
        initial_balance=50000.0,
        profit_target_pct=5.0,
        daily_loss_pct=5.0,
        max_loss_pct=10.0,
    ),
}


def _tier_matches(tier: Dict[str, Any], initial: float) -> bool:
    exact_values = tier.get("initial_balances")
    if exact_values is not None:
        return any(abs(initial - float(v)) < 0.01 for v in exact_values)
    min_value = tier.get("min_initial_balance")
    max_value = tier.get("max_initial_balance")
    if min_value is not None and initial < float(min_value):
        return False
    if max_value is not None and initial > float(max_value):
        return False
    return True


def _materialize_constraints(constraints: Dict[str, Any], initial: float) -> Dict[str, Any]:
    out = deepcopy(constraints or {})
    for rule in out.values():
        tiers = rule.get("tiers") or []
        if tiers:
            matches = [t for t in tiers if _tier_matches(t, initial)]
            if matches:
                rule["limit_pct"] = float(matches[0]["limit_pct"])
            else:
                rule["enabled"] = False
                rule["unavailable_reason"] = f"no configured tier for initial balance {initial:g}"
    return out


def _materialize(profile: Dict[str, Any]) -> Dict[str, Any]:
    p = deepcopy(profile)
    initial = float(p.get("initial_balance") or 0.0)
    if "profit_target_pct" in p:
        p["profit_target_usd"] = round(initial * float(p["profit_target_pct"]) / 100.0, 2)
    if "daily_loss_limit_pct" in p:
        p["daily_loss_limit_usd"] = round(initial * float(p["daily_loss_limit_pct"]) / 100.0, 2)
    if "max_loss_limit_pct" in p:
        p["max_loss_limit_usd"] = round(initial * float(p["max_loss_limit_pct"]) / 100.0, 2)
    p["risk_constraints"] = _materialize_constraints(p.get("risk_constraints") or {}, initial)
    return p


def normalize_symbol(symbol: str, profile: Dict[str, Any]) -> str:
    """Normalize a broker symbol only by explicit profile rules."""
    value = str(symbol or "").strip().upper()
    cfg = profile.get("symbol_normalization") or {}
    for suffix in cfg.get("strip_suffixes") or []:
        suffix_u = str(suffix).upper()
        if suffix_u and value.endswith(suffix_u):
            value = value[: -len(suffix_u)]
            break
    return value


def get_profile(profile_id: str) -> Dict[str, Any]:
    if profile_id not in _PROFILES:
        raise ValueError(f"unknown provider profile: {profile_id}")
    return _materialize(_PROFILES[profile_id])


def list_profiles() -> List[Dict[str, Any]]:
    return [get_profile(k) for k in sorted(_PROFILES)]


def normalize_profile(
    profile: Optional[Dict[str, Any]] = None,
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize registry or legacy flat config into one profile contract."""
    supplied = deepcopy(profile or {})
    chosen = profile_id or supplied.get("profile_id")
    if chosen and chosen != "legacy_custom":
        base = get_profile(str(chosen))
        base.update(supplied)
        canonical = get_profile(str(chosen))
        if isinstance(canonical.get("daily_loss"), dict):
            nested = canonical["daily_loss"]
            nested.update(supplied.get("daily_loss") or {})
            base["daily_loss"] = nested
        if isinstance(canonical.get("risk_constraints"), dict):
            nested_constraints = canonical["risk_constraints"]
            for key, value in (supplied.get("risk_constraints") or {}).items():
                merged_rule = deepcopy(nested_constraints.get(key) or {})
                merged_rule.update(value or {})
                nested_constraints[key] = merged_rule
            base["risk_constraints"] = nested_constraints
        return _materialize(base)

    if not supplied:
        return get_profile("moneta_2step_phase1_5_10")

    initial = float(supplied.get("initial_balance") or 0.0)
    supplied.setdefault("profile_id", "legacy_custom")
    supplied.setdefault("provider", "custom")
    supplied.setdefault("program", "legacy")
    supplied.setdefault("phase", 1)
    supplied.setdefault("rules_version", "legacy")
    supplied.setdefault("source_url", None)
    supplied.setdefault("verified_at", None)
    supplied.setdefault("risk_constraints", {})
    supplied.setdefault("symbol_normalization", {})
    supplied.setdefault(
        "profit_target_pct",
        (float(supplied.get("profit_target_usd") or 0.0) / initial * 100.0) if initial else 0.0,
    )
    supplied.setdefault(
        "daily_loss_limit_pct",
        (float(supplied.get("daily_loss_limit_usd") or 0.0) / initial * 100.0) if initial else 0.0,
    )
    supplied.setdefault(
        "max_loss_limit_pct",
        (float(supplied.get("max_loss_limit_usd") or 0.0) / initial * 100.0) if initial else 0.0,
    )
    reset = int(supplied.get("day_reset_hour_server") or 0)
    supplied.setdefault(
        "daily_loss",
        {
            "reference": "day_start_balance",
            "includes_floating": True,
            "reset_basis": "server_local",
            "reset_hour_server": reset,
            "reset_hour_utc": None,
        },
    )
    return _materialize(supplied)
