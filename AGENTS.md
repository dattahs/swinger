# Swinger — Agent build instructions

**Source of truth for v1 implementation:** [`REQUIREMENTS_v1.md`](REQUIREMENTS_v1.md) (v1.3 — includes VPS live deployment in Section 0)

Build modules in dependency order (Section 3). Do not implement v2 items marked out of scope.

**Supporting docs (reference only, not build spec):**
- `PRD_Darvas_Trading_v4.md` — decision history and commentary
- `PRD_Darvas_Trading_v5.md`, `PRD_Darvas_Trading_v6.md` — condensed sketches; divergences resolved in REQUIREMENTS §0
- `BACKTEST_PLAN_Darvas_Trading_v1.md` — data ingest runbook
- `IMPLEMENTATION_PLAN_Backtest.md` — step-by-step laptop backtest build plan

**When implementing a module:** read its section in `REQUIREMENTS_v1.md`, implement under `src/` per Section 2 layout, add tests from Section 15.
