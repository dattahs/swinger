#!/usr/bin/env python3
"""Extended insights for a backtest run."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.monthly_analysis import build_monthly_table, load_run_frames, print_monthly_table


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()

    dec = pd.read_csv(run / "decision_log.csv", parse_dates=["date"])
    eq = pd.read_csv(run / "equity_curve.csv", parse_dates=["date"])
    summary = json.loads((run / "summary_report.json").read_text())

    log_path = run / "action_debug.csv"
    log = pd.read_csv(log_path) if log_path.exists() else pd.DataFrame()

    def reason(row):
        try:
            return json.loads(row["details_json"]).get("reason_code") if pd.notna(row.get("details_json")) else None
        except Exception:
            return None

    if not log.empty:
        log["reason_code"] = log.apply(reason, axis=1)

    print("=" * 60)
    print(f"INSIGHTS: {run.name}")
    print(f"Period: {summary['start_date']} to {summary['end_date']}")
    print(f"Final equity: INR {summary['final_equity_inr']:,.0f}  |  Trades: {summary['total_closed_trades']}")
    print(f"CAGR: {summary['cagr']:.2%}  |  Max DD: {summary['max_drawdown_pct']:.2f}%")
    print("=" * 60)

    try:
        eq_m, closed, open_buys = load_run_frames(run)
        monthly = build_monthly_table(eq_m, closed, open_buys)
        print_monthly_table(monthly)
    except FileNotFoundError as exc:
        print(f"\n(Monthly trade breakdown skipped: {exc})")

    days = dec["date"].nunique()
    print(f"\nSessions: {days}")

    print("\n--- Box funnel ---")
    for s, n in dec.groupby("box_state").size().sort_values(ascending=False).items():
        print(f"  {s}: {n:,} symbol-days")

    bo = dec[dec["box_state"] == "BREAKOUT"]
    val = dec[dec["box_state"] == "VALIDATED"]
    print(f"\n  Unique BREAKOUT symbols: {bo['symbol'].nunique()}")
    print(f"  Unique VALIDATED symbols: {val['symbol'].nunique()}")

    if not bo.empty:
        print("\n--- Breakout symbols (symbol-days) ---")
        print(bo.groupby("symbol").size().sort_values(ascending=False).head(20).to_string())

    # Gate rejects
    if not log.empty:
        gate = log[(log["category"] == "GATE") & (log["action"] == "REJECT")]
        print("\n--- Gate rejections ---")
        for code, n in Counter(gate["reason_code"]).most_common():
            print(f"  {code}: {n:,}")

        # Post-breakout rejections
        all_rej = log[log["action"] == "REJECT"]
        print("\n--- All rejection categories ---")
        for (cat, code), n in all_rej.groupby(["category", "reason_code"]).size().sort_values(ascending=False).head(15).items():
            print(f"  {cat}/{code}: {n}")

        # Selections
        sel = log[(log["category"] == "RANK") & (log["action"] == "SELECT")]
        print(f"\n--- Selections: {len(sel)} ---")
        if not sel.empty:
            print(sel[["date", "symbol", "message"]].to_string(index=False))

        # Breakout considers + downstream
        cons = log[(log["category"] == "BREAKOUT") & (log["action"] == "CONSIDER")]
        print(f"\n--- Breakouts considered: {len(cons)} ---")
        if not cons.empty:
            print(cons.groupby("symbol").size().sort_values(ascending=False).head(15).to_string())

        risk_rej = log[(log["category"] == "RISK") & (log["action"] == "REJECT")]
        if not risk_rej.empty:
            print("\n--- Risk rejections ---")
            for code, n in Counter(risk_rej["reason_code"]).most_common():
                print(f"  {code}: {n}")

        filt_rej = log[(log["category"] == "FILTER") & (log["action"] == "REJECT")]
        if not filt_rej.empty:
            print("\n--- Filter rejections on breakouts ---")
            for code, n in Counter(filt_rej["reason_code"]).most_common(10):
                print(f"  {code}: {n}")

        # Sector bullish trend over time
        bulls = []
        for _, r in log[log["action"] == "OPEN"].iterrows():
            try:
                d = json.loads(r["details_json"])
                bulls.append((r["date"], d.get("sector_trend_bullish", 0)))
            except Exception:
                pass
        if bulls:
            bdf = pd.DataFrame(bulls, columns=["date", "bullish"])
            print("\n--- Sector bullish (50 MA) per session ---")
            print(f"  min={bdf['bullish'].min()}  max={bdf['bullish'].max()}  avg={bdf['bullish'].mean():.0f}")
            print("  By month:")
            bdf["date"] = pd.to_datetime(bdf["date"])
            print(bdf.groupby(bdf["date"].dt.to_period("M"))["bullish"].mean().round(0).to_string())

    # Filter fail on breakout days in decision log
    bo_dec = dec[dec["box_state"] == "BREAKOUT"]
    if not bo_dec.empty:
        print("\n--- Breakout decision log filter failures ---")
        print(bo_dec["filter_fail_reason"].value_counts(dropna=False).head(10).to_string())

    # Progress: breakouts per day from progress log if available
    prog = run / "progress.log"
    if prog.exists():
        lines = [ln for ln in prog.read_text(encoding="utf-8", errors="replace").splitlines() if "breakouts=" in ln]
        if lines:
            bo_days = sum(1 for ln in lines if "breakouts=" in ln and "breakouts=0" not in ln.split("|")[2])
            print(f"\n--- Sessions with >=1 breakout: {bo_days} / {len(lines)} ---")

    # Trades
    ledger = run / "trade_ledger.csv"
    if ledger.exists() and ledger.stat().st_size > 10:
        trades = pd.read_csv(ledger)
        print(f"\n--- Trade ledger ({len(trades)} rows) ---")
        if not trades.empty:
            print(trades[["date", "symbol", "direction", "quantity", "price"]].to_string(index=False))


if __name__ == "__main__":
    main()
