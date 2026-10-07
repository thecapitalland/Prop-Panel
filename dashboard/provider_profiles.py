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
    rules_version: str = "2026-10",
    source_url: Optional[str] = None,
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
            "reset_hour_server": int(reset_hour_server),
            "reset_hour_utc": reset_hour_utc,
        },
        "rules_version": rules_version,
        "source_url": source_url,
        "verified_at": "2026-10-07",
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
        source_url="https://sarmayegozarebartar.com/select-the-rules/",
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
        source_url="https://sarmayegozarebartar.com/select-the-rules/",
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


def _materialize(profile: Dict[str, Any]) -> Dict[str, Any]:
    p = deepcopy(profile)
    initial = float(p.get("initial_balance") or 0.0)
    if "profit_target_pct" in p:
        p["profit_target_usd"] = round(initial * float(p["profit_target_pct"]) / 100.0, 2)
    if "daily_loss_limit_pct" in p:
        p["daily_loss_limit_usd"] = round(initial * float(p["daily_loss_limit_pct"]) / 100.0, 2)
    if "max_loss_limit_pct" in p:
        p["max_loss_limit_usd"] = round(initial * float(p["max_loss_limit_pct"]) / 100.0, 2)
    return p


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
        if isinstance(get_profile(str(chosen)).get("daily_loss"), dict):
            nested = get_profile(str(chosen))["daily_loss"]
            nested.update(supplied.get("daily_loss") or {})
            base["daily_loss"] = nested
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
            "reset_hour_server": reset,
            "reset_hour_utc": None,
        },
    )
    return _materialize(supplied)
