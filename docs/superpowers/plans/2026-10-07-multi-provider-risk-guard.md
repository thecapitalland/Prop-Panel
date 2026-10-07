# Multi-Provider Risk Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a provider-independent prop-rule layer and a read-only pre-trade risk guard that previews current/open-SL/proposed-trade exposure without executing or blocking trades.

**Architecture:** Keep provider rules as data, keep breach math in pure Python, and isolate MT5-specific P/L-at-SL calculation behind a small adapter. Flask only orchestrates live state and exposes the preview API; the UI consumes the normalized response.

**Tech Stack:** Python 3.11+, Flask, MetaTrader5 Python package, vanilla JavaScript/Tailwind UI, unittest, GitHub Actions on `windows-latest`.

**Spec:** `docs/superpowers/specs/2026-10-07-multi-provider-risk-guard-design.md`

## Global Constraints

- Advisory/read-only only in this phase; no order placement, cancellation, or blocking.
- Provider rule values must carry `rules_version`, `source_url`, and `verified_at` metadata.
- A position without a usable stop loss must be surfaced as unbounded risk, never as zero risk.
- Existing Moneta dashboard behavior must remain backward compatible.
- Core risk functions must not import Flask or `MetaTrader5`.
- Provider profiles must be switchable without scattering provider-specific conditionals through the core math.
- `dashboard/terminals.json` becomes local-only; `dashboard/terminals.example.json` is committed.

## Review Focus

- Exact-limit boundaries: $0.01 below, exactly at, and $0.01 beyond a breach must produce stable statuses.
- Missing/zero stop loss: never convert unknown downside to `$0` risk; flag it as unbounded.
- Symbol calculation failure: preview must fail closed with a clear error rather than guessing pip/contract values.
- Provider/profile mismatch: unknown profile IDs must not silently fall back to another firm's rules.
- Existing users with legacy `prop_profile` config must keep working while the new `profile_id` path is introduced.

---

### Task 1: Provider Profile Registry and Backward-Compatible Normalization

**Files:**
- Create: `dashboard/provider_profiles.py`
- Create: `dashboard/test_provider_profiles.py`
- Modify: `dashboard/prop_metrics.py`
- Modify: `dashboard/test_dashboard.py`

**Interfaces:**
- Produces: `get_profile(profile_id: str) -> dict`
- Produces: `list_profiles() -> list[dict]`
- Produces: `normalize_profile(profile: dict | None = None, profile_id: str | None = None) -> dict`
- `build_prop_payload(...)` continues accepting `profile=` and gains normalized provider metadata in `program`.

- [ ] **Step 1: Write failing provider-profile tests**

Cover:
- `moneta_2step_phase1_5_10` resolves to the current 5%/10% compatibility profile.
- `sgb_plan_a_phase1` is representable with 5% daily / 12% max and provider metadata.
- `sgb_plan_b_phase1` is separately addressable.
- `custom` profile exists without provider-specific branches.
- unknown profile ID raises `ValueError`.
- legacy flat `prop_profile` values normalize without changing existing limits.

- [ ] **Step 2: Run profile tests and verify failure**

Run: `cd dashboard && python -m unittest test_provider_profiles.py -v`
Expected: FAIL because the registry/normalizer does not exist.

- [ ] **Step 3: Implement profile registry and normalization**

Use a small immutable-style registry in `provider_profiles.py`. Include rule provenance metadata and nested daily-loss semantics while exposing the existing flat compatibility keys (`daily_loss_limit_usd`, `max_loss_limit_usd`, etc.) consumed by `prop_metrics.py`.

- [ ] **Step 4: Update `build_prop_payload` to normalize profiles**

`prop_metrics.py` imports only the normalization helper; output `program` includes `profile_id`, `provider`, `program`, `phase`, `rules_version`, `source_url`, and `verified_at` while preserving current numeric fields.

- [ ] **Step 5: Run provider and existing metric tests**

Run: `cd dashboard && python -m unittest test_provider_profiles.py test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/provider_profiles.py dashboard/test_provider_profiles.py dashboard/prop_metrics.py dashboard/test_dashboard.py
git commit -m "feat: add provider profile registry"
```

---

### Task 2: Pure Risk Engine

**Files:**
- Create: `dashboard/risk_engine.py`
- Create: `dashboard/test_risk_engine.py`

**Interfaces:**
- Consumes normalized profile dictionaries from Task 1.
- Produces: `evaluate_risk(*, account: dict, profile: dict, open_sl_risk_usd: float, proposed_trade_risk_usd: float = 0.0, unbounded_positions: int = 0, personal_policy: dict | None = None, day_start_reference: float | None = None) -> dict`
- Produces statuses: `SAFE`, `WARNING`, `HIGH_RISK`, `BREACH`, plus `UNBOUNDED_RISK` flagging.

- [ ] **Step 1: Write boundary-first failing tests**

Tests pin:
- one cent below daily breach => not `BREACH`
- exactly at breach floor => `BREACH`
- one cent beyond => `BREACH`
- overall max-loss breach boundary
- open SL exposure increases projected usage
- proposed risk combines with open SL exposure
- personal stop can return `STOP` while provider remains `SAFE`
- unbounded position count is surfaced and prevents a false `SAFE` recommendation.

- [ ] **Step 2: Run risk tests and verify failure**

Run: `cd dashboard && python -m unittest test_risk_engine.py -v`
Expected: FAIL because `risk_engine.py` does not exist.

- [ ] **Step 3: Implement `evaluate_risk`**

Derive current daily/overall buffers from live equity and normalized limits; subtract known open/proposed downside to obtain projected worst-case equity. Keep threshold classification deterministic: warning 70%, high-risk 85%, breach 100% unless overridden by personal policy.

- [ ] **Step 4: Add failure-mode tests**

Cover negative risk inputs, missing/zero limits, and unbounded positions. Invalid negative exposure must raise `ValueError`; zero/absent limits must produce an explicit unavailable/error state rather than divide-by-zero.

- [ ] **Step 5: Run risk tests**

Run: `cd dashboard && python -m unittest test_risk_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/risk_engine.py dashboard/test_risk_engine.py
git commit -m "feat: add pure pre-trade risk engine"
```

---

### Task 3: MT5 Exposure Adapter

**Files:**
- Create: `dashboard/mt5_risk_adapter.py`
- Create: `dashboard/test_mt5_risk_adapter.py`
- Modify: `dashboard/app.py`

**Interfaces:**
- Produces: `position_sl_exposure(position: dict, profit_calculator) -> dict`
- Produces: `aggregate_open_sl_exposure(positions: list[dict], profit_calculator) -> dict`
- Produces: `proposed_trade_exposure(trade: dict, profit_calculator) -> dict`
- `profit_calculator` is injected for tests and wraps MT5 `order_calc_profit` in production.

- [ ] **Step 1: Write failing adapter tests with a fake calculator**

Cover BUY/SELL positions, multiple positions, missing SL -> unbounded, invalid volume, invalid side, and calculator failure -> explicit error.

- [ ] **Step 2: Run adapter tests and verify failure**

Run: `cd dashboard && python -m unittest test_mt5_risk_adapter.py -v`
Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement adapter without importing Flask**

Use current/entry price and SL only as inputs to the injected calculator. Do not implement home-grown pip/tick formulas.

- [ ] **Step 4: Add production wrapper in `app.py`**

Add `_mt5_profit_at_close(symbol, side, volume, price_open, price_close) -> float` using `mt5.order_calc_profit`; return/raise clear errors when symbol data or calculation is unavailable.

- [ ] **Step 5: Run adapter + existing Flask tests**

Run: `cd dashboard && python -m unittest test_mt5_risk_adapter.py test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/mt5_risk_adapter.py dashboard/test_mt5_risk_adapter.py dashboard/app.py
git commit -m "feat: add MT5 risk exposure adapter"
```

---

### Task 4: Risk Summary in Live Payload and Preview API

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/test_dashboard.py`

**Interfaces:**
- Adds `risk_guard` to successful `/api/data` payloads.
- Adds `POST /api/risk/preview`.
- Preview request fields: `symbol`, `side`, `entry`, `stop_loss`, `volume`.
- Preview response contains `ok`, `trade`, `existing_exposure`, `risk`, and `advisory_only: true`.

- [ ] **Step 1: Write failing Flask tests**

Cover:
- `/api/data` contains risk summary when live data is available.
- `/api/risk/preview` rejects missing symbol, bad side, volume <= 0, stop <= 0.
- preview never calls any order-send/trade mutation API.
- safe preview returns calculated proposed USD risk.
- calculator failure returns 400/explicit error and does not guess.

- [ ] **Step 2: Run Flask tests and verify failure**

Run: `cd dashboard && python -m unittest test_dashboard.py -v`
Expected: FAIL on new risk-preview assertions.

- [ ] **Step 3: Add shared live-state helper for risk evaluation**

Reuse the same account/profile/positions already gathered by `get_live_payload`; do not perform a second independent trading-state mutation. Bridge mode may return open-position summary but proposed-trade MT5 calculation requires available MT5 calculation capability; otherwise return a clear preview-unavailable reason.

- [ ] **Step 4: Implement `/api/risk/preview`**

Validate JSON input, call MT5 adapter for proposed exposure, aggregate open SL risk, call pure risk engine, and return advisory output only.

- [ ] **Step 5: Run Flask + risk tests**

Run: `cd dashboard && python -m unittest test_dashboard.py test_risk_engine.py test_mt5_risk_adapter.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/app.py dashboard/test_dashboard.py
git commit -m "feat: expose pre-trade risk preview API"
```

---

### Task 5: Pre-Trade Risk Guard UI

**Files:**
- Modify: `dashboard/templates/index.html`
- Modify: `dashboard/test_dashboard.py`

**Interfaces:**
- Consumes `risk_guard` from `/api/data`.
- Calls `POST /api/risk/preview` from the `Can I Take This Trade?` form.

- [ ] **Step 1: Add a lightweight template regression test**

Assert rendered `/` contains `Pre-Trade Risk Guard`, `Can I Take This Trade?`, and no text implying trade execution/blocking.

- [ ] **Step 2: Run template test and verify failure**

Run: `cd dashboard && python -m unittest test_dashboard.py -v`
Expected: FAIL until the UI exists.

- [ ] **Step 3: Add the Risk Guard card to Live Monitor**

Display provider/profile, daily buffer, max buffer, known SL exposure, unbounded-position warning, projected usage, safe additional risk, and advisory status.

- [ ] **Step 4: Add preview form and JavaScript**

Fields: symbol, BUY/SELL, entry, stop loss, volume. Submit to `/api/risk/preview`; render status and errors without reloading the page.

- [ ] **Step 5: Run template/API tests**

Run: `cd dashboard && python -m unittest test_dashboard.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dashboard/templates/index.html dashboard/test_dashboard.py
git commit -m "feat: add pre-trade risk guard UI"
```

---

### Task 6: Local Configuration Cleanup

**Files:**
- Create: `dashboard/terminals.example.json`
- Modify: `dashboard/app.py`
- Modify: `dashboard/test_dashboard.py`
- Modify: `.gitignore`
- Delete from tracking: `dashboard/terminals.json`

**Interfaces:**
- Runtime continues reading/writing `dashboard/terminals.json`.
- `load_config()` falls back to a sanitized example/default when the local file is absent.

- [ ] **Step 1: Write failing config tests**

Cover missing runtime config, loading sanitized defaults, and ensuring an existing local config remains writable/selectable.

- [ ] **Step 2: Run config tests and verify failure**

Run: `cd dashboard && python -m unittest test_dashboard.py -v`
Expected: FAIL on the new fallback behavior.

- [ ] **Step 3: Add sanitized example and ignore runtime file**

Move public defaults to `terminals.example.json`; add `dashboard/terminals.json` to `.gitignore`.

- [ ] **Step 4: Implement fallback behavior**

`load_config()` reads runtime config when present; otherwise reads the example/default without writing machine-specific paths until the user explicitly changes configuration.

- [ ] **Step 5: Run config + full dashboard tests**

Run: `cd dashboard && python -m unittest test_dashboard.py test_provider_profiles.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .gitignore dashboard/app.py dashboard/terminals.example.json dashboard/test_dashboard.py
git rm --cached dashboard/terminals.json
git commit -m "chore: make terminal configuration local-only"
```

---

### Task 7: Documentation, Changelog, and Full Verification

**Files:**
- Modify: `README.md`
- Create: `CHANGELOG.md`
- Modify: `.github/workflows/tests.yml` if needed to include new test modules explicitly.

**Interfaces:**
- Documents provider profiles, advisory-only Risk Guard, unbounded-SL semantics, and local configuration setup.

- [ ] **Step 1: Update README**

Document Moneta/SGB/custom profile architecture, `Pre-Trade Risk Guard`, `Can I Take This Trade?`, and the rule-source-of-truth disclaimer. State that Moneta 2-Step variants may differ by selected drawdown add-on and that provider terms remain authoritative.

- [ ] **Step 2: Add `CHANGELOG.md`**

Create an `Unreleased` section describing multi-provider profiles, pure risk engine, preview API/UI, and config cleanup.

- [ ] **Step 3: Update CI test command**

Ensure CI runs `test_provider_profiles.py`, `test_risk_engine.py`, `test_mt5_risk_adapter.py`, `test_dashboard.py`, and `test_tester_report.py`.

- [ ] **Step 4: Run the complete local-equivalent verification through GitHub Actions**

Push final commit and inspect the `windows-latest` workflow for the exact head SHA.
Expected: compile step PASS and all unit tests PASS with zero failures.

- [ ] **Step 5: Public-tree safety scan**

Search the current tree for personal account IDs, personal names, hard-coded secrets/tokens, and machine-specific user paths. Expected: no hits beyond intentionally public provider/trademark names and sanitized examples.

- [ ] **Step 6: Commit**

```bash
git add README.md CHANGELOG.md .github/workflows/tests.yml
git commit -m "docs: document multi-provider risk guard"
```

## Self-Review Result

- Spec coverage: all spec sections map to Tasks 1-7.
- Interfaces are consistent: profiles -> pure engine -> MT5 adapter -> Flask API -> UI.
- Risk calculation remains advisory and isolated from trade mutation paths.
- Backward compatibility is explicitly tested for legacy `prop_profile` config.
- Review-focus failure modes each have an owning task/test.
- No task requires a new framework, database, cloud service, or frontend rewrite.