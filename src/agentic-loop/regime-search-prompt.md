# Agent prompt: Darvas parameter search with regime-analog validation

## Mission

Run an iterative backtest experiment on the Swinger Darvas Box strategy. Each round:

1. Screen a parameter grid on a primary window.
2. Validate the top 3 candidates on historical **index-candle** and **India VIX** analog periods.
3. Promote consistent winners to a `configs/winners/` folder.
4. Mutate parameters around winners and repeat until no new config beats the incumbent winners on the primary window **and** passes analog consistency.

Do **not** change `config.yaml` or promote a winner to production without explicit human approval. All experiment configs live under `configs/experiments/` and `configs/winners/`.

---

## Prerequisites

1. Confirm `data/processed/swinger_data.db` exists and covers at least `2017-01-01 → 2026-05-31`.
2. Confirm India VIX cache exists or download via `src/data/vix_data.py` (`load_or_download_vix`).
3. Use `config.yaml` as the base template (current optimal: `adaptive_new_high_lookback.sma_period: 80`, `breakout_reset_above_top_pct: 4.0`, `min_box_duration_days: 4`).
4. Disable email and verbose logging on all experiment runs (`send_email_on_complete: false`, `progress_log.enabled: false`, `debug_log.enabled: false`).
5. Reuse patterns from `scripts/run_optimization_batch.py` (`apply_darvas_algo_overrides`, JSONL logging) and `scripts/run_vix_analog_backtests.py` (VIX analog discovery).

---

## Definitions

| Term | Value |
|---|---|
| **Primary screening window** | `2024-06-01` → `2026-05-31` (~2 years; matches prior optimization work) |
| **Analog window length** | **18 calendar months** of trading sessions (~378 sessions; use actual session count from data, not a fixed 252) |
| **Reference “current” period** | Last 18 months ending `2026-05-31`: `2024-12-01` → `2026-05-31` (adjust if data end differs) |
| **Index for candle matching** | `NIFTY 50` daily OHLC from `swinger_data.db` |
| **VIX series** | India VIX from `data/processed/india_vix_daily.csv` |
| **Feasible** | `max_drawdown_pct ≤ 10.0` |
| **Screening score** | `score = cagr` if feasible, else `-1` |
| **Ranking** | Sort by `score` desc, then `max_drawdown_pct` asc, then `total_closed_trades` desc |

**Consistency rule (both analog tests):** A config is **consistent** on a set of 3 windows if **all three** satisfy:

- `cagr ≥ 0` (non-negative)
- `max_drawdown_pct ≤ 8.0` (stricter than feasibility gate)
- CAGR ranks in the **top 2 of the 3 candidates** on that window (relative consistency)

A config is a **round winner** only if it is consistent on **both** the index-candle analog set **and** the VIX analog set.

---

## Folder layout (create if missing)

```
configs/
  experiments/
    round-01/
      <config-name>.yaml
  winners/
    round-01/
      <config-name>.yaml
      <config-name>-summary.md
backtest_outputs/
  experiments/
    round-01/
      screening/
      index-analogs/
      vix-analogs/
src/agentic-loop/
  regime-search-log.jsonl      # append-only, one JSON object per run
  regime-search-results.md     # human-readable rolling summary
```

Each `*-summary.md` winner file must include: parameter overrides, primary-window metrics, all 6 analog-window metrics (3 index + 3 VIX), consistency verdict, and path to `summary_report.json`.

---

## Phase 0 — Build index candle matcher (if not present)

Mirror `src/analysis/vix_curve_match.py`:

- Create `src/analysis/index_curve_match.py` with `find_index_analogs()` using the same composite score: z-scored Pearson on close/returns/range/body + DTW on closes.
- Input: NIFTY 50 daily bars, reference window, `top_k=3`, non-overlapping candidates ending strictly before the reference start.
- For each analog window `[analog_start, analog_end]`, the **backtest window** is the **same calendar dates shifted forward 18 months** (not +1 year; this differs from the VIX script’s +1y shift — index analogs use +18mo to match the 18-month study horizon).
- Add `scripts/run_index_analog_backtests.py` modeled on `scripts/run_vix_analog_backtests.py`.

---

## Phase 1 — Screening grid (Round N)

**Round 1 seed grid** (7–12 configs; one YAML per config under `configs/experiments/round-01/`):

| Config name | Key overrides (on top of `config.yaml`) |
|---|---|
| `baseline` | none |
| `sma30-reset4` | `adaptive_new_high_lookback.sma_period: 30` |
| `sma80-reset4` | `adaptive_new_high_lookback.sma_period: 80` |
| `reset-tight-0.5` | `breakout_reset_above_top_pct: 0.5` |
| `reset-loose-4.0` | `breakout_reset_above_top_pct: 4.0` |
| `box-dur-5` | `min_box_duration_days: 5` |
| `box-height-4pct` | `min_box_height_pct: 4.0` |
| `trail-risk-8` | `trailing_stop.max_trail_risk_pct: 8.0` |
| `r-managed-on` | `r_managed_runner.enabled: true`, `breakeven_r_threshold: 0.8`, `max_target_r: 5.0` |
| `lookback-narrow` | `min_lookback_weeks: 12`, `max_lookback_weeks: 30` |
| `lookback-wide` | `min_lookback_weeks: 6`, `max_lookback_weeks: 45` |

Run each config on the **primary screening window**:

```bash
python scripts/run_backtest.py --config configs/experiments/round-01/<name>.yaml \
  --start 2024-06-01 --end 2026-05-31 --no-email
```

Parse `summary_report.json` from each `backtest_outputs/run_*` folder. Log every run to `src/agentic-loop/regime-search-log.jsonl`.

**Shortlist top 3** by ranking rules above. Record in `regime-search-results.md` under `## Round N — Screening`.

---

## Phase 2 — Index-candle analog validation

1. **Discover analogs** for reference window `2024-12-01 → 2026-05-31` on NIFTY 50 (18-month shape match, `top_k=3`).
2. For **each of the top 3 screening configs**, run backtests on all 3 analog backtest windows.
3. Build a table per config:

| Config | Analog # | Index window | Backtest window | CAGR | Max DD | Trades | Consistent? |

4. Mark configs that pass the **consistency rule** for index analogs.

---

## Phase 3 — VIX analog validation

1. Discover VIX analogs for the same reference window using `find_vix_analogs()` (`top_k=3`). Use existing `scripts/run_vix_analog_backtests.py` logic but run **each of the top 3 screening configs** (not just `config.yaml`).
2. VIX analog backtest windows: use the script’s default **subsequent-year** shift (`analog_start+1y → analog_end+1y`) — this is intentional for VIX regime carry-over.
3. Same consistency table and rule as Phase 2.

---

## Phase 4 — Promote winners

For each config consistent on **both** Phase 2 and Phase 3:

1. Copy YAML to `configs/winners/round-<N>/<config-name>.yaml`.
2. Write `<config-name>-summary.md` with full metrics (primary + 6 analogs).
3. Add to the **incumbent winners list** for the next round.

If **no** config passes both analog tests, promote none; note in results and proceed to Phase 5 using the top screening config as the mutation base.

---

## Phase 5 — Mutation round (repeat)

Compare new screening results against **all incumbent winners** on the primary window. A config **beats incumbents** if:

- `cagr` is ≥ 0.5 percentage points higher than the best incumbent, **and**
- `max_drawdown_pct` is ≤ incumbent best + 0.5 pp.

**Round N+1 mutation axes** (pick 4–6 combos, don’t full Cartesian product):

| Axis | Values to try |
|---|---|
| Lookback SMA | 30, 50, 80 |
| Adaptive band | narrow (12–30), default (9–39), wide (6–45) |
| `breakout_reset_above_top_pct` | 1.0, 2.0, 4.0 |
| `min_box_duration_days` | 4, 5, 6 |
| `min_box_height_pct` | 3.0, 4.0, 5.0 |
| `trailing_stop.max_trail_risk_pct` | 8.0, 10.0 |
| `r_managed_runner` | off, on (`breakeven_r_threshold: 0.8`, `max_target_r: 5.0`) |
| `stale_box_tsl_daily_pct` | 8.0, 10.0, 12.0 |

Seed mutations from the **best incumbent winner’s** overrides (not bare `config.yaml`). Name configs `round-02-<base>-<mutation>.yaml`.

**Stop condition:** After **3 rounds** OR when a round produces no new winner that beats incumbents on primary **and** passes both analog tests.

---

## Reporting (after each round)

Update `src/agentic-loop/regime-search-results.md` with:

1. **Screening leaderboard** (all configs, primary window)
2. **Top 3 shortlist**
3. **Index analog consistency matrix** (3×3 configs × windows)
4. **VIX analog consistency matrix**
5. **Winners promoted this round** (with YAML paths)
6. **Cumulative winners table** (all rounds)
7. **Next-round mutation plan**

---

## Constraints

- Do not edit production `config.yaml`.
- Do not skip PIT fundamentals or change universe/risk limits unless explicitly in the grid.
- Every backtest must use `initial_capital_inr: 500000`, `simulation_slippage_pct: 0.05`, `target_segment: NIFTY_500`.
- Analog search must not use data from inside the reference window (no lookahead).
- If a run fails or DB is missing, stop and report — do not fabricate metrics.
- Commit experiment configs and results markdown to git; do not commit `backtest_outputs/` run folders.

---

## Success criteria

The experiment is complete when you deliver:

1. `configs/winners/` with 1–3 validated configs (each with summary markdown)
2. `src/agentic-loop/regime-search-results.md` with full round-by-round audit trail
3. A final recommendation table: config name, primary CAGR/DD, min analog CAGR, max analog DD, key parameter deltas vs `config.yaml`

---

## Optional first command (sanity check only)

Before Round 1, verify the toolchain:

```bash
python scripts/run_backtest.py --config config.yaml --start 2024-06-01 --end 2026-05-31 --no-email
```

Confirm `summary_report.json` contains `cagr`, `max_drawdown_pct`, `total_closed_trades`, `win_rate`.

---

## Notes for the agent

- Prior work (`src/agentic-loop/optimization-results.md`) found `breakout_reset_above_top_pct: 4.0` + `sma_period: 80` dominant on the 2Y window — treat that as the baseline to beat, not as pre-validated on analog periods.
- `configs/baseline-next-best.yaml` (`min_box_duration_days: 5`) is a useful A/B reference; include it in Round 1 if not already in the grid.
- Index analog matcher does not exist yet; Phase 0 is mandatory before Phase 2.
