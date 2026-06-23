#!/usr/bin/env python3
"""
Download NSE Bhavcopy + reference data and ingest into swinger_data.db.

Usage:
  python scripts/download_bhavcopy.py --from 2016-09-01 --to 2026-05-31
  python scripts/download_bhavcopy.py --from 2018-01-01 --to 2018-03-31 --ingest-only
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.bhavcopy import (
    build_trading_calendar_from_bars,
    download_bhavcopy_range,
    ingest_bhavcopy_dir,
)
from src.data.constituents import (
    fetch_nifty500_symbols,
    ingest_nifty500_membership,
    ingest_sector_map,
)
from src.data.fundamentals_bootstrap import ingest_bootstrap_fundamentals
from src.data.index_data import download_and_ingest_nifty50
from src.data.sector_etfs import SECTOR_ETF_SYMBOLS


def main() -> None:
    parser = argparse.ArgumentParser(description="Download and ingest NSE Bhavcopy")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--from", dest="from_date", required=True, help="Start YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End YYYY-MM-DD")
    parser.add_argument(
        "--raw-dir",
        default="data/raw/bhavcopy",
        help="Directory for downloaded ZIP/CSV files",
    )
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--ingest-only", action="store_true")
    parser.add_argument("--skip-fundamentals-bootstrap", action="store_true")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    raw_dir = ROOT / args.raw_dir
    db_path = ROOT / cfg.backtest.data_db_path

    print(f"Data lake: {db_path}")
    print(f"Bhavcopy raw: {raw_dir}")
    print(f"Range: {start} .. {end}")

    if not args.ingest_only:
        print("\n[1/6] Downloading Bhavcopy (this may take a while)...")
        dl, sk = download_bhavcopy_range(start, end, raw_dir)
        print(f"  Done: downloaded={dl}, skipped={sk}")
        if args.download_only:
            return

    print("\n[2/6] Loading NIFTY 500 symbol list...")
    try:
        symbols = fetch_nifty500_symbols()
    except Exception as exc:
        print(f"  CSV fetch failed ({exc}), using API fallback")
        from src.data.constituents import fetch_nifty500_from_api

        symbols = fetch_nifty500_from_api()
    print(f"  {len(symbols)} symbols")

    print("\n[3/6] Ingesting Bhavcopy into SQLite...")
    sym_set = set(symbols) | set(SECTOR_ETF_SYMBOLS)
    rows = ingest_bhavcopy_dir(raw_dir, db_path, symbols=sym_set)
    print(f"  {rows} bar rows inserted")

    print("\n[4/6] NIFTY 500 membership + sector map...")
    n = ingest_nifty500_membership(db_path, symbols=symbols)
    s = ingest_sector_map(db_path, symbols=symbols)
    print(f"  membership={n}, sectors={s}")

    print("\n[5/6] NIFTY 50 + sector index series...")
    idx_n = download_and_ingest_nifty50(db_path, start, end, include_sector_indices=True)
    print(f"  {idx_n} index rows")

    cal_n = build_trading_calendar_from_bars(db_path)
    print(f"  trading_calendar={cal_n} days")

    if not args.skip_fundamentals_bootstrap:
        print("\n[6/6] Bootstrap fundamentals (replace with XBRL ingest for true PIT)...")
        pit_n = ingest_bootstrap_fundamentals(
            db_path,
            effective_date=cfg.backtest.price_warmup_start_date,
            symbols=symbols,
        )
        print(f"  {pit_n} PIT rows (bootstrap — not filing-date accurate)")
    else:
        print("\n[6/6] Skipped fundamentals bootstrap")

    print("\nReady. Run: python scripts/run_backtest.py --config config.yaml")


if __name__ == "__main__":
    main()
