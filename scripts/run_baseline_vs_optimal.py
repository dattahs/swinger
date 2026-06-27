#!/usr/bin/env python3
"""Compare baseline vs optimal Darvas config over a custom date window."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester
from src.config import (
    BASELINE_NEXT_BEST_CONFIG_PATH,
    DEFAULT_CONFIG_PATH,
    apply_darvas_algo_overrides,
    load_config,
)

START = date(2021, 2, 1)
END = date(2022, 7, 31)
OUTPUT = ROOT / "src" / "agentic-loop" / "baseline-vs-optimal-2021-2022.md"


def _run(label: str, config_path: Path, overrides: dict[str, Any], hypothesis: str) -> dict:
    cfg = load_config(config_path)
    if overrides:
        cfg = apply_darvas_algo_overrides(cfg, overrides)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = True

    bt = Backtester(cfg, repo_root=ROOT)
    run_dir = bt.run(
        start=START,
        end=END,
        email_experiment={
            "name": label,
            "hypothesis": hypothesis,
            "params": overrides or {"config": str(config_path)},
            "cadence": "daily",
        },
    )
    summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
    return {
        "label": label,
        "overrides": overrides,
        "run_dir": str(run_dir),
        "cagr": float(summary["cagr"]),
        "max_drawdown_pct": float(summary["max_drawdown_pct"]),
        "win_rate": float(summary.get("win_rate") or 0),
        "total_closed_trades": int(summary.get("total_closed_trades") or 0),
        "final_equity_inr": float(summary.get("final_equity_inr") or 0),
    }


def main() -> int:
    baseline = _run(
        "baseline_dur5",
        ROOT / BASELINE_NEXT_BEST_CONFIG_PATH,
        {},
        "Next-best baseline (zoom_sma80_reset_loose_4.0, min_box_duration_days=5)",
    )
    optimal = _run(
        "optimal_dur4",
        ROOT / DEFAULT_CONFIG_PATH,
        {},
        "Production optimal default (min_box_duration_days=4)",
    )

    cagr_delta = optimal["cagr"] - baseline["cagr"]
    dd_delta = optimal["max_drawdown_pct"] - baseline["max_drawdown_pct"]
    lines = [
        "# Baseline vs Optimal — Feb 2021 to Jul 2022",
        "",
        f"**Window:** {START} to {END}",
        "",
        "| Config | min_box_duration | CAGR | Max DD | Win rate | Trades | Final equity |",
        "|--------|------------------|------|--------|----------|--------|--------------|",
        (
            f"| Baseline | 5 | {100 * baseline['cagr']:.2f}% | {baseline['max_drawdown_pct']:.2f}% | "
            f"{100 * baseline['win_rate']:.1f}% | {baseline['total_closed_trades']} | "
            f"₹{baseline['final_equity_inr']:,.0f} |"
        ),
        (
            f"| Optimal | 4 | {100 * optimal['cagr']:.2f}% | {optimal['max_drawdown_pct']:.2f}% | "
            f"{100 * optimal['win_rate']:.1f}% | {optimal['total_closed_trades']} | "
            f"₹{optimal['final_equity_inr']:,.0f} |"
        ),
        "",
        f"**Delta (optimal − baseline):** CAGR {100 * cagr_delta:+.2f}pp, Max DD {dd_delta:+.2f}pp",
        "",
        f"- Baseline run: `{baseline['run_dir']}`",
        f"- Optimal run: `{optimal['run_dir']}`",
    ]
    OUTPUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    safe_lines = [line.replace("₹", "INR ") for line in lines]
    print("\n".join(safe_lines), flush=True)
    print(f"\nComparison written to {OUTPUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
