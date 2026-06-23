import sqlite3
from datetime import date
from pathlib import Path

import pytest

from src.data.seed import seed_demo_data
from src.repository.sqlite import SqliteDataLake


@pytest.fixture
def pit_db(tmp_path: Path) -> Path:
    db = tmp_path / "pit.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE fundamentals_pit (
            symbol TEXT, metric TEXT, period_end TEXT, effective_date TEXT,
            value REAL, source TEXT, source_url TEXT,
            PRIMARY KEY (symbol, metric, effective_date)
        );
        """
    )
    conn.execute(
        "INSERT INTO fundamentals_pit VALUES ('X','roe_pct','2017-12-31','2018-06-01',20,'t','')"
    )
    conn.execute(
        "INSERT INTO fundamentals_pit VALUES ('X','roe_pct','2017-12-31','2018-01-01',20,'t','')"
    )
    conn.commit()
    conn.close()
    return db


def test_pit_join_uses_effective_date(pit_db: Path):
    lake = SqliteDataLake(pit_db)
    m = lake.get_fundamentals_pit("X", date(2018, 3, 1))
    assert m.get("roe_pct") == 20


def test_seed_demo_creates_trading_days(tmp_path: Path):
    db = tmp_path / "demo.db"
    seed_demo_data(db)
    lake = SqliteDataLake(db)
    days = lake.get_trading_days(date(2018, 1, 1), date(2018, 1, 31))
    assert len(days) > 10


def test_scan_universe_requires_bar_on_session(tmp_path: Path):
    db = tmp_path / "demo.db"
    seed_demo_data(db)
    conn = sqlite3.connect(db)
    session = date(2018, 1, 15)
    for sym, bar_date in [("ACTIVE", session.isoformat()), ("STALE", "2018-06-29")]:
        conn.execute(
            "INSERT INTO nifty500_membership VALUES (?, '2016-09-01')",
            (sym,),
        )
        conn.execute(
            """
            INSERT INTO daily_bars
            (symbol, date, open, high, low, close, volume, turnover_inr)
            VALUES (?, ?, 100, 101, 99, 100, 500000, 50000000)
            """,
            (sym, bar_date),
        )
    conn.commit()
    conn.close()

    lake = SqliteDataLake(db)
    universe = lake.get_scan_universe(session)
    assert "ACTIVE" in universe
    assert "STALE" not in universe
    assert "DEMO1" not in universe
