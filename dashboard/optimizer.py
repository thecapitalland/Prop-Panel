import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
RESULTS_FILE = os.path.join(_HERE, "optimization_results.json")
_TERMINALS_FILE = os.path.join(_HERE, "terminals.json")
_HISTORY_DIR = os.path.join(_ROOT, "History")
_META_FILE = os.path.join(_HISTORY_DIR, "meta.json")

# Prefer local cache; fall back to MT5 when missing/stale/unreadable
CACHE_STALE_HOURS = 48
DEFAULT_WINDOW_DAYS = 240


def _active_terminal_path():
    """Read selected terminal from terminals.json (never launches MT5)."""
    fallback = r"C:\Program Files\Moneta Funded MT5 Terminal\terminal64.exe"
    try:
        with open(_TERMINALS_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        active = cfg.get("active_id")
        for t in cfg.get("terminals", []):
            if t.get("id") == active:
                return t.get("path") or fallback
    except Exception:
        pass
    return fallback


def _parquet_path(symbol, tf):
    return os.path.join(_HISTORY_DIR, symbol, f"{tf}.parquet")


def _read_meta():
    if not os.path.isfile(_META_FILE):
        return None
    try:
        with open(_META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_fresh(symbol, max_age_hours=CACHE_STALE_HOURS):
    """Return True if History parquet + meta look usable for symbol."""
    m5 = _parquet_path(symbol, "M5")
    h1 = _parquet_path(symbol, "H1")
    if not (os.path.isfile(m5) and os.path.isfile(h1)):
        return False
    meta = _read_meta()
    if not meta:
        # Files exist without meta — treat as usable but not preferred forever
        return True
    series = (meta.get("series") or {}).get(symbol) or {}
    for tf in ("M5", "H1"):
        info = series.get(tf) or {}
        if not info.get("ok") or not info.get("bars"):
            return False
        to_s = info.get("to")
        if not to_s:
            return False
        try:
            to_dt = datetime.fromisoformat(to_s.replace("Z", "+00:00"))
            if to_dt.tzinfo is None:
                to_dt = to_dt.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - to_dt
            if age > timedelta(hours=max_age_hours):
                return False
        except Exception:
            return False
    return True


def _prepare_frames(df_m5, df_h1):
    df_m5 = df_m5.copy()
    df_h1 = df_h1.copy()
    if "time_dt" not in df_m5.columns:
        df_m5["time_dt"] = pd.to_datetime(df_m5["time"], unit="s")
    else:
        df_m5["time_dt"] = pd.to_datetime(df_m5["time_dt"])
    if getattr(df_m5["time_dt"].dt, "tz", None) is not None:
        df_m5["time_dt"] = df_m5["time_dt"].dt.tz_localize(None)

    if "time_dt" not in df_h1.columns:
        df_h1["time_dt"] = pd.to_datetime(df_h1["time"], unit="s")
    else:
        df_h1["time_dt"] = pd.to_datetime(df_h1["time_dt"])
    if getattr(df_h1["time_dt"].dt, "tz", None) is not None:
        df_h1["time_dt"] = df_h1["time_dt"].dt.tz_localize(None)

    df_m5["day_name"] = df_m5["time_dt"].dt.day_name()
    return df_m5, df_h1


def load_from_history(symbol):
    """Load M5/H1 from local History parquet. Returns (df_m5, df_h1) or (None, None)."""
    m5_path = _parquet_path(symbol, "M5")
    h1_path = _parquet_path(symbol, "H1")
    if not (os.path.isfile(m5_path) and os.path.isfile(h1_path)):
        return None, None
    try:
        df_m5 = pd.read_parquet(m5_path)
        df_h1 = pd.read_parquet(h1_path)
        if df_m5.empty or df_h1.empty:
            return None, None
        return _prepare_frames(df_m5, df_h1)
    except Exception as exc:
        print(f"WARN: History load failed for {symbol}: {exc}")
        return None, None


def fetch_symbol_data_mt5(symbol, days=DEFAULT_WINDOW_DAYS):
    """Fallback: pull from attached MT5 terminal (end=now, not a fixed date)."""
    if not mt5.initialize(path=_active_terminal_path()):
        print(f"WARN: mt5.initialize failed: {mt5.last_error()}")
        return None, None

    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)

    rates_m5 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, from_date, to_date)
    rates_h1 = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_H1, from_date - timedelta(days=10), to_date)
    mt5.shutdown()

    if rates_m5 is None or rates_h1 is None:
        return None, None

    df_m5 = pd.DataFrame(rates_m5)
    df_h1 = pd.DataFrame(rates_h1)
    return _prepare_frames(df_m5, df_h1)


def load_symbol_data(symbol, days=DEFAULT_WINDOW_DAYS, prefer_cache=True):
    """Prefer local History parquet; fall back to MT5 if missing/stale/unreadable."""
    if prefer_cache and _cache_fresh(symbol):
        df_m5, df_h1 = load_from_history(symbol)
        if df_m5 is not None:
            print(f"[{symbol}] loaded from History cache")
            return df_m5, df_h1, "history"

    # Try cache even if meta says stale (better than nothing if MT5 fails)
    df_m5, df_h1 = load_from_history(symbol)
    if df_m5 is not None and prefer_cache:
        print(f"[{symbol}] History present but stale/incomplete — trying MT5 refresh")
    else:
        print(f"[{symbol}] History miss — fetching from MT5")

    m5_mt5, h1_mt5 = fetch_symbol_data_mt5(symbol, days=days)
    if m5_mt5 is not None:
        print(f"[{symbol}] loaded from MT5")
        return m5_mt5, h1_mt5, "mt5"

    if df_m5 is not None:
        print(f"[{symbol}] MT5 failed; using stale History cache")
        return df_m5, df_h1, "history_stale"

    print(f"[{symbol}] no data available")
    return None, None, "none"


# Back-compat alias used by older call sites
def fetch_symbol_data(symbol, days=DEFAULT_WINDOW_DAYS):
    df_m5, df_h1, _src = load_symbol_data(symbol, days=days)
    return df_m5, df_h1


def simulate_fast(symbol, df_m5, df_h1, strategy, risk_pct, profit_x, sl_gap, level_gap, day_filter):
    initial_balance = 50000.0
    balance = initial_balance
    peak_balance = initial_balance
    max_dd_pct = 0.0
    trades = []

    asian_ranges = {}
    if strategy == "Asian Sweep":
        for idx, row in df_h1.iterrows():
            dt = row["time_dt"]
            if dt.hour == 7:
                day_str = dt.strftime("%Y-%m-%d")
                asian_bars = df_h1[(df_h1["time_dt"] >= dt - timedelta(hours=7)) & (df_h1["time_dt"] < dt)]
                if not asian_bars.empty:
                    asian_ranges[day_str] = {
                        "high": asian_bars["high"].max(),
                        "low": asian_bars["low"].min(),
                    }

    ny_ranges = {}
    if strategy == "NY ORB":
        for idx, row in df_h1.iterrows():
            dt = row["time_dt"]
            if dt.hour == 13 and dt.minute == 45:
                day_str = dt.strftime("%Y-%m-%d")
                ny_bars = df_m5[(df_m5["time_dt"] >= dt - timedelta(minutes=15)) & (df_m5["time_dt"] < dt)]
                if not ny_bars.empty:
                    ny_ranges[day_str] = {
                        "high": ny_bars["high"].max(),
                        "low": ny_bars["low"].min(),
                    }

    sub_asian = asian_ranges.copy()
    sub_ny = ny_ranges.copy()

    for i in range(2, len(df_m5) - 120):
        bar = df_m5.iloc[i]
        prev_bar = df_m5.iloc[i - 1]
        dt = bar["time_dt"]
        day_str = dt.strftime("%Y-%m-%d")
        day_name = bar["day_name"]

        if day_filter == "No Friday" and day_name == "Friday":
            continue
        if day_filter == "Tue-Thu Only" and day_name not in ["Tuesday", "Wednesday", "Thursday"]:
            continue

        if strategy == "Asian Sweep" and day_str in sub_asian and (dt.hour >= 7 and dt.hour <= 10):
            a_high = sub_asian[day_str]["high"]
            a_low = sub_asian[day_str]["low"]

            if prev_bar["low"] < a_low and bar["close"] > a_low and bar["close"] > bar["open"]:
                entry1 = bar["close"]
                entry2 = entry1 - level_gap
                sl_price = entry2 - sl_gap
                risk_amount = balance * (risk_pct / 100.0)

                l2_filled = False
                pnl = 0
                for j in range(i + 1, min(i + 120, len(df_m5))):
                    fbar = df_m5.iloc[j]
                    if not l2_filled and fbar["low"] <= entry2:
                        l2_filled = True
                    if fbar["low"] <= sl_price:
                        pnl = -risk_amount if l2_filled else -(risk_amount * 0.5)
                        break
                    if not l2_filled and fbar["high"] >= entry1 + profit_x:
                        pnl = risk_amount * (profit_x / sl_gap)
                        break
                    if l2_filled and fbar["high"] >= entry1:
                        pnl = risk_amount * 0.8
                        break
                balance += pnl
                trades.append(pnl)
                del sub_asian[day_str]

            elif prev_bar["high"] > a_high and bar["close"] < a_high and bar["close"] < bar["open"]:
                entry1 = bar["close"]
                entry2 = entry1 + level_gap
                sl_price = entry2 + sl_gap
                risk_amount = balance * (risk_pct / 100.0)

                l2_filled = False
                pnl = 0
                for j in range(i + 1, min(i + 120, len(df_m5))):
                    fbar = df_m5.iloc[j]
                    if not l2_filled and fbar["high"] >= entry2:
                        l2_filled = True
                    if fbar["high"] >= sl_price:
                        pnl = -risk_amount if l2_filled else -(risk_amount * 0.5)
                        break
                    if not l2_filled and fbar["low"] <= entry1 - profit_x:
                        pnl = risk_amount * (profit_x / sl_gap)
                        break
                    if l2_filled and fbar["low"] <= entry1:
                        pnl = risk_amount * 0.8
                        break
                balance += pnl
                trades.append(pnl)
                del sub_asian[day_str]

        elif strategy == "NY ORB" and day_str in sub_ny and (dt.hour >= 13 and dt.hour <= 15):
            n_high = sub_ny[day_str]["high"]
            n_low = sub_ny[day_str]["low"]

            if prev_bar["close"] <= n_high and bar["close"] > n_high:
                entry1 = bar["close"]
                entry2 = entry1 - level_gap
                sl_price = entry2 - sl_gap
                risk_amount = balance * (risk_pct / 100.0)

                l2_filled = False
                pnl = 0
                for j in range(i + 1, min(i + 120, len(df_m5))):
                    fbar = df_m5.iloc[j]
                    if not l2_filled and fbar["low"] <= entry2:
                        l2_filled = True
                    if fbar["low"] <= sl_price:
                        pnl = -risk_amount if l2_filled else -(risk_amount * 0.5)
                        break
                    if not l2_filled and fbar["high"] >= entry1 + profit_x:
                        pnl = risk_amount * (profit_x / sl_gap)
                        break
                    if l2_filled and fbar["high"] >= entry1:
                        pnl = risk_amount * 0.8
                        break
                balance += pnl
                trades.append(pnl)
                del sub_ny[day_str]

            elif prev_bar["close"] >= n_low and bar["close"] < n_low:
                entry1 = bar["close"]
                entry2 = entry1 + level_gap
                sl_price = entry2 + sl_gap
                risk_amount = balance * (risk_pct / 100.0)

                l2_filled = False
                pnl = 0
                for j in range(i + 1, min(i + 120, len(df_m5))):
                    fbar = df_m5.iloc[j]
                    if not l2_filled and fbar["high"] >= entry2:
                        l2_filled = True
                    if fbar["high"] >= sl_price:
                        pnl = -risk_amount if l2_filled else -(risk_amount * 0.5)
                        break
                    if not l2_filled and fbar["low"] <= entry1 - profit_x:
                        pnl = risk_amount * (profit_x / sl_gap)
                        break
                    if l2_filled and fbar["low"] <= entry1:
                        pnl = risk_amount * 0.8
                        break
                balance += pnl
                trades.append(pnl)
                del sub_ny[day_str]

        if balance > peak_balance:
            peak_balance = balance
        dd = (peak_balance - balance) / peak_balance * 100
        if dd > max_dd_pct:
            max_dd_pct = dd

    tot_trades = len(trades)
    wins = len([t for t in trades if t > 0])
    gross_profit = sum([t for t in trades if t > 0])
    gross_loss = abs(sum([t for t in trades if t < 0]))

    win_rate = (wins / tot_trades * 100) if tot_trades > 0 else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
    net_profit = balance - initial_balance
    net_profit_pct = (net_profit / initial_balance) * 100

    score = (net_profit_pct * profit_factor) / (max_dd_pct + 0.1)

    return {
        "symbol": symbol,
        "strategy": strategy,
        "risk_pct": risk_pct,
        "profit_x": profit_x,
        "sl_gap": sl_gap,
        "level_gap": level_gap,
        "day_filter": day_filter,
        "total_trades": tot_trades,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "net_profit_usd": round(net_profit, 2),
        "net_profit_pct": round(net_profit_pct, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "score": round(score, 2),
    }


def _default_grids(symbol: str):
    is_gold = "XAU" in symbol.upper() or "GOLD" in symbol.upper()
    strategies = ["NY ORB"] if is_gold else ["Asian Sweep"]
    risk_pcts = [0.30, 0.50]
    day_filters = ["All Days", "No Friday"]
    if is_gold:
        profit_xs = [8.0, 10.0, 12.0]
        sl_gaps = [7.0, 10.0]
        level_gaps = [5.0]
    else:
        profit_xs = [0.0020, 0.0030, 0.0040]
        sl_gaps = [0.0015, 0.0020]
        level_gaps = [0.0010]
    return strategies, risk_pcts, profit_xs, sl_gaps, level_gaps, day_filters


def optimize_symbol(
    symbol: str,
    *,
    risk_pcts=None,
    profit_xs=None,
    sl_gaps=None,
    level_gaps=None,
    day_filters=None,
    top_n: int = 50,
    min_trades: int = 5,
):
    """Sweep one symbol; returns (rows, data_source, stats)."""
    df_m5, df_h1, src = load_symbol_data(symbol)
    if df_m5 is None:
        return [], src, {"error": "no data", "combos": 0, "qualified": 0}

    d_strat, d_risk, d_tp, d_sl, d_gap, d_filt = _default_grids(symbol)
    strategies = d_strat
    risk_pcts = risk_pcts if risk_pcts is not None else d_risk
    profit_xs = profit_xs if profit_xs is not None else d_tp
    sl_gaps = sl_gaps if sl_gaps is not None else d_sl
    level_gaps = level_gaps if level_gaps is not None else d_gap
    day_filters = day_filters if day_filters is not None else d_filt
    top_n = max(1, int(top_n or 50))

    combos = (
        len(strategies) * len(risk_pcts) * len(profit_xs)
        * len(sl_gaps) * len(level_gaps) * len(day_filters)
    )
    sym_results = []
    for strat in strategies:
        for r_pct in risk_pcts:
            for px in profit_xs:
                for slg in sl_gaps:
                    for lg in level_gaps:
                        for df in day_filters:
                            res = simulate_fast(
                                symbol, df_m5, df_h1, strat, r_pct, px, slg, lg, df
                            )
                            if res["total_trades"] >= min_trades:
                                sym_results.append(res)

    qualified = len(sym_results)
    sym_results = sorted(sym_results, key=lambda x: x["score"], reverse=True)[:top_n]
    stats = {
        "combos": combos,
        "qualified": qualified,
        "kept": len(sym_results),
        "top_n": top_n,
        "min_trades": min_trades,
        "grid_sizes": {
            "risk": len(risk_pcts),
            "tp": len(profit_xs),
            "sl": len(sl_gaps),
            "gap": len(level_gaps),
            "filter": len(day_filters),
        },
    }
    return sym_results, src, stats


def run_symbol_optimization(symbol: str, **grid_kwargs):
    """Optimize one symbol and merge into optimization_results.json."""
    symbol = symbol.upper().strip()
    top_n = int(grid_kwargs.pop("top_n", 50) or 50)
    min_trades = int(grid_kwargs.pop("min_trades", 5) or 5)
    rows, src, stats = optimize_symbol(
        symbol, top_n=top_n, min_trades=min_trades, **grid_kwargs
    )

    all_results = {}
    if os.path.isfile(RESULTS_FILE):
        try:
            with open(RESULTS_FILE, "r", encoding="utf-8") as f:
                all_results = json.load(f)
        except Exception:
            all_results = {}

    sources = all_results.get("data_sources") or {}
    sources[symbol] = src
    all_results[symbol] = rows
    all_results["data_sources"] = sources
    all_results["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_results["last_run"] = {
        "symbol": symbol,
        "grid": {k: v for k, v in grid_kwargs.items() if v is not None},
        "source": src,
        "stats": stats,
    }

    # Full qualified list (optional, capped) for download — separate file
    full_path = os.path.join(os.path.dirname(RESULTS_FILE), f"opt_full_{symbol}.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "stats": stats, "rows": rows}, f, indent=2)

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print(
        f"Optimization Complete for {symbol}! src={src} "
        f"combos={stats['combos']} qualified={stats['qualified']} kept={stats['kept']}"
    )
    return all_results


def run_full_optimization():
    symbols = ["EURUSD", "GBPUSD", "XAUUSD", "EURCAD"]
    all_results = {}
    sources = {}

    for sym in symbols:
        rows, src, _stats = optimize_symbol(sym, top_n=10)
        sources[sym] = src
        if rows:
            all_results[sym] = rows
        elif src:
            all_results[sym] = []

    all_results["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    all_results["data_sources"] = sources

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)

    print("Optimization Complete! Saved to optimization_results.json")
    print(f"Data sources: {sources}")
    for sym, rows in all_results.items():
        if sym in ("updated_at", "data_sources", "last_run"):
            continue
        if rows:
            top = rows[0]
            print(
                f"  {sym} top score={top['score']} trades={top['total_trades']} "
                f"net%={top['net_profit_pct']} src={sources.get(sym)}"
            )
    return all_results


OPT_JOB_STATUS = os.path.join(_HERE, "opt_job_status.json")


def _write_job_status(payload: dict) -> None:
    payload = {**payload, "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    with open(OPT_JOB_STATUS, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def main_cli(argv=None):
    """CLI entry for subprocess runs (keeps Flask responsive)."""
    import argparse

    p = argparse.ArgumentParser(description="OLC Python optimizer (History cache)")
    p.add_argument("--symbol", default="", help="Single symbol, empty = all")
    p.add_argument("--risk", default="", help="Comma-separated risk %")
    p.add_argument("--tp", default="", help="Comma-separated profit_x")
    p.add_argument("--sl", default="", help="Comma-separated SL gaps")
    p.add_argument("--gap", default="", help="Comma-separated level gaps")
    p.add_argument("--top-n", type=int, default=50)
    args = p.parse_args(argv)

    def _nums(s):
        import re
        if not s:
            return None
        found = re.findall(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", s)
        return [float(x) for x in found] if found else None

    _write_job_status({
        "status": "running",
        "symbol": (args.symbol or "ALL").upper(),
        "pid": os.getpid(),
        "error": None,
    })
    try:
        if args.symbol:
            run_symbol_optimization(
                args.symbol.upper().strip(),
                risk_pcts=_nums(args.risk),
                profit_xs=_nums(args.tp),
                sl_gaps=_nums(args.sl),
                level_gaps=_nums(args.gap),
                top_n=args.top_n,
            )
        else:
            run_full_optimization()
        _write_job_status({
            "status": "done",
            "symbol": (args.symbol or "ALL").upper(),
            "pid": os.getpid(),
            "error": None,
        })
    except Exception as exc:
        _write_job_status({
            "status": "failed",
            "symbol": (args.symbol or "ALL").upper(),
            "pid": os.getpid(),
            "error": str(exc),
        })
        raise


if __name__ == "__main__":
    main_cli()
