#!/usr/bin/env python3
"""Download Upstox NSE EQ instrument master for symbol→token mapping."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.broker.instruments import download_upstox_nse_instruments


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default="./data/instruments/upstox_nse_eq.json",
        help="Output JSON path",
    )
    args = parser.parse_args()
    count = download_upstox_nse_instruments(args.out)
    print(f"Wrote {count} NSE_EQ symbols to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
