#!/usr/bin/env python3
"""Ingest Nifty Midcap 100 index OHLC from NSE index archives into the data lake."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.sector_regime_council import MIDCAP_DB_SYMBOL
from src.config import load_config
from src.data.bhavcopy import SESSION, _ensure_session
from src.data.index_data import _row_from_archive
from src.repository.sqlite import init_data_lake


def ingest_midcap_index(
    db_path: Path,
    *,
    limit_days: int | None = None,
    pause_sec: float = 0.12,
) -> int:
    """Download Nifty Midcap 100 closes for NIFTY 50 trading days in the lake."""
    _ensure_session()
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path, timeout=60)
    try:
        query = "SELECT DISTINCT date FROM daily_bars WHERE symbol='NIFTY 50' ORDER BY date"
        if limit_days is not None:
            query += f" DESC LIMIT {int(limit_days)}"
        dates = [r[0] for r in conn.execute(query)]
        if limit_days is not None:
            dates = list(reversed(dates))
        count = 0
        for ds in dates:
            d = date.fromisoformat(ds)
            ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year}"
            url = f"https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"
            resp = SESSION.get(url, timeout=60)
            if resp.status_code != 200 or resp.text.startswith("<!"):
                continue
            df = pd.read_csv(StringIO(resp.text))
            match = df[df["Index Name"].str.contains("Midcap 100", case=False, na=False)]
            if match.empty:
                continue
            row = _row_from_archive(match.iloc[0], d, "Closing Index Value")
            if row is None:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, turnover_inr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    MIDCAP_DB_SYMBOL,
                    ds,
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    0,
                    0.0,
                ),
            )
            count += 1
            time.sleep(pause_sec)
        conn.commit()
        return count
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Nifty Midcap 100 index bars")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--db", default=None, help="Override data lake path")
    parser.add_argument(
        "--limit-days",
        type=int,
        default=None,
        help="Only fetch the most recent N trading days (default: all NIFTY 50 dates)",
    )
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    db_path = Path(args.db) if args.db else ROOT / cfg.backtest.data_db_path
    n = ingest_midcap_index(db_path, limit_days=args.limit_days)
    print(f"Ingested {n} rows for {MIDCAP_DB_SYMBOL} into {db_path}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
