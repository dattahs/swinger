#!/usr/bin/env python3
"""Run sector regime council for all 10 NSE segments on a chosen as-of date."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.sector_regime_council import CouncilRequest, run_sector_regime_council
from src.config import load_config


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sector regime council — 10 segments, Darvas parameter hints",
    )
    parser.add_argument(
        "--as-of",
        default=None,
        help="Analysis date YYYY-MM-DD (default: latest NIFTY 50 bar in data lake)",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=6,
        help="Lookback window in calendar months (default: 6)",
    )
    parser.add_argument("--config", default="config.yaml", help="Config for data_db_path")
    parser.add_argument("--db", default=None, help="Override SQLite data lake path")
    parser.add_argument(
        "--vix-csv",
        default="data/processed/india_vix_daily.csv",
        help="India VIX daily CSV path",
    )
    parser.add_argument(
        "--fii-30d-cr",
        type=float,
        default=None,
        help="FII net flow last 30 days (Cr INR); omit to skip systemic FII check",
    )
    parser.add_argument(
        "--skip-breadth",
        action="store_true",
        help="Skip constituent breadth scan (faster; uses 50%% placeholder)",
    )
    parser.add_argument("--out", default=None, help="Write JSON to file (default: stdout)")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    db_path = Path(args.db) if args.db else ROOT / cfg.backtest.data_db_path
    if not db_path.is_file():
        print(f"Data lake not found: {db_path}", file=sys.stderr)
        print(
            "Run: python scripts/download_sector_data.py --from YYYY-MM-DD --to YYYY-MM-DD",
            file=sys.stderr,
        )
        return 2

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    request = CouncilRequest(
        as_of=as_of,
        window_months=args.window_months,
        db_path=db_path,
        vix_csv_path=ROOT / args.vix_csv,
        fii_net_flow_30d_cr=args.fii_30d_cr,
        skip_breadth=args.skip_breadth,
    )

    try:
        result = run_sector_regime_council(request)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    payload = json.dumps(result, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.write_text(payload, encoding="utf-8")
        print(f"Wrote {len(result['sectors'])} sectors to {out_path}", file=sys.stderr)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
