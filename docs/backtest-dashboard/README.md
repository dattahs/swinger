# Backtest dashboard

Self-contained HTML report aggregating optimization, box-shape, target-setting, and March-yearwise experiments.

## View locally

Open `index.html` in a browser (requires internet for Chart.js CDN).

## Regenerate

```bash
python scripts/build_backtest_dashboard.py
```

Outputs:
- `docs/backtest-dashboard/index.html` — interactive UI
- `docs/backtest-dashboard/data.json` — scrubbed raw records (no secrets, paths anonymized)

Data sources: `src/agentic-loop/*.jsonl`
