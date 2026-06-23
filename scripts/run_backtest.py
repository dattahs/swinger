#!/usr/bin/env python3
"""Run historical backtest — REQUIREMENTS v1.2 Section 8."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester
from src.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Darvas Box backtest")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--start", help="Override start date YYYY-MM-DD")
    parser.add_argument("--end", help="Override end date YYYY-MM-DD")
    parser.add_argument("--seed-demo", action="store_true", help="Seed synthetic data for smoke test")
    parser.add_argument(
        "--debug-log",
        action="store_true",
        help="Enable detailed action debug log (overrides config backtest.debug_log.enabled)",
    )
    parser.add_argument(
        "--no-debug-log",
        action="store_true",
        help="Disable detailed action debug log",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable session progress log to console/file",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path
    cfg = load_config(config_path)

    if args.debug_log:
        cfg.backtest.debug_log.enabled = True
    if args.no_debug_log:
        cfg.backtest.debug_log.enabled = False
    if args.no_progress:
        cfg.backtest.progress_log.enabled = False

    if args.seed_demo:
        from src.data.seed import seed_demo_data

        seed_demo_data(cfg.backtest.data_db_path)

    start = date.fromisoformat(args.start) if args.start else None
    end = date.fromisoformat(args.end) if args.end else None

    bt = Backtester(cfg, repo_root=ROOT)
    out = bt.run(start=start, end=end)
    print(f"Backtest complete. Outputs written to {out}", flush=True)


if __name__ == "__main__":
    main()
