#!/usr/bin/env python3
"""Analyze backtest closed trades and monthly P&L."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def analyze(run_dir: Path) -> dict:
    closed_path = run_dir / "closed_trades.csv"
    eq_path = run_dir / "equity_curve.csv"
    summary_path = run_dir / "summary_report.json"

    if not closed_path.is_file():
        raise FileNotFoundError(f"No closed_trades.csv in {run_dir}")

    trades = pd.read_csv(closed_path)
    eq = pd.read_csv(eq_path, parse_dates=["date"]) if eq_path.is_file() else pd.DataFrame()
    summary = json.loads(summary_path.read_text()) if summary_path.is_file() else {}

    if trades.empty:
        return {"error": "no closed trades", "summary": summary}

    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] <= 0]

    best = trades.loc[trades["pnl"].idxmax()]
    worst = trades.loc[trades["pnl"].idxmin()]

    monthly = trades.copy()
    monthly["month"] = monthly["exit_date"].dt.to_period("M")
    by_month = (
        monthly.groupby("month")
        .agg(
            trades_closed=("pnl", "count"),
            wins=("pnl", lambda s: (s > 0).sum()),
            losses=("pnl", lambda s: (s <= 0).sum()),
            total_pnl=("pnl", "sum"),
            avg_win=("pnl", lambda s: s[s > 0].mean() if (s > 0).any() else 0.0),
            avg_loss=("pnl", lambda s: s[s <= 0].mean() if (s <= 0).any() else 0.0),
        )
        .reset_index()
    )
    by_month["month"] = by_month["month"].astype(str)

    return {
        "summary": summary,
        "total_closed": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(trades), 1),
        "total_pnl_inr": round(float(trades["pnl"].sum()), 2),
        "avg_profit_per_win_inr": round(float(wins["pnl"].mean()), 2) if len(wins) else 0.0,
        "avg_loss_per_loss_inr": round(float(losses["pnl"].mean()), 2) if len(losses) else 0.0,
        "avg_pnl_per_trade_inr": round(float(trades["pnl"].mean()), 2),
        "biggest_winner": {
            "symbol": best["symbol"],
            "pnl_inr": round(float(best["pnl"]), 2),
            "entry_date": str(best["entry_date"].date()),
            "exit_date": str(best["exit_date"].date()),
            "exit_reason": best.get("exit_reason", ""),
        },
        "biggest_loser": {
            "symbol": worst["symbol"],
            "pnl_inr": round(float(worst["pnl"]), 2),
            "entry_date": str(worst["entry_date"].date()),
            "exit_date": str(worst["exit_date"].date()),
            "exit_reason": worst.get("exit_reason", ""),
        },
        "monthly": by_month.to_dict(orient="records"),
        "final_equity_inr": float(eq.iloc[-1]["equity"]) if not eq.empty else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", nargs="?", help="Backtest output directory")
    parser.add_argument("--latest", action="store_true", help="Use newest run in backtest_outputs")
    args = parser.parse_args()

    if args.latest or not args.run_dir:
        base = ROOT / "backtest_outputs"
        runs = sorted(base.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not runs:
            print("No backtest runs found")
            return 2
        run_dir = runs[0]
    else:
        run_dir = Path(args.run_dir)

    out = analyze(run_dir)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
