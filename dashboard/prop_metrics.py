"""Pure prop-challenge metrics — no MetaTrader dependency (unit-testable)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from provider_profiles import get_profile, normalize_profile


DEFAULT_PROFILE = get_profile("moneta_2step_phase1_5_10")


def _status(used_pct: float, warn_at: float = 60.0, breach_at: float = 100.0) -> str:
    if used_pct >= breach_at:
        return "BREACHED"
    if used_pct >= warn_at:
        return "WARNING"
    return "SAFE"


def day_bucket(
    ts: int,
    reset_hour: int,
    reset_hour_utc: Optional[int] = None,
    reset_basis: Optional[str] = None,
    server_utc_offset_seconds: Optional[int] = None,
) -> int:
    """Trading-day id using provider reset semantics."""
    basis = reset_basis or ("utc" if reset_hour_utc is not None else "server_local")
    if basis == "broker_server":
        if server_utc_offset_seconds is None:
            raise ValueError("broker server UTC offset required for trading-day bucket")
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) + timedelta(
            seconds=int(server_utc_offset_seconds)
        )
        shifted = dt - timedelta(hours=int(reset_hour))
    elif basis == "utc" or reset_hour_utc is not None:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
        shifted = dt - timedelta(hours=int(reset_hour_utc or 0))
    else:
        dt = datetime.fromtimestamp(int(ts))
        shifted = dt - timedelta(hours=int(reset_hour))
    return int(shifted.strftime("%Y%m%d"))


def next_reset_countdown(
    now: datetime,
    reset_hour: int,
    reset_hour_utc: Optional[int] = None,
    reset_basis: Optional[str] = None,
    server_utc_offset_seconds: Optional[int] = None,
    deal_times_are_utc: bool = True,
) -> Dict[str, Any]:
    """Seconds until next provider reset."""
    basis = reset_basis or ("utc" if reset_hour_utc is not None else "server_local")
    if basis == "broker_server":
        if server_utc_offset_seconds is None:
            return {
                "available": False,
                "seconds_remaining": None,
                "hh": None,
                "mm": None,
                "ss": None,
                "label": "--:--:--",
                "reset_hour": int(reset_hour),
                "reset_timezone": "broker_server",
                "next_reset_iso": None,
            }
        current_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        current = current_utc.astimezone(timezone.utc) + timedelta(
            seconds=int(server_utc_offset_seconds)
        )
        target = current.replace(hour=int(reset_hour), minute=0, second=0, microsecond=0)
        if current >= target:
            target = target + timedelta(days=1)
        reset_label = int(reset_hour)
        reset_timezone = "broker_server"
    elif basis == "utc" or reset_hour_utc is not None:
        current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        target = current.replace(
            hour=int(reset_hour_utc or 0), minute=0, second=0, microsecond=0
        )
        if current >= target:
            target = target + timedelta(days=1)
        reset_label = int(reset_hour_utc or 0)
        reset_timezone = "UTC"
    else:
        current = now.replace(tzinfo=None) if now.tzinfo else now
        target = current.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if current >= target:
            target = target + timedelta(days=1)
        reset_label = int(reset_hour)
        reset_timezone = "server/local"
    remaining = int((target - current).total_seconds())
    h, rem = divmod(max(0, remaining), 3600)
    m, s = divmod(rem, 60)
    return {
        "available": True,
        "seconds_remaining": remaining,
        "hh": h,
        "mm": m,
        "ss": s,
        "label": f"{h:02d}:{m:02d}:{s:02d}",
        "reset_hour": reset_label,
        "reset_timezone": reset_timezone,
        "next_reset_iso": target.isoformat(sep=" ", timespec="seconds"),
    }


def build_closed_trades(deal_list: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Group IN/OUT deals into closed trades + under-30s list (pure Python — no pandas)."""
    if not deal_list:
        return [], []

    by_pos: Dict[int, List[Dict[str, Any]]] = {}
    for d in deal_list:
        if not d.get("symbol"):
            continue
        pid = int(d.get("position_id") or 0)
        if pid <= 0:
            continue
        by_pos.setdefault(pid, []).append(d)

    closed: List[Dict[str, Any]] = []
    under30: List[Dict[str, Any]] = []

    for pos_id, group in by_pos.items():
        group.sort(key=lambda x: int(x.get("time_msc") or x.get("time") or 0))
        in_deals = [d for d in group if int(d.get("entry", -1)) == 0]
        out_deals = [d for d in group if int(d.get("entry", -1)) in (1, 2)]
        if not in_deals or not out_deals:
            continue

        first_in = in_deals[0]
        last_out = out_deals[-1]
        open_time = datetime.fromtimestamp(int(first_in["time"]))
        close_time = datetime.fromtimestamp(int(last_out["time"]))
        t_in = int(first_in.get("time_msc") or int(first_in["time"]) * 1000)
        t_out = int(last_out.get("time_msc") or int(last_out["time"]) * 1000)
        duration_sec = float((t_out - t_in) / 1000.0)
        if duration_sec < 0:
            duration_sec = float((close_time - open_time).total_seconds())

        pnl = float(
            sum(
                float(d.get("profit") or 0)
                + float(d.get("swap") or 0)
                + float(d.get("commission") or 0)
                for d in group
            )
        )
        is_u30 = bool(duration_sec < 30.0)
        info = {
            "position_id": int(pos_id),
            "symbol": str(first_in["symbol"]),
            "type": "BUY" if int(first_in["type"]) == 0 else "SELL",
            "volume": round(float(first_in["volume"]), 2),
            "open_price": float(first_in["price"]),
            "close_price": float(last_out["price"]),
            "open_time": open_time.strftime("%Y-%m-%d %H:%M:%S"),
            "close_time": close_time.strftime("%Y-%m-%d %H:%M:%S"),
            "close_ts": int(last_out["time"]),
            "duration_sec": round(duration_sec, 1),
            "duration_str": (
                f"{int(duration_sec)}s"
                if duration_sec < 60
                else f"{int(duration_sec // 60)}m {int(duration_sec % 60)}s"
            ),
            "profit": round(pnl, 2),
            "is_under_30s": is_u30,
        }
        closed.append(info)
        if is_u30:
            under30.append(info)

    closed.sort(key=lambda x: x["close_time"])
    under30.sort(key=lambda x: x["close_time"], reverse=True)
    return closed, under30


def build_prop_payload(
    *,
    account: Dict[str, Any],
    deal_list: List[Dict[str, Any]],
    open_positions: List[Dict[str, Any]],
    profile: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    terminal: Optional[Dict[str, Any]] = None,
    day_start_reference: Optional[float] = None,
    day_reference_quality: str = "reconstructed_balance",
    server_utc_offset_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble full dashboard JSON from account + deals (no MT5 calls)."""
    profile = normalize_profile(profile=profile)
    now = now or datetime.now(timezone.utc)
    initial = float(profile["initial_balance"])
    pt = float(profile["profit_target_usd"])
    daily_lim = float(profile["daily_loss_limit_usd"])
    max_lim = float(profile["max_loss_limit_usd"])
    min_days = int(profile["min_trading_days"])
    min_day_pct = float(profile["min_day_profit_pct"])
    reset_hour = int(profile["day_reset_hour_server"])
    daily_rule = profile.get("daily_loss") or {}
    reset_hour_utc = daily_rule.get("reset_hour_utc")
    if reset_hour_utc is not None:
        reset_hour_utc = int(reset_hour_utc)
    reset_basis = str(daily_rule.get("reset_basis") or ("utc" if reset_hour_utc is not None else "server_local"))
    provider_time_available = bool(deal_times_are_utc) and not (
        reset_basis == "broker_server" and server_utc_offset_seconds is None
    )
    cons_cap = float(profile["consistency_cap_pct"])

    balance = float(account.get("balance", 0.0))
    equity = float(account.get("equity", 0.0))
    floating = float(account.get("profit", 0.0))

    closed, under30 = build_closed_trades(deal_list)

    # Daily window: from last reset
    today_start = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if now < today_start:
        today_start = today_start - timedelta(days=1)

    # Provider trading-day P/L: include every trade-deal monetary effect
    # (profit/swap/commission), not balance/credit operations.
    daily_nets: Dict[int, float] = {}
    realized_trading_pnl = 0.0
    for d in deal_list:
        if not d.get("symbol"):
            continue
        pnl = float(d.get("profit", 0) + d.get("swap", 0) + d.get("commission", 0))
        realized_trading_pnl += pnl
        if provider_time_available:
            b = day_bucket(
                int(d.get("time_utc", d["time"])),
                reset_hour,
                reset_hour_utc=reset_hour_utc,
                reset_basis=reset_basis,
                server_utc_offset_seconds=server_utc_offset_seconds,
            )
            daily_nets[b] = daily_nets.get(b, 0.0) + pnl

    if provider_time_available:
        now_for_bucket = (
            now.replace(tzinfo=timezone.utc)
            if now.tzinfo is None and reset_basis in ("utc", "broker_server")
            else now
        )
        today_bucket = day_bucket(
            int(now_for_bucket.timestamp()),
            reset_hour,
            reset_hour_utc=reset_hour_utc,
            reset_basis=reset_basis,
            server_utc_offset_seconds=server_utc_offset_seconds,
        )
        today_realized = float(daily_nets.get(today_bucket, 0.0))
    else:
        today_bucket = None
        today_realized = 0.0

    # Prefer an explicit provider reset reference when a live bridge captured it.
    # Otherwise reconstruct from balance and today's realized P/L (approximate).
    explicit_day_reference = (
        day_start_reference is not None and float(day_start_reference) > 0
    )
    daily_available = bool(explicit_day_reference or provider_time_available)
    day_start_balance = (
        float(day_start_reference)
        if explicit_day_reference
        else (balance - today_realized if provider_time_available else balance)
    )
    current_daily_loss = (
        max(0.0, day_start_balance - equity) if daily_available else 0.0
    )
    daily_used_pct = (
        current_daily_loss / daily_lim * 100.0
        if daily_available and daily_lim > 0
        else 0.0
    )

    current_overall_loss = max(0.0, initial - equity)
    overall_used_pct = (current_overall_loss / max_lim * 100.0) if max_lim > 0 else 0.0

    total_pnl = equity - initial
    realized_target_pnl = realized_trading_pnl
    profit_progress_pct = (realized_target_pnl / pt * 100.0) if pt > 0 else 0.0
    if realized_target_pnl < 0:
        profit_progress_pct = 0.0

    day_target = initial * (min_day_pct / 100.0)
    day_rule_applicable = day_target > 0
    trading_days_applicable = min_days > 0
    day_rule_available = bool((not day_rule_applicable) or provider_time_available)
    trading_days_available = bool((not trading_days_applicable) or provider_time_available)
    day_remaining = (
        max(0.0, day_target - today_realized)
        if day_rule_applicable and day_rule_available
        else 0.0
    )
    day_hit = bool(
        day_rule_applicable and day_rule_available and today_realized >= day_target
    )
    day_progress_pct = (
        today_realized / day_target * 100.0
        if day_rule_applicable and day_rule_available
        else 0.0
    )
    qualifying = [
        d for d, net in daily_nets.items()
        if (net >= day_target if day_target > 0 else net > 0)
    ]
    profitable_days = [d for d, net in daily_nets.items() if net > 0]
    counted_days = qualifying if min_day_pct > 0 else profitable_days
    days_with_closes = set()
    if provider_time_available:
        for d in deal_list:
            if d.get("symbol") and int(d.get("entry", -1)) in (1, 2):
                days_with_closes.add(
                    day_bucket(
                        int(d.get("time_utc", d["time"])),
                        reset_hour,
                        reset_hour_utc=reset_hour_utc,
                        reset_basis=reset_basis,
                        server_utc_offset_seconds=server_utc_offset_seconds,
                    )
                )
    best_day = max(daily_nets.values()) if daily_nets else 0.0
    gross_profit_days = sum(v for v in daily_nets.values() if v > 0)
    consistency_pct = (best_day / gross_profit_days * 100.0) if gross_profit_days > 0 else 0.0
    history_start = None
    history_end = None
    close_times = [int(d["time"]) for d in deal_list if d.get("symbol") and int(d.get("entry", -1)) in (1, 2)]
    if close_times:
        history_start = datetime.fromtimestamp(min(close_times)).strftime("%Y-%m-%d")
        history_end = datetime.fromtimestamp(max(close_times)).strftime("%Y-%m-%d")

    wins = [t for t in closed if t["profit"] > 0]
    losses = [t for t in closed if t["profit"] < 0]
    win_amt = sum(t["profit"] for t in wins)
    loss_amt = abs(sum(t["profit"] for t in losses))
    avg_win = (win_amt / len(wins)) if wins else 0.0
    avg_loss = (-loss_amt / len(losses)) if losses else 0.0
    pf = (win_amt / loss_amt) if loss_amt > 0 else (99.0 if win_amt > 0 else 0.0)
    best_trade = max((t["profit"] for t in closed), default=0.0)
    worst_trade = min((t["profit"] for t in closed), default=0.0)
    win_rate = (len(wins) / len(closed) * 100.0) if closed else 0.0
    win_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else 0.0

    # Equity curve from closed trades + threshold lines
    running = initial
    curve = []
    peak = initial
    trough = initial
    for t in closed:
        running += t["profit"]
        peak = max(peak, running)
        trough = min(trough, running)
        curve.append({"time": t["close_time"], "balance": round(running, 2)})
    # Append live equity point
    if curve:
        curve.append({"time": now.strftime("%Y-%m-%d %H:%M:%S"), "balance": round(balance, 2), "equity": round(equity, 2)})
    else:
        curve = [{"time": now.strftime("%Y-%m-%d %H:%M:%S"), "balance": round(balance, 2), "equity": round(equity, 2)}]

    highest_equity = max(peak, equity)
    lowest_equity = min(trough, equity)

    # Symbol breakdown
    sym: Dict[str, Dict[str, Any]] = {}
    for t in closed:
        s = t["symbol"]
        if s not in sym:
            sym[s] = {"symbol": s, "trades": 0, "wins": 0, "losses": 0, "profit": 0.0, "volume": 0.0}
        sym[s]["trades"] += 1
        sym[s]["profit"] += t["profit"]
        sym[s]["volume"] += t["volume"]
        if t["profit"] > 0:
            sym[s]["wins"] += 1
        elif t["profit"] < 0:
            sym[s]["losses"] += 1
    symbol_stats = []
    for s, d in sym.items():
        wr = (d["wins"] / d["trades"] * 100.0) if d["trades"] else 0.0
        symbol_stats.append({
            "symbol": d["symbol"],
            "trades": d["trades"],
            "wins": d["wins"],
            "losses": d["losses"],
            "win_rate": round(wr, 1),
            "profit": round(d["profit"], 2),
            "volume": round(d["volume"], 2),
        })
    symbol_stats.sort(key=lambda x: x["trades"], reverse=True)

    start_date = None
    if closed:
        start_date = closed[0]["open_time"][:10]

    return {
        "ok": True,
        "error": None,
        "terminal": terminal or {},
        "program": {
            "profile_id": profile.get("profile_id"),
            "provider": profile.get("provider"),
            "program": profile.get("program"),
            "phase": profile.get("phase"),
            "name": profile["name"],
            "initial_balance": initial,
            "start_date": start_date,
            "profit_target_usd": pt,
            "daily_loss_limit_usd": daily_lim,
            "max_loss_limit_usd": max_lim,
            "min_trading_days": min_days,
            "min_day_profit_pct": min_day_pct,
            "rules_version": profile.get("rules_version"),
            "source_url": profile.get("source_url"),
            "verified_at": profile.get("verified_at"),
        },
        "account": account,
        "live": {
            "balance": round(balance, 2),
            "equity": round(equity, 2),
            "floating": round(floating, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round((total_pnl / initial * 100.0) if initial else 0.0, 2),
            "highest_equity": round(highest_equity, 2),
            "lowest_equity": round(lowest_equity, 2),
        },
        "daily_drawdown": {
            "available": daily_available,
            "starting_balance": round(day_start_balance, 2) if daily_available else None,
            "reference_quality": (
                day_reference_quality
                if explicit_day_reference
                else (
                    "reconstructed_balance"
                    if provider_time_available
                    else (
                        "broker_offset_unavailable"
                        if reset_basis == "broker_server"
                        else "deal_time_basis_unavailable"
                    )
                )
            ),
            "current_daily_loss": round(current_daily_loss, 2) if daily_available else None,
            "daily_drawdown_pct": (
                round((current_daily_loss / day_start_balance * 100.0) if day_start_balance else 0.0, 2)
                if daily_available else None
            ),
            "limit_usd": daily_lim,
            "used_of_limit_pct": round(daily_used_pct, 2) if daily_available else None,
            "max_daily_loss_pct": round((daily_lim / initial * 100.0) if initial else 0.0, 2),
            "max_daily_loss_usd": daily_lim,
            "status": _status(daily_used_pct) if daily_available else "UNAVAILABLE",
        },
        "overall_drawdown": {
            "initial_balance": initial,
            "current_overall_loss": round(current_overall_loss, 2),
            "overall_drawdown_pct": round((current_overall_loss / initial * 100.0) if initial else 0.0, 2),
            "limit_usd": max_lim,
            "used_of_limit_pct": round(overall_used_pct, 2),
            "max_overall_loss_pct": round((max_lim / initial * 100.0) if initial else 0.0, 2),
            "status": _status(overall_used_pct),
        },
        "profit_target": {
            "target_usd": pt,
            "current_pnl": round(max(0.0, realized_target_pnl), 2),
            "realized_pnl": round(realized_target_pnl, 2),
            "floating_excluded_usd": round(floating, 2),
            "remaining_usd": round(max(0.0, pt - max(0.0, realized_target_pnl)), 2),
            "progress_pct": round(min(100.0, max(0.0, profit_progress_pct)), 2),
            "status": "MET" if realized_target_pnl >= pt else "IN_PROGRESS",
        },
        "today_trading_day": {
            "applicable": day_rule_applicable,
            "available": day_rule_available,
            "realized_pnl": round(today_realized, 2),
            "target_usd": round(day_target, 2),
            "remaining_usd": round(day_remaining, 2),
            "progress_pct": round(min(100.0, max(0.0, day_progress_pct)), 2),
            "hit": day_hit,
            "status": (
                "NOT_APPLICABLE"
                if not day_rule_applicable
                else (
                    "UNAVAILABLE"
                    if not day_rule_available
                    else ("MET" if day_hit else "NOT_MET")
                )
            ),
        },
        "trading_days": {
            "applicable": trading_days_applicable,
            "available": trading_days_available,
            "counted_days": len(counted_days) if trading_days_available else 0,
            "qualifying_days": len(counted_days),
            "days_hitting_05pct": len(qualifying),
            "profitable_days": len(profitable_days),
            "days_with_trades": len(days_with_closes),
            "required": min_days,
            "target_met": (
                len(counted_days) >= min_days
                if trading_days_applicable and trading_days_available
                else False
            ),
            "best_day_profit": round(best_day, 2),
            "consistency_pct": round(consistency_pct, 1),
            "consistency_cap_pct": cons_cap,
            "consistency_ok": consistency_pct <= cons_cap or gross_profit_days <= 0,
            "history_start": history_start,
            "history_end": history_end,
            "day_details": [
                {
                    "day": str(k),
                    "pnl": round(v, 2),
                    "counts": (v >= day_target if day_target > 0 else v > 0),
                    "hits_05pct": (v >= day_target if day_target > 0 else v > 0),
                }
                for k, v in sorted(daily_nets.items())
            ],
        },
        "daily_reset": next_reset_countdown(
            now,
            reset_hour,
            reset_hour_utc=reset_hour_utc,
            reset_basis=reset_basis,
            server_utc_offset_seconds=server_utc_offset_seconds,
        ),
        "chart": {
            "equity_curve": curve[-200:],
            "lines": {
                "profit_target_level": round(initial + pt, 2),
                "max_loss_floor": round(initial - max_lim, 2),
                "daily_loss_floor_today": (
                    round(day_start_balance - daily_lim, 2) if daily_available else None
                ),
                "initial_balance": initial,
            },
        },
        "stats": {
            "total_trades": len(closed),
            "raw_deals": len(deal_list),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            # avg win $ / avg loss $  (NOT the same as win_rate %)
            "win_ratio": round(win_ratio, 2),
            "profit_factor": round(pf, 2),
            "best_trade": round(best_trade, 2),
            "worst_trade": round(worst_trade, 2),
            "under_30s_count": len(under30),
            "under_30s_pct": round((len(under30) / len(closed) * 100.0) if closed else 0.0, 1),
        },
        "open_positions": open_positions,
        "under_30s_trades": under30[:20],
        "symbol_stats": symbol_stats,
        "recent_trades": list(reversed(closed))[:50],
        "equity_curve": curve[-100:],  # backward compat for old UI bits
    }
