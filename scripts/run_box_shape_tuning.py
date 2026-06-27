#!/usr/bin/env python3
"""Sequential box-shape tuning on top of the best 2Y backtest config.

Tunes only darvas_reversal_days, min_box_duration_days, min_box_height_pct.
Each run emails results; later experiments adapt toward the best path so far.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester
from src.config import apply_darvas_algo_overrides, load_config_relaxed
from src.notify.backtest_email import resolve_email_settings, send_backtest_results_email

LOG_PATH = ROOT / "src" / "agentic-loop" / "box-shape-tuning-log.jsonl"
RESULTS_PATH = ROOT / "src" / "agentic-loop" / "box-shape-tuning-results.md"
BASE_CONFIG = ROOT / "config.yaml"
BASELINE_CONFIG = ROOT / "configs" / "baseline-next-best.yaml"
START = date(2024, 6, 1)
END = date(2026, 6, 19)
def _email_to() -> tuple[str, ...]:
    import os

    raw = os.environ.get("SWINGER_EMAIL_TO", "").strip()
    if not raw:
        return ()
    return tuple(addr.strip() for addr in raw.split(",") if addr.strip())

# Best 2Y config from optimization iter 9 (zoom_sma80_reset_loose_4.0).
BASE_OVERRIDES: dict[str, Any] = {
    "breakout_reset_above_top_pct": 4.0,
    "adaptive_sma_period": 80,
}

TUNING_KEYS = ("darvas_reversal_days", "min_box_duration_days", "min_box_height_pct")


@dataclass
class Experiment:
    name: str
    hypothesis: str
    overrides: dict[str, Any] = field(default_factory=dict)


def _score_key(record: dict) -> tuple[float, float]:
    if not record.get("feasible"):
        return (-1.0, 0.0)
    return (float(record["cagr"]), -float(record["max_drawdown_pct"]))


def _merge_params(box: dict[str, Any]) -> dict[str, Any]:
    return {**BASE_OVERRIDES, **box}


def _box_from_record(record: dict) -> dict[str, Any]:
    params = record.get("params") or {}
    return {k: params[k] for k in TUNING_KEYS if k in params}


def run_experiment(exp: Experiment, iteration: int, *, send_email: bool = True) -> dict:
    cfg = load_config_relaxed(BASE_CONFIG)
    cfg = apply_darvas_algo_overrides(cfg, exp.overrides)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False

    t0 = time.monotonic()
    bt = Backtester(cfg, repo_root=ROOT)
    run_dir = bt.run(start=START, end=END)
    elapsed = time.monotonic() - t0

    summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
    cagr = float(summary["cagr"])
    max_dd = float(summary["max_drawdown_pct"])
    feasible = max_dd <= 10.0
    record = {
        "iteration": iteration,
        "name": exp.name,
        "hypothesis": exp.hypothesis,
        "base_config": str(BASE_CONFIG),
        "params": exp.overrides,
        "run_dir": str(run_dir),
        "cagr": cagr,
        "max_drawdown_pct": max_dd,
        "win_rate": summary.get("win_rate"),
        "total_closed_trades": summary.get("total_closed_trades"),
        "feasible": feasible,
        "score": cagr if feasible else -1.0,
        "elapsed_sec": round(elapsed, 1),
    }
    append_log(record)

    if send_email:
        try:
            settings = resolve_email_settings(to_addrs=_email_to())
            send_backtest_results_email(run_dir, settings, experiment=record)
            print(f"  Email sent to {', '.join(_email_to())}", flush=True)
        except Exception as exc:
            print(f"  Email failed for iteration {iteration}: {exc}", flush=True)

    print(
        f"[{iteration:02d}] {exp.name}: CAGR={cagr:.2%} DD={max_dd:.2f}% "
        f"trades={record['total_closed_trades']} ({elapsed:.0f}s)",
        flush=True,
    )
    return record


def append_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_log() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    return [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def best_record(records: list[dict]) -> dict | None:
    feasible = [r for r in records if r.get("feasible")]
    if not feasible:
        return None
    return max(feasible, key=_score_key)


def _sweep_values(center: int | float, candidates: list[int | float]) -> list[int | float]:
    ordered = [center] + [v for v in candidates if v != center]
    seen: set[int | float] = set()
    out: list[int | float] = []
    for v in ordered:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def build_experiment_plan() -> list[Experiment]:
    """20 sequential experiments with adaptive phases (executed in order)."""
    return []  # built dynamically in main()


def plan_adaptive_experiments() -> list[Experiment]:
    """Pre-build first experiment; remainder filled after each phase in main()."""
    return [
        Experiment(
            "box_baseline_confirm",
            "Confirm production optimal defaults before box-shape tuning",
            overrides=_merge_params(
                {
                    "darvas_reversal_days": 3,
                    "min_box_duration_days": 4,
                    "min_box_height_pct": 3.0,
                }
            ),
        ),
    ]


def phase_sweep_experiments(
    best: dict[str, Any],
    key: str,
    candidates: list[Any],
    prefix: str,
    hypothesis_tpl: str,
    *,
    tried: set[tuple[Any, ...]],
    budget: int,
) -> list[Experiment]:
    exps: list[Experiment] = []
    for value in candidates:
        if len(exps) >= budget:
            break
        trial = dict(best)
        trial[key] = value
        sig = tuple(trial[k] for k in TUNING_KEYS)
        if sig in tried:
            continue
        exps.append(
            Experiment(
                f"{prefix}_{key}_{value}",
                hypothesis_tpl.format(value=value, **trial),
                overrides=_merge_params(trial),
            )
        )
    return exps


def refine_experiments(
    best: dict[str, Any],
    *,
    tried: set[tuple[Any, ...]],
    budget: int,
) -> list[Experiment]:
    exps: list[Experiment] = []
    rev = int(best["darvas_reversal_days"])
    dur = int(best["min_box_duration_days"])
    height = float(best["min_box_height_pct"])

    tweaks: list[tuple[str, Any, str]] = [
        ("darvas_reversal_days", rev - 1, "Tighten reversal window by 1 day"),
        ("darvas_reversal_days", rev + 1, "Widen reversal window by 1 day"),
        ("min_box_duration_days", dur - 1, "Shorter min box duration by 1 day"),
        ("min_box_duration_days", dur + 1, "Longer min box duration by 1 day"),
        ("min_box_height_pct", round(height - 0.5, 1), "Lower min box height by 0.5%"),
        ("min_box_height_pct", round(height + 0.5, 1), "Raise min box height by 0.5%"),
    ]
    for key, value, hypo in tweaks:
        if len(exps) >= budget:
            break
        if key == "darvas_reversal_days" and not (2 <= int(value) <= 6):
            continue
        if key == "min_box_duration_days" and not (3 <= int(value) <= 10):
            continue
        if key == "min_box_height_pct" and not (1.5 <= float(value) <= 8.0):
            continue
        trial = dict(best)
        trial[key] = value
        sig = tuple(trial[k] for k in TUNING_KEYS)
        if sig in tried:
            continue
        exps.append(
            Experiment(
                f"refine_{key}_{value}",
                hypo,
                overrides=_merge_params(trial),
            )
        )
    return exps


def write_results_md(records: list[dict]) -> None:
    best = best_record(records)
    lines = [
        "# Box Shape Tuning Results",
        "",
        f"**Base config:** `{BASE_CONFIG.name}` (zoom_sma80_reset_loose_4.0)",
        f"**Window:** {START} to {END}",
        f"**Iterations:** {len(records)}",
        "",
    ]
    if best:
        lines.extend(
            [
                "## Best config",
                "",
                f"- **Name:** {best['name']}",
                f"- **CAGR:** {100 * best['cagr']:.2f}%",
                f"- **Max DD:** {best['max_drawdown_pct']:.2f}%",
                f"- **Win rate:** {100 * (best.get('win_rate') or 0):.1f}%",
                f"- **Trades:** {best.get('total_closed_trades')}",
                f"- **Run:** `{best['run_dir']}`",
                "",
                "### Parameters",
                "```yaml",
            ]
        )
        for key, value in sorted((best.get("params") or {}).items()):
            lines.append(f"{key}: {value}")
        lines.append("```")
        lines.append("")

    lines.extend(["## Experiment log", "", "| Iter | Name | CAGR | Max DD | Trades |", "|------|------|------|--------|--------|"])
    for r in records:
        lines.append(
            f"| {r['iteration']} | {r['name']} | {100 * r['cagr']:.2f}% | "
            f"{r['max_drawdown_pct']:.2f}% | {r.get('total_closed_trades', '')} |"
        )
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    records = load_log()
    iteration = max((r["iteration"] for r in records), default=0) + 1
    tried: set[tuple[Any, ...]] = {
        tuple((r.get("params") or {}).get(k) for k in TUNING_KEYS) for r in records
    }

    best_box: dict[str, Any] = {
        "darvas_reversal_days": 3,
        "min_box_duration_days": 4,
        "min_box_height_pct": 3.0,
    }
    top = best_record(records)
    if top:
        best_box.update(_box_from_record(top))

    target_total = 20

    def run_queue(queue: list[Experiment]) -> None:
        nonlocal iteration, best_box
        for exp in queue:
            if iteration > target_total:
                return
            sig = tuple(exp.overrides.get(k) for k in TUNING_KEYS)
            if sig in tried:
                continue
            record = run_experiment(exp, iteration)
            records.append(record)
            tried.add(sig)
            leader = best_record(records)
            if leader:
                best_box.update(_box_from_record(leader))
                print(
                    f"  >> leader: {leader['name']} CAGR={leader['cagr']:.2%} "
                    f"DD={leader['max_drawdown_pct']:.2f}%",
                    flush=True,
                )
            write_results_md(records)
            iteration += 1

    if iteration == 1:
        run_queue(plan_adaptive_experiments())

    if iteration <= target_total:
        run_queue(
            phase_sweep_experiments(
                best_box,
                "darvas_reversal_days",
                [2, 3, 4, 5, 6],
                "rev",
                "Sweep reversal days — {value}d (dur={min_box_duration_days}, height={min_box_height_pct})",
                tried=tried,
                budget=4,
            )
        )

    if iteration <= target_total:
        run_queue(
            phase_sweep_experiments(
                best_box,
                "min_box_duration_days",
                [3, 4, 5, 6, 7, 8],
                "dur",
                "Sweep min box duration — {value}d (rev={darvas_reversal_days}, height={min_box_height_pct})",
                tried=tried,
                budget=4,
            )
        )

    if iteration <= target_total:
        run_queue(
            phase_sweep_experiments(
                best_box,
                "min_box_height_pct",
                [2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
                "height",
                "Sweep min box height — {value}% (rev={darvas_reversal_days}, dur={min_box_duration_days})",
                tried=tried,
                budget=4,
            )
        )

    if iteration <= target_total:
        run_queue(refine_experiments(best_box, tried=tried, budget=6))

    if iteration <= target_total:
        run_queue(
            [
                Experiment(
                    "best_combo_confirm",
                    "Confirm best box-shape combo after adaptive search",
                    overrides=_merge_params(best_box),
                )
            ]
        )

    write_results_md(records)
    best = best_record(records)
    if best:
        print(
            f"\nDone. Best: {best['name']} CAGR={best['cagr']:.2%} DD={best['max_drawdown_pct']:.2f}%",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
