# Optimization Results

**Window:** 2024-06-01 to 2026-06-19
**Iterations:** 21

## Optimal config

- **Name:** zoom_sma80_reset_loose_4.0
- **CAGR:** 21.62%
- **Max DD:** 1.62%
- **Win rate:** 68.2%
- **Trades:** 236
- **Cadence:** daily
- **Run:** `C:\code\Swinger\backtest_outputs\run_20260623_105357`

### Parameter overrides
```yaml
darvas_box.breakout_reset_above_top_pct: 4.0
universe_filters.adaptive_new_high_lookback.sma_period: 80
```

## Top 3 feasible configs

| Rank | Name | CAGR | Max DD | Cadence | Trades |
|------|------|------|--------|---------|--------|
| 1 | zoom_sma80_reset_loose_4.0 | 21.62% | 1.62% | daily | 236 |
| 2 | confirm_zoom_sma80_reset_loose_4.0 | 21.62% | 1.62% | daily | 236 |
| 3 | zoom_pct_5_85_reset_loose_4.0 | 21.12% | 2.12% | daily | 240 |

## Algorithm suggestions

### Key findings
- **`breakout_reset_above_top_pct` is the dominant lever.** Moving from 2.0 → 4.0 (iter 5) jumped CAGR from 9.45% to 19.93%. Tightening to 0.5% (iter 4, 21) destroyed returns — stale boxes recycle too aggressively and kill valid breakouts.
- **SMA 80 beats SMA 50/30** for regime detection when combined with loose reset (iter 9: 21.62% vs iter 8 SMA30: 20.80%). Slower SMA = smoother regime signal, fewer whipsaws in lookback length.
- **Daily recalibration wins.** All non-daily cadences (iter 13–17) underperformed daily at 16–18% CAGR. The adaptive lookback benefits from updating every session in this 2Y window.
- **Adaptive beats fixed lookback** on 2Y data (best adaptive path ~21.6% vs best fixed 13w at 10.39%), but only after fixing the stale-breakout reset.
- **Reproducibility confirmed** — iter 18 exactly matched iter 9 (21.62% CAGR, 1.62% DD, 236 trades).

### Recommended config changes (v1)
```yaml
darvas_box:
  breakout_reset_above_top_pct: 4.0   # was 2.0

universe_filters:
  adaptive_new_high_lookback:
    enabled: true                      # keep
    sma_period: 80                     # was 50
    recalibration_cadence: daily       # keep default
    min_lookback_weeks: 9              # unchanged
    max_lookback_weeks: 39             # unchanged
```

### Out-of-scope / v2 ideas
- Investigate why `breakout_reset_above_top_pct: 4.0` helps so much — may indicate boxes are resetting prematurely at 2.0% on strong momentum names; consider making reset threshold ATR-relative instead of fixed %.
- Event-based cadence (SMA cross, iter 15) cut CAGR to 11.91% — not worth pursuing unless transaction-cost of daily recalibration becomes a concern in live.
- Percentile tightening (5/85, iter 10) is a close second at 21.12% with more trades (240) — worth A/B in paper trading.

## Experiment log

| Iter | Name | CAGR | Max DD | Feasible | Cadence |
|------|------|------|--------|----------|---------|
| 1 | baseline | 9.45% | 3.30% | True | daily |
| 2 | fixed_13w | 10.39% | 2.99% | True | daily |
| 3 | fixed_39w | 4.38% | 2.64% | True | daily |
| 4 | reset_tight_0.5 | -1.92% | 6.11% | True | daily |
| 5 | reset_loose_4.0 | 19.93% | 2.29% | True | daily |
| 6 | narrow_band | 8.90% | 3.41% | True | daily |
| 7 | wide_band | 11.20% | 2.47% | True | daily |
| 8 | zoom_sma30_reset_loose_4.0 | 20.80% | 2.91% | True | daily |
| 9 | zoom_sma80_reset_loose_4.0 | 21.62% | 1.62% | True | daily |
| 10 | zoom_pct_5_85_reset_loose_4.0 | 21.12% | 2.12% | True | daily |
| 11 | zoom_pct_20_95_reset_loose_4.0 | 19.98% | 2.54% | True | daily |
| 12 | zoom_reset_1.0_reset_loose_4.0 | 2.48% | 4.67% | True | daily |
| 13 | cadence_weekly_zoom_sma80_reset_loose_4.0 | 17.99% | 2.36% | True | weekly |
| 14 | cadence_monthly_zoom_sma80_reset_loose_4.0 | 17.67% | 2.82% | True | monthly |
| 15 | cadence_sma_cross_zoom_sma80_reset_loose_4.0 | 11.91% | 2.65% | True | event_nifty_sma_cross |
| 16 | cadence_spread_jump_zoom_sma80_reset_loose_4.0 | 16.62% | 2.48% | True | event_spread_jump |
| 17 | cadence_static_zoom_sma80_reset_loose_4.0 | 16.07% | 2.33% | True | static |
| 18 | confirm_zoom_sma80_reset_loose_4.0 | 21.62% | 1.62% | True | daily |
| 19 | confirm_zoom_pct_5_85_reset_loose_4.0 | 21.12% | 2.12% | True | daily |
| 20 | aggressive_wide | 20.41% | 3.18% | True | daily |
| 21 | conservative_narrow | -2.19% | 6.47% | True | daily |
