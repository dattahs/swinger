"""Tests for sector RS and trend override helpers."""

import pytest
from datetime import date, timedelta

import pandas as pd

from src.config import AppConfig, BacktestConfig, DarvasBoxConfig, MarketTrendFilter
from src.engine.filters import symbol_trend_ok
from src.engine.sector_rs import compute_sector_rs_percentiles, trailing_return


def _bars(closes: list[float]) -> pd.DataFrame:
    start = date(2024, 1, 1)
    rows = []
    for i, close in enumerate(closes):
        d = start + timedelta(days=i)
        rows.append(
            {
                "symbol": "X",
                "date": d,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1_000_000,
                "turnover_inr": 1e8,
            }
        )
    return pd.DataFrame(rows)


def _cfg() -> AppConfig:
    return AppConfig(
        backtest=BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            price_warmup_start_date=date(2024, 1, 1),
        ),
        darvas_box=DarvasBoxConfig(
            market_trend_filter=MarketTrendFilter(
                moving_averages=[3, 5],
                allow_sector_trend_override=True,
            )
        ),
    )


def test_trailing_return():
    bars = _bars([100, 100, 100, 120])
    assert trailing_return(bars, 2) == pytest.approx(0.2)


def test_sector_rs_percentiles_ranking():
    nifty = _bars([100] * 60 + [110])
    pharma = _bars([100] * 60 + [130])
    it = _bars([100] * 60 + [105])
    sector_index_bars = {
        "NIFTY PHARMA": pharma,
        "NIFTY IT": it,
    }
    pct = compute_sector_rs_percentiles(
        {"Pharmaceuticals", "IT"},
        sector_index_bars,
        nifty,
        lookback_days=5,
    )
    assert pct["Pharmaceuticals"] > pct["IT"]


def test_symbol_trend_uses_sector_etf_when_nifty_bearish():
    cfg = _cfg()
    # NIFTY below short MAs
    nifty = _bars([120, 115, 110, 105, 100, 95])
    # Sector ETF above its MAs
    etf = _bars([90, 92, 94, 96, 98, 100])
    ok = symbol_trend_ok(
        "RELIANCE",
        "Pharmaceuticals",
        nifty,
        {"PHARMABEES": etf},
        {"NIFTY PHARMA": etf},
        cfg,
    )
    assert ok is True


def test_symbol_trend_sector_index_mode_ignores_nifty():
    cfg = AppConfig(
        backtest=BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            price_warmup_start_date=date(2024, 1, 1),
        ),
        darvas_box=DarvasBoxConfig(
            market_trend_filter=MarketTrendFilter(
                mode="sector_index",
                moving_averages=[3, 5],
            )
        ),
    )
    nifty = _bars([120, 115, 110, 105, 100, 95])
    pharma_index = _bars([90, 92, 94, 96, 98, 100])
    ok = symbol_trend_ok(
        "SUNPHARMA",
        "Pharmaceuticals",
        nifty,
        {},
        {"NIFTY PHARMA": pharma_index},
        cfg,
    )
    assert ok is True

    weak_sector = _bars([100, 98, 96, 94, 92, 90])
    assert not symbol_trend_ok(
        "SUNPHARMA",
        "Pharmaceuticals",
        nifty,
        {},
        {"NIFTY PHARMA": weak_sector},
        cfg,
    )
