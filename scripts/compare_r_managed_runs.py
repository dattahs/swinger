#!/usr/bin/env python3
"""Compare baseline vs R-managed runner backtest outputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def analyze(run_dir: Path) -> dict:
    summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
    closed = pd.read_csv(run_dir / "closed_trades.csv") if (run_dir / "closed_trades.csv").exists() else pd.DataFrame()
    decisions = pd.read_csv(run_dir / "decision_log.csv")
    trail = decisions[decisions["action_type"] == "TRAIL_OCO"] if not decisions.empty else pd.DataFrame()
    out = dict(summary)
    if not closed.empty:
        out["stop_exits"] = int((closed["exit_reason"] == "STOP_LOSS_HIT").sum())
        out["target_exits"] = int((closed["exit_reason"] == "TARGET_HIT").sum())
        out["total_pnl"] = float(closed["pnl"].sum())
        out["avg_winner"] = float(closed.loc[closed["pnl"] > 0, "pnl"].mean())
        out["avg_loser"] = float(closed.loc[closed["pnl"] <= 0, "pnl"].mean())
    out["trail_oco_actions"] = len(trail)
    return out


def main() -> None:
    baseline = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "backtest_outputs/run_20260623_210959"
    rrm = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "backtest_outputs/run_20260623_212702"

    b = analyze(baseline)
    r = analyze(rrm)

    print("Metric                    Baseline        R-managed       Delta")
    print("-" * 62)
    for key in [
        "cagr",
        "final_equity_inr",
        "max_drawdown_pct",
        "total_closed_trades",
        "win_rate",
        "total_pnl",
        "stop_exits",
        "target_exits",
        "trail_oco_actions",
        "avg_winner",
        "avg_loser",
    ]:
        if key not in b and key not in r:
            continue
        bv = b.get(key, 0)
        rv = r.get(key, 0)
        if isinstance(bv, float):
            delta = rv - bv
            print(f"{key:24} {bv:14.4f} {rv:14.4f} {delta:+.4f}")
        else:
            print(f"{key:24} {bv!s:>14} {rv!s:>14} {rv - bv if isinstance(bv, (int, float)) else ''}")

    b_closed = pd.read_csv(baseline / "closed_trades.csv")
    r_closed = pd.read_csv(rrm / "closed_trades.csv")
    if b_closed.equals(r_closed):
        print("\nclosed_trades.csv: IDENTICAL")
    else:
        merged = b_closed.merge(
            r_closed,
            on=["symbol", "entry_date"],
            suffixes=("_b", "_r"),
            how="outer",
            indicator=True,
        )
        print("\nclosed_trades merge:", merged["_merge"].value_counts().to_dict())
        both = merged[merged["_merge"] == "both"]
        if not both.empty:
            both = both.copy()
            both["pnl_delta"] = both["pnl_r"] - both["pnl_b"]
            print(f"Matched trades with PnL change: {(both['pnl_delta'] != 0).sum()} / {len(both)}")
            print(f"Total PnL delta: {both['pnl_delta'].sum():,.2f}")


if __name__ == "__main__":
    main()
