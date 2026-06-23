#!/usr/bin/env python3
"""Trace symbol box state, breakout history, and live vs backtest decisions."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.engine.darvas import _compute_hybrid_box, compute_atr
from src.repository.sqlite import SqliteDataLake

SYMS = ["BAJAJ-AUTO", "BRITANNIA"]


def trace_symbol(sym: str, dl: SqliteDataLake, cfg) -> None:
    print("=" * 72)
    print(f"  {sym}")
    print("=" * 72)

    bars_recent = dl.get_daily_bars(sym, date(2026, 6, 19), 30)
    if not bars_recent.empty:
        last = bars_recent.iloc[-1]
        print(f"19-Jun close: {last['close']:.2f}  high: {last['high']:.2f}  low: {last['low']:.2f}")

    # Key Apr-May bars
    bars = dl.get_daily_bars(sym, date(2026, 6, 19), 400)
    print("\nApr-May 2026 (selected dates):")
    for _, r in bars.iterrows():
        d = r["date"]
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        if date(2026, 4, 1) <= d <= date(2026, 5, 15):
            print(f"  {d}  C={r['close']:8.2f}  H={r['high']:8.2f}  L={r['low']:8.2f}")

    # Live registry
    live = __import__("sqlite3").connect(ROOT / "data/live/sessions/run_20260622.db")
    live.row_factory = __import__("sqlite3").Row
    row = live.execute(
        "SELECT * FROM active_state_registry WHERE symbol=?", (sym,)
    ).fetchone()
    if row:
        r = dict(row)
        print(f"\nState registry (post live run):")
        print(f"  state={r['box_state']}  top={r['box_top']}  bottom={r['box_bottom']}")
        print(f"  start={r['box_start_date']}  rev_hi={r['reversal_high']}  close={r['last_close']}")
        top, bot = r["box_top"], r["box_bottom"]
        close = r["last_close"]
        if top and bot and close:
            if close > top:
                print(f"  >> price {close:.0f} is ABOVE box top {top:.0f} by {(close/top-1)*100:.1f}%")
            elif close < bot:
                print(f"  >> price {close:.0f} is BELOW box bottom {bot:.0f} by {(1-close/bot)*100:.1f}%")
            else:
                print(f"  >> price inside box")

    dec = live.execute(
        "SELECT * FROM decision_log WHERE symbol=? AND date='2026-06-22'", (sym,)
    ).fetchone()
    if dec:
        d = dict(dec)
        print(f"\n22-Jun live decision: {d['action_type']} rank={d['rank']} skip={d['skip_reason']}")
        if d.get("trigger_price"):
            print(
                f"  trigger={d['trigger_price']} stop={d['stop_loss_price']} "
                f"target={d['target_price']} box=[{d['box_bottom']}, {d['box_top']}]"
            )

    # Backtest warmup path
    log = ROOT / "backtest_outputs/run_20260621_225616/decision_log.csv"
    if log.exists():
        import pandas as pd

        df = pd.read_csv(log, parse_dates=["date"])
        sub = df[df["symbol"] == sym].copy()
        bo = sub[sub["action_type"] == "PLACE_BUY_GTT"]
        if not bo.empty:
            print("\nBacktest PLACE_BUY_GTT events:")
            for _, r in bo.iterrows():
                print(
                    f"  {r['date'].date()}  box=[{r['box_bottom']}, {r['box_top']}]  "
                    f"trigger={r['trigger_price']}"
                )
        br = sub[sub["box_state"] == "BREAKOUT"]
        if not br.empty:
            first = br.iloc[0]
            print(f"\nFirst BREAKOUT in warmup: {first['date'].date()}  box=[{first['box_bottom']}, {first['box_top']}]")
        # Apr 8 and Apr-May transitions
        print("\nApr 2026 state snapshots:")
        apr = sub[(sub["date"] >= "2026-04-01") & (sub["date"] <= "2026-05-15")]
        prev = None
        for _, r in apr.iterrows():
            key = (r["box_state"], r["box_top"], r["box_bottom"], r["action_type"], r.get("skip_reason"))
            if key != prev:
                print(
                    f"  {r['date'].date()}  {r['box_state']:10}  "
                    f"box=[{r['box_bottom']}, {r['box_top']}]  "
                    f"act={r['action_type']}  skip={r.get('skip_reason') or ''}"
                )
                prev = key
        jun19 = sub[sub["date"] == "2026-06-19"]
        if not jun19.empty:
            r = jun19.iloc[0]
            print(
                f"\n19-Jun backtest: {r['box_state']} box=[{r['box_bottom']}, {r['box_top']}] "
                f"act={r['action_type']} skip={r.get('skip_reason') or ''}"
            )

    live.close()


def main() -> None:
    cfg = load_config_relaxed(ROOT / "config.yaml")
    dl = SqliteDataLake(ROOT / "data/processed/swinger_data.db")
    for sym in SYMS:
        trace_symbol(sym, dl, cfg)
        print()


if __name__ == "__main__":
    main()
