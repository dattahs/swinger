#!/usr/bin/env python3
"""Orchestrate data ingest pipelines."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config
from src.data.seed import seed_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest backtest data")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--demo", action="store_true", help="Seed synthetic demo data")
    parser.add_argument(
        "--download-bhavcopy",
        action="store_true",
        help="Run full download+ingest via scripts/download_bhavcopy.py",
    )
    parser.add_argument("--verify-only", action="store_true", help="Check DB exists")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    db = Path(cfg.backtest.data_db_path)
    if not db.is_absolute():
        db = ROOT / db

    if args.verify_only:
        if not db.exists():
            print(f"MISSING: {db}")
            sys.exit(1)
        import sqlite3

        conn = sqlite3.connect(db)
        bars = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
        uni = conn.execute("SELECT COUNT(*) FROM nifty500_membership").fetchone()[0]
        pit = conn.execute("SELECT COUNT(*) FROM fundamentals_pit").fetchone()[0]
        conn.close()
        print(f"OK: {db}")
        print(f"  daily_bars={bars}, nifty500={uni}, fundamentals_pit={pit}")
        return

    if args.demo:
        seed_demo_data(db)
        print(f"Demo data seeded at {db}")
        return

    if args.download_bhavcopy:
        import subprocess

        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "download_bhavcopy.py"),
                "--config",
                args.config,
                "--from",
                str(cfg.backtest.price_warmup_start_date),
                "--to",
                str(cfg.backtest.end_date),
            ],
            check=True,
        )
        return

    print("Usage:")
    print("  python scripts/ingest_all.py --demo")
    print("  python scripts/ingest_all.py --verify-only")
    print("  python scripts/ingest_all.py --download-bhavcopy")
    print("  python scripts/download_bhavcopy.py --from 2018-01-01 --to 2018-03-31")


if __name__ == "__main__":
    main()
