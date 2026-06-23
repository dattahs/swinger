#!/usr/bin/env python3
"""Preview GTT placements and open book for a single session (forward-looking)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester, _resolve_path
from src.config import load_config
from src.models import ActionType


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview trades for a session date")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--session", required=True, help="Target session YYYY-MM-DD")
    parser.add_argument("--warmup-from", default="2025-10-01", help="Backtest warmup start")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital INR")
    args = parser.parse_args()

    session = date.fromisoformat(args.session)
    warmup = date.fromisoformat(args.warmup_from)

    config_path = ROOT / args.config
    cfg = load_config(config_path)
    cfg.backtest.initial_capital_inr = args.capital
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False

    bt = Backtester(cfg, repo_root=ROOT)
    out = bt.run(start=warmup, end=session)

    # Last session decisions
    import pandas as pd

    dec = pd.read_csv(out / "decision_log.csv", parse_dates=["date"])
    last = dec[dec["date"].dt.date == session]
    selected = last[last["selected"] == True].copy()  # noqa: E712

    ledger = pd.read_csv(out / "trade_ledger.csv")
    open_buys = ledger[(ledger["direction"] == "BUY") & (ledger["is_active"] == 1)]

    eq = pd.read_csv(out / "equity_curve.csv", parse_dates=["date"])
    last_eq = eq[eq["date"].dt.date == session].iloc[-1]

    summary = json.loads((out / "summary_report.json").read_text())

    print("=" * 70)
    print(f"SESSION PREVIEW: {session.isoformat()}")
    print(f"Initial capital: INR {args.capital:,.0f}  |  Warmup from: {warmup.isoformat()}")
    print(f"Equity at close: INR {last_eq['equity']:,.0f}")
    print(f"Settled cash:    INR {last_eq['settled_cash']:,.0f}")
    print(f"Open positions:  {int(last_eq['open_positions_count'])}")
    print("=" * 70)

    print(f"\n--- New BUY GTT orders placed on {session} ({len(selected)}) ---")
    if selected.empty:
        print("  (none)")
    else:
        cols = [
            "symbol",
            "trigger_price",
            "stop_loss_price",
            "target_price",
            "quantity",
            "structural_rr",
        ]
        print(selected[cols].to_string(index=False))

    print(f"\n--- Open positions after {session} ---")
    if open_buys.empty:
        print("  (none)")
    else:
        print(
            open_buys[["symbol", "quantity", "price", "current_stop_loss", "current_target"]].to_string(
                index=False
            )
        )

    print(f"\n--- Skip reasons on breakout candidates ({session}) ---")
    bo = last[(last["box_state"] == "BREAKOUT") & last["skip_reason"].notna()]
    if not bo.empty:
        print(bo["skip_reason"].value_counts().head(8).to_string())

    print(f"\nRun output: {out}")


if __name__ == "__main__":
    main()
