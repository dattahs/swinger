# Swinger — Agent build instructions

**Source of truth for v1 implementation:** [`REQUIREMENTS_v1.md`](REQUIREMENTS_v1.md) (v1.3 — includes VPS live deployment in Section 0)

Build modules in dependency order (Section 3). Do not implement v2 items marked out of scope.

**Supporting docs (reference only, not build spec):**
- `PRD_Darvas_Trading_v4.md` — decision history and commentary
- `PRD_Darvas_Trading_v5.md`, `PRD_Darvas_Trading_v6.md` — condensed sketches; divergences resolved in REQUIREMENTS §0
- `BACKTEST_PLAN_Darvas_Trading_v1.md` — data ingest runbook
- `IMPLEMENTATION_PLAN_Backtest.md` — step-by-step laptop backtest build plan

**When implementing a module:** read its section in `REQUIREMENTS_v1.md`, implement under `src/` per Section 2 layout, add tests from Section 15.

## Cursor Cloud specific instructions

Python-only project (no Node/Docker). Dependencies are installed into `.venv` by the startup update script (`requirements.txt` + `requirements-live.txt`). Activate it before any work: `source .venv/bin/activate`.

- **Tests:** `python -m pytest -q` (config in `pytest.ini`; `pythonpath=.`, tests in `tests/`). As of this setup, 3 tests fail on a fresh checkout and are unrelated to the environment: `tests/test_debug_log.py::test_make_run_output_dir_timestamped` (relies on second-resolution timestamps differing between two fast calls) and two `tests/test_virtual_broker.py` target-fill cases. The other ~75 tests pass.
- **Lint/build:** no linter (ruff/flake8/mypy) or build step is configured; there is nothing to "build". For a quick sanity check use `python -m compileall -q src scripts`.
- **Run the app (backtest):** `python scripts/run_backtest.py --config config.yaml`. The default `config.yaml` window (2018–2026, NIFTY 500) needs an ingested data lake at `backtest.data_db_path` (`./data/processed/swinger_data.db`), which is NOT in the repo. For a self-contained smoke run that needs no real data, use `python scripts/run_backtest.py --seed-demo --no-email` (the seed data intentionally produces 0 trades because its turnover is below the universe filter). Outputs go to `./backtest_outputs/run_<timestamp>/` (gitignored).
- **Data ingest** (`scripts/ingest_all.py`, `scripts/download_*.py`) hits live NSE/Upstox endpoints and needs network + credentials; it is not part of routine local dev.
- **Live runner** (`scripts/run_live.py`, `scripts/run_mock_live.py`) needs broker credentials from `.env` (copy `.env.example`); `live.paper_mode: true` keeps it from placing real orders.
- Real-data backtests/live runs require the `.env` secrets and broker IP whitelisting described in `REQUIREMENTS_v1.md` Section 9; these are not available by default in this environment.
