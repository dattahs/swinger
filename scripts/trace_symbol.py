#!/usr/bin/env python3
"""Trace a symbol's journey through a debug-log backtest run."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--from", dest="from_date", default="2025-01-01")
    args = parser.parse_args()

    log = pd.read_csv(args.run_dir / "action_debug.csv")
    dec = pd.read_csv(args.run_dir / "decision_log.csv", parse_dates=["date"])
    sym = args.symbol.upper()

    print(f"=== {sym} decision log (box state changes) ===")
    d = dec[dec["symbol"] == sym].copy()
    d = d[d["date"] >= pd.Timestamp(args.from_date)]
    prev = None
    for _, r in d.iterrows():
        st = r["box_state"]
        if st != prev:
            top = r["box_top"]
            bot = r["box_bottom"]
            bounds = f" [{bot:.2f}-{top:.2f}]" if pd.notna(top) and pd.notna(bot) else ""
            filt = ""
            if r["box_state"] == "BREAKOUT" and pd.notna(r.get("filter_fail_reason")):
                filt = f" filter_fail={r['filter_fail_reason']}"
            print(f"  {r['date'].date()}  {st}{bounds}{filt}")
            prev = st

    print(f"\n=== {sym} action log ===")
    ev = log[(log["symbol"] == sym) & (log["date"] >= args.from_date)]
    ev = ev[ev["action"].isin(
        ["TRANSITION", "CONSIDER", "SELECT", "PLACE_GTT", "FILL_BUY", "FILL_SELL", "TRAIL", "REJECT"]
    )]
    for _, r in ev.iterrows():
        msg = str(r["message"]).replace("\u2192", "->")[:100]
        print(f"  {r['date']}  {r['category']:8}  {r['action']:10}  {msg}")


if __name__ == "__main__":
    main()
