#!/usr/bin/env python3
"""Find NIFTY 50 index-candle analog periods and backtest shifted windows."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.index_curve_match import IndexWindowMatch, find_index_analogs, load_index_daily_bars
from src.backtest.backtester import Backtester
from src.config import load_config


def _format_match_table(
    matches: list[IndexWindowMatch],
    reference: tuple[date, date],
    *,
    session_count: int,
) -> list[str]:
    ref_start, ref_end = reference
    lines = [
        "NIFTY 50 index-candle analog study",
        f"Reference window: {ref_start} -> {ref_end} ({session_count} trading sessions, shape-matched)",
        "",
        "Matching method: z-scored Pearson on close/returns/range/body + DTW on closes.",
        "Backtest windows: analog period shifted forward 18 calendar months.",
        "",
        "Top analog windows and backtest ranges:",
    ]
    for m in matches:
        lines.append(
            f"  #{m.rank}  analog {m.analog_start} -> {m.analog_end}  "
            f"score={m.score:.4f}  corr={m.corr_close:.3f}  dtw_sim={m.dtw_similarity:.3f}"
        )
        lines.append(f"       Backtest window: {m.backtest_start} -> {m.backtest_end}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--ref-start", required=True, help="Reference window start YYYY-MM-DD")
    parser.add_argument("--ref-end", required=True, help="Reference window end YYYY-MM-DD")
    parser.add_argument("--db", default=None, help="Override data_db_path")
    parser.add_argument("--search-start", default="2017-01-01")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true", help="Find analogs only; skip backtests")
    parser.add_argument("--output-dir", default=None, help="Directory for summary report text")
    args = parser.parse_args()

    ref_start = date.fromisoformat(args.ref_start)
    ref_end = date.fromisoformat(args.ref_end)

    cfg = load_config(ROOT / args.config)
    db_path = Path(args.db) if args.db else ROOT / cfg.backtest.data_db_path
    if not db_path.is_file():
        print(f"Missing data lake: {db_path}", file=sys.stderr)
        return 1

    print(f"Loading NIFTY 50 bars from {db_path}...", flush=True)
    bars = load_index_daily_bars(db_path, end=ref_end)
    if bars.empty:
        print("No NIFTY 50 bars in database.", file=sys.stderr)
        return 1
    print(f"  {len(bars)} sessions loaded (through {bars['date'].iloc[-1]})", flush=True)

    ref_mask = (bars["date"] >= ref_start) & (bars["date"] <= ref_end)
    ref_sessions = len(bars.loc[ref_mask])

    matches = find_index_analogs(
        bars,
        reference_start=ref_start,
        reference_end=ref_end,
        top_k=args.top_k,
        search_start=date.fromisoformat(args.search_start),
    )
    if not matches:
        print("No index analog windows found.", file=sys.stderr)
        return 1

    report_lines = _format_match_table(matches, (ref_start, ref_end), session_count=ref_sessions)
    for line in report_lines:
        print(line, flush=True)

    if args.dry_run:
        return 0

    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False
    cfg.backtest.timestamped_runs = True

    summary_rows: list[dict] = []
    for m in matches:
        label = f"index_analog{m.rank}_{m.backtest_start}_{m.backtest_end}"
        print(f"\nRunning backtest {label}...", flush=True)
        bt = Backtester(cfg, repo_root=ROOT)
        out_dir = bt.run(start=m.backtest_start, end=m.backtest_end)
        summary = json.loads((Path(out_dir) / "summary_report.json").read_text(encoding="utf-8"))
        summary_rows.append({"match": m, "summary": summary, "run_dir": str(out_dir)})

    report_lines.extend(["", f"Backtest results ({args.config}):"])
    for row in summary_rows:
        m: IndexWindowMatch = row["match"]
        s = row["summary"]
        report_lines.extend(
            [
                "",
                f"Analog #{m.rank}: index {m.analog_start} -> {m.analog_end} "
                f"(score {m.score:.4f}, corr {m.corr_close:.3f})",
                f"  Backtest {s.get('start_date')} -> {s.get('end_date')}",
                f"  CAGR: {100 * float(s.get('cagr', 0)):.2f}%  "
                f"Max DD: {s.get('max_drawdown_pct')}%  "
                f"Trades: {s.get('total_closed_trades')}  "
                f"Win rate: {100 * float(s.get('win_rate', 0)):.1f}%",
                f"  Output: {row['run_dir']}",
            ]
        )

    plain_report = "\n".join(report_lines)
    out_dir = Path(args.output_dir) if args.output_dir else ROOT / "backtest_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_report = out_dir / f"index_analog_study_{ref_start}_{ref_end}.txt"
    out_report.write_text(plain_report, encoding="utf-8")
    print(f"\nReport written to {out_report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
