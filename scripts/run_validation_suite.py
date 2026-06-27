#!/usr/bin/env python3
"""Run comprehensive backtest validation suite and email results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.validation.suite import run_validation_suite
from src.notify.backtest_email import load_email_settings_from_env
from src.notify.validation_email import send_validation_report_email


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml", help="Base strategy config")
    parser.add_argument("--output-dir", type=Path, help="Validation output directory root")
    parser.add_argument("--no-email", action="store_true", help="Skip email delivery")
    parser.add_argument("--dry-run-email", action="store_true", help="Build email only")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = ROOT / config_path

    out_dir, report_md = run_validation_suite(config_path, repo_root=ROOT, output_dir=args.output_dir)
    try:
        print(report_md)
    except UnicodeEncodeError:
        print(report_md.encode("ascii", errors="replace").decode("ascii"))

    if args.no_email:
        print(f"\nSkipped email. Report at {out_dir / 'validation_report.md'}")
        return 0

    try:
        settings = load_email_settings_from_env()
        send_validation_report_email(
            out_dir,
            report_md,
            settings,
            dry_run=args.dry_run_email,
        )
        if args.dry_run_email:
            print("\n[dry-run] Email not sent.")
        else:
            print(f"\nValidation report emailed to {', '.join(settings.to_addrs)}")
    except Exception as exc:
        print(f"\nEmail failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
