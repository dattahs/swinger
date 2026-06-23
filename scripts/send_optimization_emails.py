#!/usr/bin/env python3
"""Send optimization experiment summary emails from experiment-log.jsonl."""

from __future__ import annotations

import argparse
import json
import smtplib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.notify.backtest_email import (  # noqa: E402
    BacktestRunIncompleteError,
    load_email_settings_from_env,
    send_backtest_results_email,
)

LOG_PATH = ROOT / "src" / "agentic-loop" / "experiment-log.jsonl"


def load_experiments(log_path: Path) -> list[dict]:
    if not log_path.is_file():
        raise FileNotFoundError(f"No experiment log at {log_path}")
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=LOG_PATH,
        help="Path to experiment-log.jsonl",
    )
    parser.add_argument("--to", action="append", dest="to_addrs", help="Recipient email")
    parser.add_argument("--iteration", type=int, action="append", help="Only send this iteration")
    parser.add_argument("--dry-run", action="store_true", help="Build messages only")
    parser.add_argument("--keep-zip", action="store_true", help="Keep zip files after send")
    args = parser.parse_args()

    try:
        experiments = load_experiments(args.log)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.iteration:
        wanted = set(args.iteration)
        experiments = [e for e in experiments if e.get("iteration") in wanted]

    if not experiments:
        print("No experiments to email", file=sys.stderr)
        return 2

    try:
        settings = load_email_settings_from_env()
        if args.to_addrs:
            from dataclasses import replace

            settings = replace(settings, to_addrs=tuple(args.to_addrs))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    sent = 0
    for exp in experiments:
        run_dir = Path(exp["run_dir"])
        label = f"#{exp.get('iteration')} {exp.get('name')}"
        try:
            send_backtest_results_email(
                run_dir,
                settings,
                dry_run=args.dry_run,
                keep_zip=args.keep_zip,
                experiment=exp,
            )
        except BacktestRunIncompleteError as exc:
            print(f"Skip {label}: {exc}", file=sys.stderr)
            continue
        except smtplib.SMTPException as exc:
            print(f"SMTP error for {label}: {exc}", file=sys.stderr)
            return 1

        action = "Would send" if args.dry_run else "Sent"
        print(f"{action} email for {label} -> {', '.join(settings.to_addrs)}")
        sent += 1

    print(f"Done: {sent}/{len(experiments)} emails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
