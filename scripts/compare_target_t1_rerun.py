#!/usr/bin/env python3
"""Re-run 2-year and VIX-analog windows; compare before/after T+1 target rule."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / "src" / "backtest" / "virtual_broker.py"
BROKER_SAVED = ROOT / "backtest_outputs" / "_virtual_broker_t1.py"
OUT = ROOT / "backtest_outputs" / "target_t1_comparison.json"

TWO_YEAR = (date(2024, 6, 1), date(2026, 5, 31))
VIX_WINDOWS = [
    {
        "label": "VIX analog #1",
        "vix_analog": "2019-06-06 -> 2020-06-08",
        "start": date(2020, 6, 6),
        "end": date(2021, 6, 8),
        "before_run": "run_20260627_095625",
    },
    {
        "label": "VIX analog #2",
        "vix_analog": "2023-04-06 -> 2024-04-04",
        "start": date(2024, 4, 6),
        "end": date(2025, 4, 4),
        "before_run": "run_20260627_100047",
    },
    {
        "label": "VIX analog #3",
        "vix_analog": "2021-05-07 -> 2022-05-02",
        "start": date(2022, 5, 7),
        "end": date(2023, 5, 2),
        "before_run": "run_20260627_100614",
    },
]


def _load_summary(run_dir: str | Path) -> dict:
    path = ROOT / "backtest_outputs" / str(run_dir) / "summary_report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _metrics(summary: dict) -> dict:
    return {
        "start_date": summary.get("start_date"),
        "end_date": summary.get("end_date"),
        "cagr_pct": round(100 * float(summary.get("cagr", 0)), 2),
        "max_dd_pct": float(summary.get("max_drawdown_pct", 0)),
        "trades": int(summary.get("total_closed_trades", 0)),
        "win_rate_pct": round(100 * float(summary.get("win_rate", 0)), 1),
        "final_equity_inr": round(float(summary.get("final_equity_inr", 0)), 0),
        "run_directory": summary.get("run_directory"),
    }


def _run_backtest(start: date, end: date) -> Path:
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_backtest.py"),
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--no-email",
            "--no-progress",
        ],
        cwd=ROOT,
        check=True,
    )
    runs = sorted((ROOT / "backtest_outputs").glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for run in runs:
        if (run / "summary_report.json").is_file():
            return run
    raise RuntimeError("No backtest output found")


def _restore_t1_broker() -> None:
    shutil.copy2(BROKER_SAVED, BROKER)


def _use_git_broker() -> None:
    old = subprocess.run(
        ["git", "show", "HEAD:src/backtest/virtual_broker.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    BROKER.write_text(old.stdout, encoding="utf-8")


def main() -> int:
    if not BROKER_SAVED.is_file():
        shutil.copy2(BROKER, BROKER_SAVED)

    results: list[dict] = []

    # --- Before (same-day target allowed) ---
    _use_git_broker()
    print("=== BEFORE: same-day target allowed ===", flush=True)
    two_yr_before = _run_backtest(*TWO_YEAR)
    results.append(
        {
            "scenario": "Last 2 years (Jun 2024 – May 2026)",
            "before": _metrics(_load_summary(two_yr_before)),
        }
    )
    for w in VIX_WINDOWS:
        run_dir = _run_backtest(w["start"], w["end"])
        entry = next(r for r in results if r.get("label") == w["label"]) if any(
            r.get("label") == w["label"] for r in results
        ) else None
        if entry is None:
            entry = {"label": w["label"], "vix_analog": w["vix_analog"]}
            results.append(entry)
        entry["before_rerun"] = _metrics(_load_summary(run_dir))
        entry["before_original"] = _metrics(_load_summary(w["before_run"]))

    # --- After (target T+1) ---
    _restore_t1_broker()
    print("=== AFTER: target active from T+1 ===", flush=True)
    two_yr_after = _run_backtest(*TWO_YEAR)
    for r in results:
        if "Last 2 years" in r.get("scenario", ""):
            r["after"] = _metrics(_load_summary(two_yr_after))
            break
    for w in VIX_WINDOWS:
        run_dir = _run_backtest(w["start"], w["end"])
        for r in results:
            if r.get("label") == w["label"]:
                r["after"] = _metrics(_load_summary(run_dir))
                break

    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}", flush=True)

    lines = [
        "# Target T+1 backtest comparison",
        "",
        "Rule change: stop same-day as fill; target only from next session.",
        "",
        "## Last 2 years (2024-06-01 → 2026-05-31)",
        "",
    ]
    two = results[0]
    b, a = two["before"], two["after"]
    lines.append(
        f"| Metric | Before | After | Delta |"
    )
    lines.append(f"|--------|--------|-------|-------|")
    for key, fmt in [
        ("cagr_pct", "{:.2f}%"),
        ("max_dd_pct", "{:.2f}%"),
        ("trades", "{:.0f}"),
        ("win_rate_pct", "{:.1f}%"),
        ("final_equity_inr", "₹{:,.0f}"),
    ]:
        bv, av = b[key], a[key]
        if key == "final_equity_inr":
            delta = av - bv
            lines.append(f"| {key} | {fmt.format(bv)} | {fmt.format(av)} | {fmt.format(delta)} |")
        elif key == "trades":
            lines.append(f"| {key} | {int(bv)} | {int(av)} | {int(av - bv):+d} |")
        else:
            lines.append(f"| {key} | {fmt.format(bv)} | {fmt.format(av)} | {av - bv:+.2f} |")

    lines.extend(["", "## VIX analog subsequent-year windows", ""])
    for r in results[1:]:
        lines.append(f"### {r['label']} — VIX {r['vix_analog']}")
        lines.append(f"Backtest: {r['before_rerun']['start_date']} → {r['before_rerun']['end_date']}")
        lines.append("")
        lines.append("| Metric | Before (original run) | Before (re-run) | After T+1 |")
        lines.append("|--------|----------------------|-----------------|-----------|")
        bo = r["before_original"]
        br = r["before_rerun"]
        af = r["after"]
        lines.append(
            f"| CAGR | {bo['cagr_pct']:.2f}% | {br['cagr_pct']:.2f}% | {af['cagr_pct']:.2f}% |"
        )
        lines.append(
            f"| Max DD | {bo['max_dd_pct']:.2f}% | {br['max_dd_pct']:.2f}% | {af['max_dd_pct']:.2f}% |"
        )
        lines.append(
            f"| Trades | {bo['trades']} | {br['trades']} | {af['trades']} |"
        )
        lines.append(
            f"| Win rate | {bo['win_rate_pct']:.1f}% | {br['win_rate_pct']:.1f}% | {af['win_rate_pct']:.1f}% |"
        )
        lines.append(
            f"| Final equity | ₹{bo['final_equity_inr']:,.0f} | ₹{br['final_equity_inr']:,.0f} | ₹{af['final_equity_inr']:,.0f} |"
        )
        lines.append("")

    report = ROOT / "backtest_outputs" / "target_t1_comparison.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
