---
project_name: 'Swinger'
user_name: 'Datta'
date: '2026-06-25'
sections_completed:
  - technology_stack
  - language_rules
  - framework_rules
  - testing_rules
  - quality_rules
  - workflow_rules
  - anti_patterns
status: complete
rule_count: 42
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

| Layer | Choice |
|-------|--------|
| Language | Python 3.x (3.11+ recommended; avoid 3.14-only APIs without guard) |
| Config / types | Pydantic v2 (`pydantic>=2.0`), PyYAML (`pyyaml>=6.0`) |
| Data | pandas `>=2.0`, numpy `>=1.24`, pyarrow `>=14.0`, SQLite |
| HTTP / retry | requests `>=2.31`, tenacity `>=8.2` |
| NSE data | jugaad-data `>=0.24` |
| Tests | pytest `>=7.0` (`pythonpath = .`, `testpaths = tests`) |
| Live auth | Playwright `>=1.40`, pyotp `>=2.9` (`requirements-live.txt`) |
| Broker | Upstox v3 GTT REST (default); Zerodha Kite optional |
| Live deploy | **VPS** — cron/systemd → `scripts/run_live.py` (not AWS Lambda) |
| Live persistence | SQLite at `live.local_db_path` (`SqliteLiveRepository`) |
| Backtest persistence | SQLite at `backtest.data_db_path` (`SqliteDataLake`) |
| Spec authority | `REQUIREMENTS_v1.md` v1.3 — not PRD v4/v5/v6 sketches |

---

## Critical Implementation Rules

### Language-Specific Rules

- Use `from __future__ import annotations` in all new `src/` modules.
- Imports are always absolute: `from src.<package> import ...` — never relative imports.
- Module docstrings cite the binding REQUIREMENTS section (e.g. `REQUIREMENTS v1.3 Section 9`).
- Use Pydantic v2 models in `src/config.py` and `src/models.py`; validate via `AppConfig.model_validate`.
- Prefer `load_config()` for live paths; use `load_config_relaxed()` only for backtest/optimization scripts that skip auth ARN validation.
- Dates in config YAML are ISO strings parsed to `datetime.date` in `config.py`.
- Do not use `lookback_years_for_doubling` — deprecated; use `lookback_years_for_52wk_high`.

### Framework-Specific Rules

**Strategy engine (core invariant)**

- `run_daily_strategy_iteration()` in `src/engine/engine.py` is shared by backtest and live — never fork strategy logic per environment.
- Kill switch is evaluated in `LiveRunner.run()` / backtester day loop, **not** inside the engine.
- `TRAIL_OCO` emits only when `Risk_pct ≤ trailing_stop.max_trail_risk_pct` (default 10%).
- Structural R filter runs before ranking: reject when `structural_rr < min_structural_r_ratio`.
- GTT pricing: `entry_price = box_top`; `trigger = box_top + gtt_trigger_buffer_inr`; `stop = box_bottom - stop_loss_buffer_fraction_inr`.

**Repository**

- Implement against `Repository` ABC (`src/repository/base.py`).
- Live v1: `SqliteLiveRepository` — not DynamoDB (deferred v2).
- Backtest: `SqliteBacktestRepository` + `SqliteDataLake` on `backtest.data_db_path`.
- PIT fundamentals join: `effective_date = next_trading_session_after(submission_date)`; no lookahead.

**Live VPS**

- Entry point: `scripts/run_live.py` → `LiveRunner` — not `lambda_handler.py` (deprecated stub).
- `system.storage.live_backend` must be `sqlite` for v1; reject `s3`.
- Register VPS public IP in Upstox developer console (`system.networking.vps_public_ip`).
- Use `flock` or systemd timer to prevent overlapping 16:30 runs.
- Secrets live in `.env` on the host (see `.env.example`); never commit credentials.

**Broker**

- Default provider: `upstox`. GTT client in `src/broker/upstox.py`.
- Idempotency keys on every order action: `make_idempotency_key(symbol, date, action_type)`.
- Transient broker errors: retry ×3 with backoff; persistent failures log + alert, do not mark placed.
- `discretionary` ↔ `manual_daily_login`; `fully_automated` ↔ `totp_automated_login` — reject mismatches at config load.

### Testing Rules

- Tests live in `tests/test_*.py`; reference REQUIREMENTS Section 15 acceptance criteria in module docstring where applicable.
- Run from repo root: `python -m pytest tests/` (requires venv with `requirements.txt` installed).
- Unit tests build minimal config via `AppConfig.model_construct()` helpers — do not require full `config.yaml` unless integration test.
- Use `load_config_relaxed()` + `seed_demo_data()` for parity/integration tests (`test_parity.py` pattern).
- Mock broker and repository interfaces — do not hit live Upstox in unit tests.
- New engine logic needs tests for: Darvas transitions, sizing caps, structural R gate, TRAIL 10% gate, PIT lookahead.

### Code Quality & Style Rules

- Minimize scope: smallest correct diff; no unrelated refactors.
- Match existing naming: snake_case files/functions, PascalCase Pydantic models and enums (`BoxStateEnum`).
- No over-abstraction — inline one-liners rather than micro-helpers.
- Comments only for non-obvious business logic (box state rules, PIT discipline, broker edge cases).
- Build modules in REQUIREMENTS §3 order (M1→M9); do not implement v2 scope (dashboard, intraday kill-switch, LLM advisor, Aurora).
- Scripts set `ROOT` and insert into `sys.path` before importing `src` (see `scripts/run_live.py`).

### Development Workflow Rules

- **Source of truth:** `REQUIREMENTS_v1.md` v1.3 > `AGENTS.md` > PRD history files.
- Supporting runbooks: `BACKTEST_PLAN_Darvas_Trading_v1.md` (data ingest), `IMPLEMENTATION_PLAN_Backtest.md` (M1–M5 laptop build).
- Backtest outputs → `backtest_outputs/` (timestamped runs when `timestamped_runs: true`).
- Optimization experiments log to `src/agentic-loop/` — do not auto-apply config changes from advisor/optimization without explicit user approval.
- Live paper mode: `live.paper_mode: true` until rollout gates in REQUIREMENTS §16 pass.
- Commits only when user requests; no force-push to main.

### Critical Don't-Miss Rules

**Do NOT**

- Deploy live to AWS Lambda or use DynamoDB for v1 (stubs exist for v2 only).
- Use SQLite-on-S3/EFS pull-push for live state (replaced by VPS local disk).
- Use broker API snapshots as primary fundamentals — must be `nse_official_xbrl_pit`.
- Emit MARKET orders without `market_protection`; GTT legs use explicit limit/trigger prices.
- Lower stop-loss on open positions when box resets to SCANNING.
- Partial-size entries when settled cash is insufficient — skip entirely (`all-or-nothing`).
- Implement v2 items: web dashboard, per-trade approve UI, intraday polling, LLM narrative advisor, `ALL_NSE` universe.

**Edge cases agents must handle**

- Box in BREAKOUT with price far above `box_top`: `breakout_reset_above_top_pct` resets to SCANNING (optimal: 4.0 per optimization-results).
- Adaptive lookback: when `adaptive_new_high_lookback.enabled`, ignore fixed `new_high_lookback_weeks` for the gate.
- `ESTABLISH_OCO` failure after confirmed fill → `oco_pending_review=true` + alert; no duplicate buys on retry.
- Missing OHLCV/fundamentals for a symbol → skip symbol, continue run (never abort full pipeline).
- Trend filter failure during FORMING/VALIDATED → freeze state, do not reset to SCANNING.

**Module gaps (v1 still open)**

- M8: `src/notify/telegram.py` not yet implemented (`backtest_email.py` exists for backtest only).
- M9: `src/advisor/advisor.py` optional last.

---

## Usage Guidelines

**For AI Agents**

- Read this file and `REQUIREMENTS_v1.md` before implementing any module.
- Follow ALL rules exactly; when in doubt, prefer the more restrictive option.
- Align live changes with `LiveRunner` + SQLite VPS path — not Lambda.
- Update this file when stack or deployment model changes.

**For Humans**

- Keep lean — only unobvious rules belong here.
- Review after major spec changes (e.g. v1.3 VPS pivot).
- Copy or symlink to `docs/project-context.md` if agents scan `project_knowledge` path.

Last Updated: 2026-06-25
