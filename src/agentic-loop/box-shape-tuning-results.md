# Box Shape Tuning Results

**Base config:** `opt-iter09-sma80.yaml` (zoom_sma80_reset_loose_4.0)
**Window:** 2024-06-01 to 2026-06-19
**Iterations:** 15

## Best config

- **Name:** dur_min_box_duration_days_4
- **CAGR:** 22.60%
- **Max DD:** 1.70%
- **Win rate:** 67.6%
- **Trades:** 253
- **Run:** `C:\code\Swinger\backtest_outputs\run_20260626_100837`

### Parameters
```yaml
adaptive_sma_period: 80
breakout_reset_above_top_pct: 4.0
darvas_reversal_days: 3
min_box_duration_days: 4
min_box_height_pct: 3.0
```

## Experiment log

| Iter | Name | CAGR | Max DD | Trades |
|------|------|------|--------|--------|
| 1 | box_baseline_confirm | 21.62% | 1.62% | 236 |
| 2 | rev_darvas_reversal_days_2 | 19.55% | 1.72% | 217 |
| 3 | rev_darvas_reversal_days_4 | 20.64% | 1.64% | 235 |
| 4 | rev_darvas_reversal_days_5 | 20.51% | 2.79% | 246 |
| 5 | rev_darvas_reversal_days_6 | 19.55% | 2.55% | 246 |
| 6 | dur_min_box_duration_days_3 | 19.99% | 2.28% | 281 |
| 7 | dur_min_box_duration_days_4 | 22.60% | 1.70% | 253 |
| 8 | dur_min_box_duration_days_6 | 18.90% | 1.63% | 215 |
| 9 | dur_min_box_duration_days_7 | 17.97% | 1.67% | 198 |
| 10 | height_min_box_height_pct_2.0 | 18.86% | 1.58% | 308 |
| 11 | height_min_box_height_pct_2.5 | 20.43% | 1.68% | 276 |
| 12 | height_min_box_height_pct_3.5 | 17.53% | 2.70% | 223 |
| 13 | height_min_box_height_pct_4.0 | 9.05% | 4.09% | 183 |
| 14 | refine_darvas_reversal_days_2 | 21.89% | 2.05% | 239 |
| 15 | refine_darvas_reversal_days_4 | 22.08% | 1.69% | 250 |
