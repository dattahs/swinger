#!/usr/bin/env python3
"""Daily live / paper run — reconcile broker, run strategy, place GTTs."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.broker.env import load_dotenv
from src.config import load_config_relaxed
from src.live.runner import LiveRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Swinger live EOD run")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", help="Session date YYYY-MM-DD (default: latest in data lake)")
    parser.add_argument("--login", action="store_true", help="Force Upstox browser login")
    parser.add_argument("--no-warmup", action="store_true", help="Skip Darvas state warmup backtest")
    parser.add_argument(
        "--force-warmup",
        action="store_true",
        help="Rebuild Darvas warmup (ignore DB + disk cache)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / ".env")
    cfg = load_config_relaxed(ROOT / args.config)
    session = date.fromisoformat(args.date) if args.date else None
    runner = LiveRunner(
        cfg,
        repo_root=ROOT,
        force_login=args.login,
        skip_warmup=args.no_warmup,
        force_warmup=args.force_warmup,
    )
    report = runner.run(session)

    print(
        f"Session {report.session_date} | equity INR {report.equity_inr:,.0f} | "
        f"drifts {report.drift_count} | planned {report.actions_planned} | "
        f"executed {report.actions_executed} | failures {report.execution_failures} | "
        f"kill_switch={report.kill_switch_active}"
    )
    return 1 if report.execution_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
