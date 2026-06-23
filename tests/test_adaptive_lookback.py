"""Tests for adaptive new-high lookback."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.config import AdaptiveNewHighLookbackConfig, AppConfig, UniverseFilters
from src.engine.adaptive_lookback import (
    calibrate_spread_percentiles,
    index_spread_pct_series,
    lookback_weeks_from_factor,
    normalized_bull_factor,
    resolve_new_high_lookback_sessions,
    weeks_to_sessions,
)
from tests.test_darvas import _minimal_config


def _index_bars(spreads: list[float], start: date = date(2020, 1, 1)) -> pd.DataFrame:
    rows = []
    for i, sp in enumerate(spreads):
        d = start + timedelta(days=i)
        sma = 100.0
        close = sma * (1 + sp / 100)
        rows.append({"date": d, "open": close, "high": close, "low": close, "close": close, "volume": 1})
    return pd.DataFrame(rows)


def test_bullish_shorter_lookback_than_bearish():
    uf = UniverseFilters(
        require_new_52wk_high=True,
        adaptive_new_high_lookback=AdaptiveNewHighLookbackConfig(
            enabled=True,
            min_lookback_weeks=9,
            max_lookback_weeks=39,
            calibration_years=1,
        ),
    )
    cfg = _minimal_config()
    cfg = cfg.model_copy(update={"universe_filters": uf})
    bear = _index_bars([-8.0] * 200 + [-1.0] * 80)
    bull = _index_bars([8.0] * 200 + [6.0] * 80)
    as_of = date(2020, 9, 27)
    bear_sess, bear_meta = resolve_new_high_lookback_sessions(bear, cfg, as_of)
    bull_sess, bull_meta = resolve_new_high_lookback_sessions(bull, cfg, as_of)
    assert bull_sess < bear_sess
    assert bull_meta["lookback_weeks"] < bear_meta["lookback_weeks"]


def test_weeks_to_sessions_two_months_nine_months():
    assert weeks_to_sessions(9) == 44
    assert weeks_to_sessions(39) == 189


def test_normalized_factor_clamped():
    assert normalized_bull_factor(0, -2, 2) == 0.5
    assert normalized_bull_factor(-10, -2, 2) == 0.0
    assert normalized_bull_factor(10, -2, 2) == 1.0
