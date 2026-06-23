"""Tests for backtest data cache."""

from __future__ import annotations

import sqlite3
from datetime import date

import pandas as pd

from src.backtest.data_cache import BacktestDataCache, enrich_bar_indicators
from src.config import AppConfig, BacktestConfig, SystemConfig
from src.data.seed import seed_demo_data
from src.repository.sqlite import SqliteDataLake


def _minimal_backtest_config() -> AppConfig:
    return AppConfig.model_construct(
        system=SystemConfig(),
        backtest=BacktestConfig(
            start_date=date(2018, 1, 1),
            end_date=date(2018, 3, 31),
            price_warmup_start_date=date(2016, 9, 1),
        ),
    )


def test_enrich_bar_indicators_adds_columns():
    df = pd.DataFrame(
        {
            "date": [date(2018, 1, d) for d in range(1, 25)],
            "open": [100.0] * 24,
            "high": [102.0] * 24,
            "low": [98.0] * 24,
            "close": [101.0] * 24,
            "volume": [500_000] * 24,
        }
    )
    out = enrich_bar_indicators(df, atr_period=20)
    assert "atr_20" in out.columns
    assert "vol_sma_20" in out.columns
    assert out.iloc[-1]["atr_20"] > 0


def test_cache_slice_and_scan_universe(tmp_path):
    db = tmp_path / "cache.db"
    seed_demo_data(db)
    session = date(2018, 1, 15)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO nifty500_membership VALUES ('ACTIVE', '2016-09-01')")
    for d in range(1, 20):
        conn.execute(
            """
            INSERT INTO daily_bars
            (symbol, date, open, high, low, close, volume, turnover_inr)
            VALUES ('ACTIVE', ?, 100, 102, 98, 101, 500000, 50000000)
            """,
            (date(2018, 1, d).isoformat(),),
        )
    conn.commit()
    conn.close()

    cfg = _minimal_backtest_config()
    cache = BacktestDataCache(SqliteDataLake(db), cfg)
    cache.warm(date(2018, 1, 1), date(2018, 1, 31))
    assert "ACTIVE" in cache.get_scan_universe(session)
    sliced = cache.slice_bars("ACTIVE", session, 30)
    assert not sliced.empty
    assert sliced.iloc[-1]["date"] == session
    assert "atr_20" in sliced.columns
