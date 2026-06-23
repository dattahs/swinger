#!/usr/bin/env python3
"""Print 5-year NIFTY vs SMA calibration for adaptive new-high lookback."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.engine.adaptive_lookback import (
    index_spread_pct_series,
    resolve_new_high_lookback_sessions,
)
from src.repository.sqlite import SqliteDataLake

cfg = load_config_relaxed(ROOT / "config.yaml")
acfg = cfg.universe_filters.adaptive_new_high_lookback
dl = SqliteDataLake(ROOT / cfg.backtest.data_db_path)
bars = dl.get_daily_bars(acfg.regime_index, date(2026, 6, 19), int(acfg.calibration_years * 252) + acfg.sma_period + 50)
spreads = index_spread_pct_series(bars, acfg.sma_period).dropna()

print(f"Regime index: {acfg.regime_index}  (NIFTY 50 proxy — NIFTY 500 index not in data lake)")
print(f"SMA period: {acfg.sma_period}")
print(f"Calibration window: {acfg.calibration_years} years ({int(acfg.calibration_years * 252)} sessions)")
print(f"Percentiles: P{acfg.low_percentile} (bear) / P{acfg.high_percentile} (bull)")
print(f"Lookback range: {acfg.min_lookback_weeks}w (~2mo) to {acfg.max_lookback_weeks}w (~9mo)")
print()
print(f"Spread (close-SMA)/SMA % over full sample:")
print(f"  min={spreads.min():.2f}%  P10={spreads.quantile(0.1):.2f}%  "
      f"median={spreads.median():.2f}%  P90={spreads.quantile(0.9):.2f}%  max={spreads.max():.2f}%")
print()

for label, d in [("Jun 2024 start", date(2024, 6, 3)), ("Jun 2026 end", date(2026, 6, 19))]:
    sess, meta = resolve_new_high_lookback_sessions(bars, cfg, d)
    print(f"{label} ({d}):")
    print(f"  spread={meta['spread_pct']}%  P10={meta['p_low']}%  P90={meta['p_high']}%")
    print(f"  bull_factor={meta['bull_factor']}  lookback={meta['lookback_weeks']}w ({sess} sessions)")
