#!/usr/bin/env python3
"""Send a backtest run summary email with zipped raw artifacts."""

from __future__ import annotations

import argparse
import smtplib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.notify.backtest_email import (  # noqa: E402
    BacktestRunIncompleteError,
    BacktestRunNotFoundError,
    build_insights,
    create_run_zip,
    resolve_email_settings,
    resolve_run_directory,
    send_backtest_results_email,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", nargs="?", help="Backtest output directory (run_*)")
    parser.add_argument("--latest", action="store_true", help="Use newest run under export directory")
    parser.add_argument(
        "--export-directory",
        type=Path,
        default=ROOT / "backtest_outputs",
        help="Base directory containing run_* folders",
    )
    parser.add_argument("--to", action="append", dest="to_addrs", help="Recipient email (repeatable)")
    parser.add_argument("--from-addr", dest="from_addr", help="Sender email address")
    parser.add_argument("--smtp-host", help="SMTP host (overrides SWINGER_SMTP_HOST)")
    parser.add_argument("--smtp-port", type=int, help="SMTP port (overrides SWINGER_SMTP_PORT)")
    parser.add_argument("--smtp-user", help="SMTP username")
    parser.add_argument("--smtp-password", help="SMTP password")
    parser.add_argument("--no-tls", action="store_true", help="Disable STARTTLS")
    parser.add_argument("--dry-run", action="store_true", help="Build message only; do not send")
    parser.add_argument("--print-body", action="store_true", help="Print insight body to stdout")
    parser.add_argument("--keep-zip", action="store_true", help="Keep temporary zip after send")
    parser.add_argument("--zip-only", type=Path, help="Write zip to this path and exit")
    args = parser.parse_args()

    try:
        run_dir = resolve_run_directory(
            args.run_dir,
            export_directory=args.export_directory,
            latest=args.latest or args.run_dir is None,
        )
    except (BacktestRunNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.zip_only:
        try:
            zip_path = create_run_zip(run_dir, args.zip_only)
        except BacktestRunIncompleteError as exc:
            print(exc, file=sys.stderr)
            return 2
        print(zip_path)
        return 0

    if args.print_body or args.dry_run:
        try:
            plain, _ = build_insights(run_dir)
        except BacktestRunIncompleteError as exc:
            print(exc, file=sys.stderr)
            return 2
        if args.print_body:
            print(plain)

    if args.dry_run and not args.print_body:
        try:
            plain, _ = build_insights(run_dir)
            print(plain)
        except BacktestRunIncompleteError as exc:
            print(exc, file=sys.stderr)
            return 2
        zip_path = create_run_zip(run_dir)
        print(f"\n[dry-run] Would attach: {zip_path} ({zip_path.stat().st_size:,} bytes)")
        if not args.keep_zip:
            zip_path.unlink(missing_ok=True)
        return 0

    try:
        settings = resolve_email_settings(
            smtp_host=args.smtp_host,
            smtp_port=args.smtp_port,
            smtp_user=args.smtp_user,
            smtp_password=args.smtp_password,
            from_addr=args.from_addr,
            to_addrs=tuple(args.to_addrs) if args.to_addrs else None,
            use_tls=False if args.no_tls else None,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2

    try:
        send_backtest_results_email(
            run_dir,
            settings,
            dry_run=False,
            keep_zip=args.keep_zip,
        )
    except BacktestRunIncompleteError as exc:
        print(exc, file=sys.stderr)
        return 2
    except smtplib.SMTPException as exc:
        print(f"SMTP error: {exc}", file=sys.stderr)
        return 1

    print(f"Sent backtest email for {run_dir.name} to {', '.join(settings.to_addrs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
