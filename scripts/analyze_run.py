#!/usr/bin/env python3
"""Full analysis of a debug-log backtest run: selections, rejections, box funnel."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _reason(row: pd.Series) -> str | None:
    raw = row.get("details_json")
    if pd.isna(raw) or not raw:
        return None
    try:
        return json.loads(raw).get("reason_code")
    except (json.JSONDecodeError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()

    log = pd.read_csv(run / "action_debug.csv")
    dec = pd.read_csv(run / "decision_log.csv", parse_dates=["date"])
    log["reason_code"] = log.apply(_reason, axis=1)

    days = dec["date"].nunique()
    print(f"=== Run {run.name} ===")
    print(f"Period: {dec['date'].min().date()} to {dec['date'].max().date()} ({days} sessions)")
    ledger = run / "trade_ledger.csv"
    trades = 0
    if ledger.exists() and ledger.stat().st_size > 10:
        trades = len(pd.read_csv(ledger))
    print(f"Trades: {trades}")

    print("\n--- Box funnel (decision log symbol-days) ---")
    for state, n in dec.groupby("box_state").size().sort_values(ascending=False).items():
        print(f"  {state}: {n}")

    validated_syms = dec.loc[dec["box_state"] == "VALIDATED", "symbol"].unique()
    breakout_days = dec.loc[dec["box_state"] == "BREAKOUT"]
    print(f"\n  VALIDATED unique symbols: {len(validated_syms)}")
    print(f"  BREAKOUT symbol-days: {len(breakout_days)}")

    print("\n--- Debug log actions ---")
    for (cat, act), n in log.groupby(["category", "action"]).size().sort_values(ascending=False).head(15).items():
        print(f"  {cat}/{act}: {n}")

    rejects = log[log["action"] == "REJECT"]
    print("\n--- All rejections by reason_code ---")
    for code, n in Counter(rejects["reason_code"].fillna("(no code)")).most_common():
        print(f"  {code}: {n}")

    print("\n--- Rejections by category ---")
    for (cat, code), n in (
        rejects.groupby(["category", "reason_code"]).size().sort_values(ascending=False).head(20).items()
    ):
        print(f"  {cat}/{code}: {n}")

    selects = log[(log["category"] == "RANK") & (log["action"] == "SELECT")]
    print(f"\n--- Selections: {len(selects)} ---")
    if not selects.empty:
        print(selects[["date", "symbol", "message"]].to_string(index=False))

    considers = log[(log["category"] == "BREAKOUT") & (log["action"] == "CONSIDER")]
    print(f"\n--- Breakout considered: {len(considers)} ---")

    trans = log[(log["category"] == "BOX") & (log["action"] == "TRANSITION")].copy()
    trans["to_state"] = trans["message"].str.extract(r"→ (\w+)")[0]
    print("\n--- Box transitions (destination) ---")
    for s, n in trans["to_state"].value_counts().items():
        print(f"  -> {s}: {n}")

    sess = log[log["action"] == "OPEN"]
    bulls: list[int] = []
    for raw in sess["details_json"].dropna():
        try:
            bulls.append(int(json.loads(raw).get("sector_trend_bullish", 0)))
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    if bulls:
        print(f"\n--- Sector bullish count per session ---")
        print(f"  min={min(bulls)}  max={max(bulls)}  avg={sum(bulls)/len(bulls):.0f}  (of ~512 universe)")

    # Breakout candidates that failed universe/fundamental filters
    bo = dec[dec["box_state"] == "BREAKOUT"]
    if not bo.empty:
        print("\n--- Breakout rows with filter failures ---")
        print(bo["filter_fail_reason"].value_counts().head(10).to_string())

    if validated_syms.size:
        print(f"\n--- Symbols with VALIDATED days ({len(validated_syms)}) ---")
        val_counts = dec[dec["box_state"] == "VALIDATED"].groupby("symbol").size().sort_values(ascending=False)
        print(val_counts.head(20).to_string())


if __name__ == "__main__":
    main()
