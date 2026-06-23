"""Data ingest helpers and seed utilities."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.repository.sqlite import init_data_lake


def seed_demo_data(db_path: str | Path, *, num_symbols: int = 3) -> None:
    """Populate minimal synthetic data for smoke tests."""
    db_path = Path(db_path)
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path)

    start = date(2016, 9, 1)
    symbols = [f"DEMO{i}" for i in range(1, num_symbols + 1)]
    sectors = {"DEMO1": "TECH", "DEMO2": "TECH", "DEMO3": "BANK"}

    for sym in symbols:
        conn.execute(
            "INSERT OR IGNORE INTO nifty500_membership VALUES (?, ?)",
            (sym, "2016-09-01"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO sector_map VALUES (?, ?)",
            (sym, sectors.get(sym, "OTHER")),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO fundamentals_pit
            (symbol, metric, period_end, effective_date, value, source, source_url)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sym, "roe_pct", "2017-12-31", "2018-01-15", 20.0, "test", ""),
        )
        for metric, val in [
            ("revenue_growth_pct", 20.0),
            ("eps_growth_pct", 20.0),
            ("roce_pct", 18.0),
            ("promoter_holding_pct", 50.0),
            ("debt_to_equity", 0.3),
            ("eps_growth_fy_1_yoy", 10.0),
            ("eps_growth_fy_2_yoy", 12.0),
            ("eps_growth_fy_3_yoy", 15.0),
        ]:
            conn.execute(
                """
                INSERT OR IGNORE INTO fundamentals_pit
                (symbol, metric, period_end, effective_date, value, source, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sym, metric, "2017-12-31", "2018-01-15", val, "test", ""),
            )

    d = start
    price = 150.0
    while d <= date(2018, 6, 30):
        if d.weekday() < 5:
            conn.execute(
                "INSERT OR IGNORE INTO trading_calendar VALUES (?, 1)", (d.isoformat(),)
            )
            for sym in symbols:
                o = price
                h = price * 1.02
                l = price * 0.98
                c = price * 1.005
                vol = 600_000
                conn.execute(
                    """
                    INSERT OR REPLACE INTO daily_bars
                    (symbol, date, open, high, low, close, volume, turnover_inr)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sym, d.isoformat(), o, h, l, c, vol, vol * c),
                )
            price *= 1.001
        d += timedelta(days=1)

    idx = "NIFTY 50"
    d = start
    idx_price = 10000.0
    while d <= date(2018, 6, 30):
        if d.weekday() < 5:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, turnover_inr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (idx, d.isoformat(), idx_price, idx_price * 1.01, idx_price * 0.99, idx_price, 0, 0),
            )
            idx_price *= 1.0005
        d += timedelta(days=1)

    conn.commit()
    conn.close()


def ingest_bhavcopy_placeholder(raw_dir: Path, db_path: Path) -> None:
    """Load CSV files from raw_dir into daily_bars. Expects NSE-style columns."""
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path)
    for csv in raw_dir.glob("*.csv"):
        df = pd.read_csv(csv)
        colmap = {
            "SYMBOL": "symbol",
            "DATE1": "date",
            "OPEN": "open",
            "HIGH": "high",
            "LOW": "low",
            "CLOSE": "close",
            "TOTTRDQTY": "volume",
            "TOTTRDVAL": "turnover_inr",
        }
        df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
        if "symbol" not in df.columns:
            continue
        for _, row in df.iterrows():
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, turnover_inr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["symbol"],
                    str(row["date"])[:10],
                    row.get("open"),
                    row.get("high"),
                    row.get("low"),
                    row.get("close"),
                    int(row.get("volume", 0)),
                    float(row.get("turnover_inr", 0)),
                ),
            )
    conn.commit()
    conn.close()
