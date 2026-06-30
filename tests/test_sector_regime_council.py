"""Tests for sector regime council."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.analysis.sector_regime_council import (
    CouncilRequest,
    apply_confidence_override,
    build_council_summary,
    classify_regime,
    darvas_parameters,
    run_sector_regime_council,
    window_start,
)
from src.repository.sqlite import init_data_lake


def _seed_bars(
    conn: sqlite3.Connection,
    symbol: str,
    start: date,
    n: int,
    *,
    base: float = 100.0,
    step: float = 0.5,
    volume: int = 1_000_000,
) -> None:
    for i in range(n):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        close = base + step * i
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_bars
            (symbol, date, open, high, low, close, volume, turnover_inr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (symbol, d.isoformat(), close, close + 1, close - 1, close, volume, 0.0),
        )


@pytest.fixture
def council_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_data_lake(db)
    conn = sqlite3.connect(db)
    start = date(2024, 1, 1)
    n = 260
    _seed_bars(conn, "NIFTY 50", start, n, base=100, step=0.1)
    _seed_bars(conn, "NIFTY BANK", start, n, base=100, step=0.8)
    _seed_bars(conn, "BANKBEES", start, n, base=50, step=0.0, volume=2_000_000)
    _seed_bars(conn, "NIFTY IT", start, n, base=200, step=-0.2)
    _seed_bars(conn, "ITBEES", start, n, base=30, step=0.0, volume=500_000)
    for sym in (
        "NIFTY PHARMA",
        "NIFTY AUTO",
        "NIFTY FMCG",
        "NIFTY METAL",
        "NIFTY REALTY",
        "NIFTY ENERGY",
        "NIFTY INFRA",
    ):
        _seed_bars(conn, sym, start, n, base=150, step=0.05)
    for etf in ("PHARMABEES", "AUTOBEES", "CONSUMBEES", "INFRABEES"):
        _seed_bars(conn, etf, start, n, base=40, step=0.0, volume=800_000)
    _seed_bars(conn, "NIFTY MIDCAP 100", start, n, base=300, step=0.15)
    conn.commit()
    conn.close()
    return db


def test_window_start():
    assert window_start(date(2026, 6, 26), 6) == date(2025, 12, 26)


def test_classify_strong_trend_up():
    regime, conf = classify_regime(
        price=120,
        ma20=115,
        ma50=110,
        ma200=100,
        rsi14=65,
        breadth=70,
        atr_cur=2.0,
        atr_avg=2.5,
    )
    assert regime == "STRONG_TREND_UP"
    assert conf >= 80


def test_classify_high_volatility():
    regime, _ = classify_regime(
        price=100,
        ma20=100,
        ma50=100,
        ma200=100,
        rsi14=50,
        breadth=50,
        atr_cur=4.0,
        atr_avg=2.0,
    )
    assert regime == "HIGH_VOLATILITY"


def test_apply_confidence_override():
    assert apply_confidence_override("WEAK_TREND_UP", 65) == "WEAK_TREND_UP"
    assert apply_confidence_override("WEAK_TREND_UP", 58) == "RANGING"


def test_darvas_parameters_skip_when_low_confidence():
    params = darvas_parameters("WEAK_TREND_UP", 55, 1.0)
    assert params["skip_new_entries"] is True


def test_build_council_summary_empty():
    summary = build_council_summary([], vix_current=None, vix_6m_avg=None, fii_net_flow_30d_cr=None)
    assert summary["dominant_regime"] == "RANGING"
    assert summary["recommended_overall_exposure"] == 0.0


def test_run_council_skip_breadth(council_db: Path):
    as_of = date(2024, 12, 31)
    result = run_sector_regime_council(
        CouncilRequest(
            as_of=as_of,
            window_months=6,
            db_path=council_db,
            vix_csv_path=Path("/nonexistent/vix.csv"),
            skip_breadth=True,
        )
    )
    assert result["analysis_date"] == as_of.isoformat()
    assert len(result["sectors"]) >= 8
    for sector in result["sectors"]:
        assert sector["regime"] in {
            "STRONG_TREND_UP",
            "WEAK_TREND_UP",
            "RANGING",
            "WEAK_TREND_DOWN",
            "STRONG_TREND_DOWN",
            "HIGH_VOLATILITY",
        }
        assert len(sector["experiment_suggestions"]) == 3
    json.dumps(result)


def test_run_council_resolves_latest_as_of(council_db: Path):
    result = run_sector_regime_council(
        CouncilRequest(
            as_of=None,
            db_path=council_db,
            vix_csv_path=Path("/nonexistent/vix.csv"),
            skip_breadth=True,
        )
    )
    assert result["analysis_date"]
    assert result["council_summary"]["capital_deployment_priority"] is not None
