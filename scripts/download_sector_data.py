#!/usr/bin/env python3
"""Download NSE sector index series and ingest sector ETF bars for backtests."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.bhavcopy import ingest_bhavcopy_dir
from src.data.constituents import ingest_sector_map
from src.data.index_data import download_and_ingest_nifty50
from src.data.sector_etfs import SECTOR_ETF_SYMBOLS


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest sector index + ETF history")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--from", dest="from_date", required=True, help="Start YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End YYYY-MM-DD")
    parser.add_argument(
        "--raw-dir",
        default="data/raw/bhavcopy",
        help="Bhavcopy raw directory (for sector ETF OHLCV re-ingest)",
    )
    parser.add_argument("--skip-etf-bhavcopy", action="store_true")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to_date)
    db_path = ROOT / cfg.backtest.data_db_path
    raw_dir = ROOT / args.raw_dir

    print(f"Data lake: {db_path}")
    print(f"Sector index range: {start} .. {end}")

    print("\n[1/3] NIFTY 50 + NSE sector indices...")
    n = download_and_ingest_nifty50(db_path, start, end, include_sector_indices=True)
    print(f"  {n} index bar rows written")

    if not args.skip_etf_bhavcopy and raw_dir.exists():
        print("\n[2/3] Re-ingesting sector ETF bars from existing Bhavcopy...")
        rows = ingest_bhavcopy_dir(raw_dir, db_path, symbols=set(SECTOR_ETF_SYMBOLS))
        print(f"  {rows} ETF bar rows touched")
    else:
        print("\n[2/3] Skipped ETF bhavcopy (no raw dir or --skip-etf-bhavcopy)")

    print("\n[3/3] Sector map for ETFs...")
    s = ingest_sector_map(db_path)
    print(f"  sector_map rows={s}")
    print("\nDone.")


if __name__ == "__main__":
    main()
