# Changelog

All notable changes to Prop-Panel are documented here.

## Unreleased

### Added
- Multi-provider rule profile registry with Moneta Funded, SGB, and Custom profiles.
- Version/source metadata for provider rules.
- Pure Python pre-trade risk engine with explicit boundary behavior.
- MT5 exposure adapter using contract-aware profit calculation rather than hard-coded pip formulas.
- Advisory-only `POST /api/risk/preview` endpoint.
- `Pre-Trade Risk Guard` and `Can I Take This Trade?` UI.
- Personal daily-stop policy layer independent of provider breach limits.
- Automatic detection of open positions without usable stop losses as unbounded risk.
- Provider profile listing and selection endpoints.
- SGB aggregate concurrent-risk limits with account-size tiers.
- SGB per-symbol 2% concurrent-risk rule with explicit user-supplied provenance metadata.
- Symbol normalization for SGB `.X` suffixes.
- Maximum safe lot sizing using MT5 contract P/L and broker volume step/min/max.
- Binding-constraint reporting and optional personal execution buffer.

### Changed
- Terminal configuration is now local-only via `dashboard/terminals.json`.
- Public configuration ships as `dashboard/terminals.example.json`.
- CI discovers all `test_*.py` modules automatically.
- Prop metrics now normalize provider profiles before calculation.
- Moneta Profit Target now uses realized trading P/L only and excludes floating P/L.
- Moneta minimum profitable days now require the configured 0.5% daily closed-profit threshold.
- Trading-day bucketing now uses provider UTC reset rules when available, avoiding workstation-timezone drift.
- Provider compliance, exposure certainty, and trader recommendation are reported separately.
- SGB current floating loss and projected loss-at-stop are evaluated separately so no-SL positions can still surface current rule breaches.

### Safety
- Risk preview remains read-only and advisory.
- No trade placement, cancellation, modification, or blocking was added.
- Provider terms and official account dashboards remain the source of truth.