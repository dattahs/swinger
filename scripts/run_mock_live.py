#!/usr/bin/env python3
"""Mock-live trial runner — simulates Upstox with bhavcopy EOD fills.

Run manually after each market close. Day 1 uses the latest ingested EOD session;
subsequent days pass --date YYYY-MM-DD after fresh bhavcopy ingest.

Example (10-day trial, ₹5L):
  python scripts/run_mock_live.py              # Day 1 — latest EOD in data lake
  python scripts/run_mock_live.py --date 2026-06-20   # Day 2 after ingest
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.broker.env import load_dotenv
from src.config import load_config_relaxed
from src.live.runner import LiveRunner
from src.models import ActionType
from src.repository.sqlite import SqliteDataLake

TRIAL_DIR = ROOT / "data" / "live" / "mock_trial_10d"
TRIAL_DB = TRIAL_DIR / "trial.db"
MANIFEST = TRIAL_DIR / "manifest.json"
MAX_TRIAL_DAYS = 10


def _load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {
        "trial_name": "mock_live_10d",
        "capital_inr": 500_000.0,
        "max_days": MAX_TRIAL_DAYS,
        "sessions": [],
    }


def _save_manifest(manifest: dict) -> None:
    TRIAL_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _report_path(session: date) -> Path:
    return TRIAL_DIR / f"session_{session.strftime('%Y%m%d')}_report.json"


def _gtt_placed_today(db_path: Path, session: date) -> list[dict]:
    import sqlite3

    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT symbol, trigger_price, stop_loss_price, target_price, quantity, structural_rr, selected "
        "FROM decision_log WHERE date = ? AND action_type = ? AND selected = 1",
        (session.isoformat(), ActionType.PLACE_BUY_GTT.value),
    )
    rows = [
        {
            "symbol": row[0],
            "trigger": row[1],
            "stop": row[2],
            "target": row[3],
            "quantity": row[4],
            "structural_rr": row[5],
            "selected": True,
        }
        for row in cur.fetchall()
    ]
    conn.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Swinger mock-live trial (bhavcopy Upstox sim)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--date", help="Session date YYYY-MM-DD (default: latest EOD in data lake)")
    parser.add_argument("--capital", type=float, default=500_000.0, help="Starting capital INR")
    parser.add_argument("--reset", action="store_true", help="Delete trial DB and restart from day 1")
    parser.add_argument("--force-warmup", action="store_true", help="Rebuild Darvas warmup cache")
    parser.add_argument("--no-warmup", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.reset and TRIAL_DB.exists():
        TRIAL_DB.unlink()
        if MANIFEST.exists():
            MANIFEST.unlink()
        for p in TRIAL_DIR.glob("session_*_report.json"):
            p.unlink()
        print(f"Reset trial state in {TRIAL_DIR}")

    load_dotenv(ROOT / ".env")
    cfg = load_config_relaxed(ROOT / args.config)
    cfg.live.paper_mode = True
    cfg.live.mock_broker = True
    cfg.live.allow_drift = True
    cfg.live.initial_capital_inr = args.capital
    cfg.live.local_db_path = str(TRIAL_DB.relative_to(ROOT)).replace("\\", "/")

    dl = SqliteDataLake(ROOT / cfg.backtest.data_db_path)
    session = date.fromisoformat(args.date) if args.date else dl.get_latest_trading_day()
    if session is None:
        print("No EOD data in data lake — run bhavcopy ingest first.")
        return 2

    manifest = _load_manifest()
    manifest["capital_inr"] = args.capital
    prior_dates = {s["date"] for s in manifest.get("sessions", [])}
    day_num = len(manifest.get("sessions", [])) + 1
    if session.isoformat() in prior_dates and not args.reset:
        day_num = next(s["day"] for s in manifest["sessions"] if s["date"] == session.isoformat())
    if day_num > MAX_TRIAL_DAYS and not args.date:
        print(f"Trial complete ({MAX_TRIAL_DAYS} sessions). Use --reset to start over or --date for ad-hoc.")
        return 0

    TRIAL_DIR.mkdir(parents=True, exist_ok=True)

    runner = LiveRunner(
        cfg,
        repo_root=ROOT,
        skip_warmup=args.no_warmup,
        force_warmup=args.force_warmup,
    )
    report = runner.run(session)

    pending = runner.repo.get_system_state("pending_gtts") or {}
    broker_sync = runner.repo.get_system_state("broker_sync") or {}
    mock_book = runner.repo.get_system_state("mock_broker_book") or {}
    open_positions = [
        {
            "symbol": p.symbol,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
            "stop": p.current_stop_loss,
            "target": p.current_target,
        }
        for p in runner.repo.get_open_positions()
    ]

    executed_today = []
    for _key, meta in (runner.repo.get_system_state("executed_idempotency") or {}).items():
        if meta.get("date") == session.isoformat():
            executed_today.append(meta)

    gtt_placed = _gtt_placed_today(TRIAL_DB, session)
    fills_today = report.fills_today or []

    out = {
        "trial_day": day_num,
        "session_date": report.session_date.isoformat(),
        "ran_at": datetime.now().isoformat(timespec="seconds"),
        "equity_inr": report.equity_inr,
        "settled_cash_inr": broker_sync.get("settled_cash_inr"),
        "reconciliation_synced": report.reconciliation_synced,
        "drift_count": report.drift_count,
        "actions_planned": report.actions_planned,
        "actions_executed": report.actions_executed,
        "execution_failures": report.execution_failures,
        "kill_switch_active": report.kill_switch_active,
        "fills_today": fills_today,
        "gtt_placed_today": [g for g in gtt_placed if g.get("selected")],
        "pending_gtts": pending,
        "open_positions": open_positions,
        "executed_actions": executed_today,
        "mock_broker_cash": mock_book.get("settled_cash_inr"),
        "live_db": str(TRIAL_DB),
    }

    report_path = _report_path(session)
    report_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    manifest["sessions"] = [s for s in manifest.get("sessions", []) if s["date"] != session.isoformat()]
    manifest["sessions"].append(
        {
            "day": day_num,
            "date": session.isoformat(),
            "ran_at": out["ran_at"],
            "report": str(report_path.relative_to(ROOT)).replace("\\", "/"),
            "equity_inr": report.equity_inr,
            "pending_gtt_count": len(pending),
            "open_positions": len(open_positions),
        }
    )
    _save_manifest(manifest)

    print("=" * 72)
    print(f"MOCK-LIVE TRIAL — Day {day_num}/{MAX_TRIAL_DAYS}  |  Session {session.isoformat()}")
    print(f"Capital INR {args.capital:,.0f}  |  Equity INR {report.equity_inr:,.0f}  |  Cash INR {float(broker_sync.get('settled_cash_inr', 0)):,.0f}")
    print(f"DB: {TRIAL_DB}")
    print(f"Report: {report_path}")
    print("=" * 72)

    if fills_today:
        print("\nFills today (mock Upstox):")
        for f in fills_today:
            print(f"  {f}")
    else:
        print("\nNo fills today.")

    if gtt_placed:
        selected = [g for g in gtt_placed if g.get("selected")]
        print(f"\nGTT decisions today ({len(selected)} placed):")
        for g in selected:
            print(
                f"  {g['symbol']:12} trigger {g['trigger']:>10.2f}  stop {g['stop']:>10.2f}  "
                f"target {g['target']:>10.2f}  qty {g['quantity']:>4}  RR {g.get('structural_rr', 0):.3f}"
            )
    else:
        print("\nNo PLACE_BUY_GTT actions today.")

    if pending:
        print(f"\nPending GTTs ({len(pending)}):")
        for sym, row in sorted(pending.items()):
            print(
                f"  {sym}: trigger {row.get('trigger_price')}  stop {row.get('stop_loss_price')}  "
                f"qty {row.get('quantity')}  id {row.get('gtt_order_id')}  placed {row.get('placed_date')}"
            )

    if open_positions:
        print(f"\nOpen positions ({len(open_positions)}):")
        for p in open_positions:
            print(
                f"  {p['symbol']}: {p['quantity']} @ {p['entry_price']:.2f}  "
                f"stop {p['stop']:.2f}  target {p['target']:.2f}"
            )

    print("=" * 72)
    if day_num < MAX_TRIAL_DAYS:
        trading = dl.get_trading_days(session, date(2099, 12, 31))
        next_hint = trading[1].isoformat() if len(trading) > 1 else "(ingest next EOD then run)"
        print(f"Next: after market close, ingest bhavcopy, then:")
        print(f"  python scripts/run_mock_live.py --date {next_hint}")
    else:
        print("Trial complete. Review reports under data/live/mock_trial_10d/")
    print("=" * 72)

    return 1 if report.execution_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
