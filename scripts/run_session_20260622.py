#!/usr/bin/env python3
"""First paper live session — 22 Jun 2026 with Upstox login + broker reconcile."""

from __future__ import annotations

import argparse
import json
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

SESSION_DATE = date(2026, 6, 22)
SESSION_DB = ROOT / "data" / "live" / "sessions" / "run_20260622.db"
SESSION_REPORT = ROOT / "data" / "live" / "sessions" / "run_20260622_report.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper live session 2026-06-22")
    parser.add_argument("--login", action="store_true", help="Open Upstox browser login")
    parser.add_argument("--prepare-only", action="store_true", help="Warm Darvas state only (no broker)")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument(
        "--force-warmup",
        action="store_true",
        help="Rebuild Darvas warmup cache (Darvas logic changes only)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / ".env")
    import os

    if args.login and not os.environ.get("UPSTOX_API_KEY", "").strip():
        print("UPSTOX_API_KEY missing — copy .env.example to .env and fill Upstox app credentials.")
        return 2

    if not args.prepare_only:
        has_creds = bool(os.environ.get("UPSTOX_API_KEY") or os.environ.get("UPSTOX_ACCESS_TOKEN"))
        token_file = ROOT / "data" / "live" / "upstox_token.json"
        if not args.login and not has_creds and not token_file.exists():
            print("No Upstox credentials found. Re-run with --login after filling .env")
            print(f"  copy {ROOT / '.env.example'} -> {ROOT / '.env'}")
            return 2

    cfg = load_config_relaxed(ROOT / "config.yaml")
    cfg.live.paper_mode = True
    cfg.live.local_db_path = str(SESSION_DB.relative_to(ROOT)).replace("\\", "/")
    cfg.live.warmup_from = date(2025, 10, 1)
    cfg.live.initial_capital_inr = 100_000.0

    SESSION_DB.parent.mkdir(parents=True, exist_ok=True)

    runner = LiveRunner(
        cfg,
        repo_root=ROOT,
        force_login=args.login,
        skip_warmup=args.no_warmup,
        force_warmup=args.force_warmup,
    )

    if args.prepare_only:
        pricing = runner._resolve_pricing_date(SESSION_DATE)  # noqa: SLF001
        runner._warm_state_registry(SESSION_DATE, pricing)  # noqa: SLF001
        n = len(runner.repo.get_state_registry())
        print(f"Prepared Darvas state for {SESSION_DATE}: {n} symbols (pricing {pricing})")
        print(f"Live DB: {SESSION_DB}")
        return 0

    report = runner.run(SESSION_DATE)

    pending = runner.repo.get_system_state("pending_gtts") or {}
    broker_sync = runner.repo.get_system_state("broker_sync") or {}

    actions_detail = []
    for key, meta in (runner.repo.get_system_state("executed_idempotency") or {}).items():
        if meta.get("date") == SESSION_DATE.isoformat():
            actions_detail.append(meta)

    out = {
        "session_date": report.session_date.isoformat(),
        "pricing_note": "Strategy bars use latest ingested EOD if session is forward-dated",
        "equity_inr": report.equity_inr,
        "reconciliation_synced": report.reconciliation_synced,
        "drift_count": report.drift_count,
        "actions_planned": report.actions_planned,
        "actions_executed": report.actions_executed,
        "execution_failures": report.execution_failures,
        "kill_switch_active": report.kill_switch_active,
        "broker_sync": broker_sync,
        "pending_gtts": pending,
        "executed_today": actions_detail,
        "live_db": str(SESSION_DB),
    }
    SESSION_REPORT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print("=" * 70)
    print(f"PAPER LIVE SESSION: {SESSION_DATE.isoformat()}  (paper_mode=true)")
    print(f"Live DB: {SESSION_DB}")
    print(f"Report:  {SESSION_REPORT}")
    print("=" * 70)
    print(
        f"Equity INR {report.equity_inr:,.0f} | broker cash INR {broker_sync.get('settled_cash_inr', 0):,.0f}"
    )
    print(
        f"Drifts {report.drift_count} | planned {report.actions_planned} | "
        f"executed {report.actions_executed} | failures {report.execution_failures}"
    )
    if pending:
        print("\nPending paper GTTs:")
        for sym, row in sorted(pending.items()):
            print(
                f"  {sym}: trigger {row.get('trigger_price')} "
                f"stop {row.get('stop_loss_price')} target {row.get('target_price')} "
                f"qty {row.get('quantity')} id {row.get('gtt_order_id')}"
            )
    else:
        print("\nNo new pending GTTs placed this session.")
    print("=" * 70)
    return 1 if report.execution_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
