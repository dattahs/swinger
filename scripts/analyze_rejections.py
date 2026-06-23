#!/usr/bin/env python3
"""Summarize gate rejections from action_debug.csv (debug-log backtest runs)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_gate_rejects(run_dir: Path) -> pd.DataFrame:
    log_path = run_dir / "action_debug.csv"
    if not log_path.exists():
        raise FileNotFoundError(f"No action_debug.csv in {run_dir} (run backtest with --debug-log)")
    df = pd.read_csv(log_path)
    mask = (df["category"] == "GATE") & (df["action"] == "REJECT")
    rejects = df.loc[mask].copy()
    if rejects.empty:
        return rejects

    def _reason(row: pd.Series) -> str:
        if not isinstance(row.get("details_json"), str) or not row["details_json"]:
            return "UNKNOWN"
        try:
            details = json.loads(row["details_json"])
        except json.JSONDecodeError:
            return "UNKNOWN"
        return str(details.get("reason_code", "UNKNOWN"))

    rejects["reason_code"] = rejects.apply(_reason, axis=1)
    return rejects


def _print_section(title: str, sub: pd.DataFrame) -> None:
    print(f"\n=== {title} ===")
    print(f"  Events: {len(sub)}")
    if sub.empty:
        print("  (none)")
        return
    print(f"  Unique symbols: {sub['symbol'].nunique()}")
    print(f"  Unique symbol-days: {len(sub.drop_duplicates(['symbol', 'date']))}")
    by_sym = sub.groupby("symbol").size().sort_values(ascending=False)
    print("  Top symbols:")
    for sym, cnt in by_sym.head(15).items():
        print(f"    {sym}: {cnt}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze gate rejections from action_debug.csv")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=ROOT / "backtest_outputs" / "run_20260620_114100",
        help="Backtest output directory containing action_debug.csv",
    )
    parser.add_argument("--near-miss-pct", type=float, default=98.0, help="52WH pct threshold")
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()

    rejects = _load_gate_rejects(run_dir)
    print(f"Gate rejection analysis: {run_dir.name}")
    if rejects.empty:
        print("No GATE / REJECT rows in action_debug.csv")
        return

    counts = Counter(rejects["reason_code"])
    print("\nReason counts:")
    for code, n in counts.most_common():
        print(f"  {code}: {n}")

    vol_df = rejects[rejects["reason_code"] == "BREAKOUT_VOLUME_LOW"].copy()
    wh_df = rejects[rejects["reason_code"] == "NO_52WK_HIGH"].copy()
    trend_df = rejects[rejects["reason_code"] == "TREND_FAIL"]
    stale_df = rejects[rejects["reason_code"] == "STALE_BARS"]

    if not wh_df.empty:
        def _pct(row: pd.Series) -> float | None:
            try:
                d = json.loads(row["details_json"])
            except (json.JSONDecodeError, TypeError):
                return None
            return d.get("pct_of_52wh")

        wh_df["pct_of_52wh"] = wh_df.apply(_pct, axis=1)
        wh_near = wh_df[wh_df["pct_of_52wh"].fillna(0) >= args.near_miss_pct]
    else:
        wh_near = wh_df

    _print_section("Breakout volume (BREAKOUT_VOLUME_LOW)", vol_df)
    _print_section(f"52-week high blocks (NO_52WK_HIGH, all)", wh_df)
    _print_section(f"52WH near-misses (>= {args.near_miss_pct}% of prior high)", wh_near)
    _print_section("Trend filter (TREND_FAIL)", trend_df)
    _print_section("Stale bars (STALE_BARS)", stale_df)

    out = run_dir / "rejection_summary_from_logs.csv"
    rejects.to_csv(out, index=False)
    print(f"\nWrote {out.name} ({len(rejects)} rows)")


if __name__ == "__main__":
    main()
