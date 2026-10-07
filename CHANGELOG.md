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

### Changed
- Terminal configuration is now local-only via `dashboard/terminals.json`.
- Public configuration ships as `dashboard/terminals.example.json`.
- CI discovers all `test_*.py` modules automatically.
- Prop metrics now normalize provider profiles before calculation.

### Safety
- Risk preview remains read-only and advisory.
- No trade placement, cancellation, modification, or blocking was added.
- Provider terms and official account dashboards remain the source of truth.