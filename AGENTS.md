# AGENTS.md — continue this project safely

This file is for **Cursor / Codex / other coding agents** picking up the repo cold.

## Mission

Prop trading toolkit around Moneta Funded:

1. Live risk dashboard (Flask, port **63000**)
2. OLC basket EA (`OrderLevelControl_Prop_EA.mq5`)
3. History cache + Python optimizer (approximate)
4. Real Strategy Tester integration for the compiled EA

Owner preference: **wave-by-wave**; do not big-bang rewrite. Ask before mutating MetaTrader processes.

## Repo map (edit targets)

| Area | Primary files | Notes |
|------|----------------|-------|
| Live API | `dashboard/app.py`, `bridge_reader.py`, `prop_metrics.py` | Prefer EA bridge; cache live payload; threaded Flask |
| UI | `dashboard/templates/index.html` | Tabs: Live + Optimizer A/B |
| Python opt | `dashboard/optimizer.py` | Must run as **subprocess** via `/api/optimization/run` (never block Flask GIL) |
| History sync | `tools/sync_history.py` | Attach-only MT5; writes `History/` |
| Tester | `dashboard/tester_runner.py`, `tester_report.py` | ini/set + report parse |
| EA | `OrderLevelControl_Prop_EA.mq5` | Source of truth for live trading logic |
| Bridge | `MonetaDashboardBridge.mq5` | Read-only JSON exporter |

## Runtime facts

- Dashboard: `python dashboard/app.py` → `http://127.0.0.1:63000`
- Bridge path: `%APPDATA%\MetaQuotes\Terminal\Common\Files\moneta_bridge.json`
- Config: `dashboard/terminals.json` (local absolute MT5 paths — update after clone)
- Prop profile default: 50k Phase I ($2500 daily / $5000 max / $2500 PT / min 3 profitable days)
- Parquet under `History/**` is **gitignored** — run `python tools/sync_history.py` after clone
- Optimizer job status: `dashboard/opt_job_status.json` (gitignored)

## Hard rules

1. **Never** start/stop/kill `terminal64.exe` unless the user explicitly OK’s it.
2. Do **not** put Telegram tokens / secrets in committed files.
3. Mode A Python sim is **not** EA-equivalent; say so in UI/docs when changing optimizer.
4. Prefer bridge for Live; if falling back to MT5 API, keep attach-only and fail closed if process not running.
5. Keep Optimizer heavy work in a **separate process** (`optimizer.py` CLI).
6. Do not commit large `*.parquet`, `tester_runs/`, `tester_results/`, or live `optimization_results.json` dumps unless asked.

## How to verify after changes

```powershell
cd dashboard
python -m unittest test_dashboard.py test_tester_report.py -v
# Live smoke (terminal + bridge running):
# Invoke-RestMethod http://127.0.0.1:63000/api/data
# History:
python ..\tools\sync_history.py --update
```

## Current status (as of last push)

- Live + bridge path working; live metrics use pure-Python trade grouping + short cache
- History sync for EURUSD/GBPUSD/XAUUSD/EURCAD M5+H1
- Optimizer UI: symbol + Risk/TP/SL/Gap grids; top-N; detached process
- Tester UI Mode B: symbol/strategy/params → ini watch / optional headless if terminal closed
- **Deferred:** deeper optimizer quality / walk-forward / EA-faithful sim (user will ask)

## Suggested next waves (only when user asks)

1. Optimizer quality: prop daily/max filters, walk-forward, export top sets to Mode B one-click  
2. EA: implement real BE/partial/trail management (inputs exist but largely unused)  
3. Multi-profile challenge vs funded  
4. CI: unit tests on push (no MT5 required)

## Commit / PR hygiene

- Small focused commits; no force-push to `main` unless user asks  
- Do not amend pushed commits  
- Document machine-path changes in PR notes (`terminals.json`)
