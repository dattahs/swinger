#!/usr/bin/env python3
"""Sequential parameter optimization — 20+ backtest experiments with JSONL log."""

from __future__ import annotations

import copy
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
from src.config import load_config_relaxed
from src.notify.backtest_email import load_email_settings_from_env, send_backtest_results_email

LOG_PATH = ROOT / "src" / "agentic-loop" / "experiment-log.jsonl"
RESULTS_PATH = ROOT / "src" / "agentic-loop" / "optimization-results.md"
START = date(2024, 6, 1)
END = date(2026, 6, 19)


@dataclass
class Experiment:
    name: str
    hypothesis: str
    cadence: str = "daily"
    overrides: dict[str, Any] = field(default_factory=dict)


def _deep_set(cfg, path: str, value: Any):
    parts = path.split(".")
    obj = cfg
    for p in parts[:-1]:
        obj = getattr(obj, p)
    setattr(obj, parts[-1], value)


def apply_overrides(cfg, overrides: dict[str, Any]):
    cfg = cfg.model_copy(deep=True)
    for path, value in overrides.items():
        _deep_set(cfg, path, value)
    return cfg


def apply_cadence(cfg, cadence: str):
    cfg = cfg.model_copy(deep=True)
    cfg.universe_filters.adaptive_new_high_lookback.recalibration_cadence = cadence
    return cfg


def run_experiment(exp: Experiment, iteration: int, *, send_email: bool = True) -> dict:
    cfg = load_config_relaxed(ROOT / "config.yaml")
    cfg = apply_overrides(cfg, exp.overrides)
    cfg = apply_cadence(cfg, exp.cadence)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False

    t0 = time.monotonic()
    bt = Backtester(cfg, repo_root=ROOT)
    run_dir = bt.run(start=START, end=END)
    elapsed = time.monotonic() - t0

    summary = json.loads((run_dir / "summary_report.json").read_text())
    cagr = float(summary["cagr"])
    max_dd = float(summary["max_drawdown_pct"])
    feasible = max_dd <= 10.0
    record = {
        "iteration": iteration,
        "name": exp.name,
        "hypothesis": exp.hypothesis,
        "cadence": exp.cadence,
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
            settings = load_email_settings_from_env()
            send_backtest_results_email(run_dir, settings, experiment=record)
            print(f"  Email sent for iteration {iteration}", flush=True)
        except Exception as exc:
            print(f"  Email failed for iteration {iteration}: {exc}", flush=True)

    print(
        f"[{iteration:02d}] {exp.name}: CAGR={cagr:.2%} DD={max_dd:.2f}% "
        f"feasible={feasible} ({elapsed:.0f}s)",
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
    return [json.loads(line) for line in LOG_PATH.read_text().splitlines() if line.strip()]


def best_feasible(records: list[dict]) -> dict | None:
    feasible = [r for r in records if r.get("feasible")]
    if not feasible:
        return None
    return max(feasible, key=lambda r: r["score"])


def phase1_experiments() -> list[Experiment]:
    return [
        Experiment("baseline", "Current config.yaml defaults"),
        Experiment(
            "fixed_13w",
            "Shorter fixed lookback — more setups",
            overrides={
                "universe_filters.adaptive_new_high_lookback.enabled": False,
                "universe_filters.new_high_lookback_weeks": 13,
            },
        ),
        Experiment(
            "fixed_39w",
            "Longer fixed lookback — stricter gate",
            overrides={
                "universe_filters.adaptive_new_high_lookback.enabled": False,
                "universe_filters.new_high_lookback_weeks": 39,
            },
        ),
        Experiment(
            "reset_tight_0.5",
            "Tighter stale-breakout reset — faster box recycle",
            overrides={"darvas_box.breakout_reset_above_top_pct": 0.5},
        ),
        Experiment(
            "reset_loose_4.0",
            "Looser stale-breakout reset — keep boxes longer",
            overrides={"darvas_box.breakout_reset_above_top_pct": 4.0},
        ),
        Experiment(
            "narrow_band",
            "Narrower adaptive min/max band",
            overrides={
                "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 12,
                "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 30,
            },
        ),
        Experiment(
            "wide_band",
            "Wider adaptive min/max band",
            overrides={
                "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 6,
                "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 45,
            },
        ),
    ]


def phase2_experiments(best: dict) -> list[Experiment]:
    base = copy.deepcopy(best.get("params") or {})
    name = best.get("name", "baseline")
    exps = [
        Experiment(
            f"zoom_sma30_{name}",
            f"SMA 30 on best quadrant ({name})",
            overrides={**base, "universe_filters.adaptive_new_high_lookback.sma_period": 30},
        ),
        Experiment(
            f"zoom_sma80_{name}",
            f"SMA 80 on best quadrant ({name})",
            overrides={**base, "universe_filters.adaptive_new_high_lookback.sma_period": 80},
        ),
        Experiment(
            f"zoom_pct_5_85_{name}",
            f"Tighter spread percentiles on best ({name})",
            overrides={
                **base,
                "universe_filters.adaptive_new_high_lookback.low_percentile": 5,
                "universe_filters.adaptive_new_high_lookback.high_percentile": 85,
            },
        ),
        Experiment(
            f"zoom_pct_20_95_{name}",
            f"Wider spread percentiles on best ({name})",
            overrides={
                **base,
                "universe_filters.adaptive_new_high_lookback.low_percentile": 20,
                "universe_filters.adaptive_new_high_lookback.high_percentile": 95,
            },
        ),
        Experiment(
            f"zoom_reset_1.0_{name}",
            f"Tighter breakout reset on best ({name})",
            overrides={**base, "darvas_box.breakout_reset_above_top_pct": 1.0},
        ),
    ]
    return exps


def phase3_cadence_experiments(best: dict) -> list[Experiment]:
    base = copy.deepcopy(best.get("params") or {})
    name = best.get("name", "baseline")
    return [
        Experiment(
            f"cadence_weekly_{name}",
            f"Weekly lookback freeze on best ({name})",
            cadence="weekly",
            overrides=base,
        ),
        Experiment(
            f"cadence_monthly_{name}",
            f"Monthly lookback freeze on best ({name})",
            cadence="monthly",
            overrides=base,
        ),
        Experiment(
            f"cadence_sma_cross_{name}",
            f"Event: Nifty SMA cross on best ({name})",
            cadence="event_nifty_sma_cross",
            overrides=base,
        ),
        Experiment(
            f"cadence_spread_jump_{name}",
            f"Event: spread jump 2% on best ({name})",
            cadence="event_spread_jump",
            overrides={
                **base,
                "universe_filters.adaptive_new_high_lookback.spread_jump_threshold_pct": 2.0,
            },
        ),
        Experiment(
            f"cadence_static_{name}",
            f"Static lookback at start on best ({name})",
            cadence="static",
            overrides=base,
        ),
    ]


def phase4_confirm_experiments(top2: list[dict]) -> list[Experiment]:
    exps: list[Experiment] = []
    for row in top2:
        exps.append(
            Experiment(
                f"confirm_{row['name']}",
                f"Re-run top config for reproducibility ({row['name']})",
                cadence=row.get("cadence", "daily"),
                overrides=copy.deepcopy(row.get("params") or {}),
            )
        )
    best = top2[0] if top2 else None
    if best:
        base = copy.deepcopy(best.get("params") or {})
        exps.append(
            Experiment(
                "aggressive_wide",
                "Aggressive: wide band + loose reset on best base",
                cadence=best.get("cadence", "daily"),
                overrides={
                    **base,
                    "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 6,
                    "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 45,
                    "darvas_box.breakout_reset_above_top_pct": 4.0,
                },
            )
        )
        exps.append(
            Experiment(
                "conservative_narrow",
                "Conservative: narrow band + tight reset on best base",
                cadence=best.get("cadence", "daily"),
                overrides={
                    **base,
                    "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 12,
                    "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 30,
                    "darvas_box.breakout_reset_above_top_pct": 0.5,
                },
            )
        )
    return exps


def build_all_experiments() -> list[Experiment]:
    """Pre-plan 20 experiments (phases 2-4 use placeholder best from phase 1 design)."""
    exps = phase1_experiments()
    # Phase 2-4 will be generated dynamically after phase 1 runs
    return exps


def write_results(records: list[dict]) -> None:
    feasible = sorted(
        [r for r in records if r.get("feasible")],
        key=lambda r: r["score"],
        reverse=True,
    )
    best = feasible[0] if feasible else None
    lines = [
        "# Optimization Results",
        "",
        f"**Window:** {START} to {END}",
        f"**Iterations:** {len(records)}",
        "",
        "## Optimal config",
        "",
    ]
    if best:
        lines += [
            f"- **Name:** {best['name']}",
            f"- **CAGR:** {best['cagr']:.2%}",
            f"- **Max DD:** {best['max_drawdown_pct']:.2f}%",
            f"- **Win rate:** {best.get('win_rate', 0):.1%}",
            f"- **Trades:** {best.get('total_closed_trades')}",
            f"- **Cadence:** {best.get('cadence', 'daily')}",
            f"- **Run:** `{best['run_dir']}`",
            "",
            "### Parameter overrides",
            "```yaml",
        ]
        for k, v in sorted((best.get("params") or {}).items()):
            lines.append(f"{k}: {v}")
        if best.get("cadence") and best.get("cadence") != "daily":
            lines.append(
                "universe_filters.adaptive_new_high_lookback.recalibration_cadence: "
                f"{best['cadence']}"
            )
        lines += ["```", ""]
    else:
        lines.append("_No feasible run found (all exceeded 10% max DD)._")
        lines.append("")

    lines += ["## Top 3 feasible configs", ""]
    lines.append("| Rank | Name | CAGR | Max DD | Cadence | Trades |")
    lines.append("|------|------|------|--------|---------|--------|")
    for i, r in enumerate(feasible[:3], 1):
        lines.append(
            f"| {i} | {r['name']} | {r['cagr']:.2%} | {r['max_drawdown_pct']:.2f}% | "
            f"{r.get('cadence', 'daily')} | {r.get('total_closed_trades')} |"
        )

    lines += ["", "## Algorithm suggestions", ""]
    if best and records:
        baseline = next((r for r in records if r["name"] == "baseline"), None)
        if baseline:
            delta = best["cagr"] - baseline["cagr"]
            lines.append(
                f"- Best config **{best['name']}** beat baseline CAGR by **{delta:+.2%}** "
                f"({baseline['cagr']:.2%} → {best['cagr']:.2%}) with DD "
                f"{best['max_drawdown_pct']:.2f}% vs {baseline['max_drawdown_pct']:.2f}%."
            )
        fixed_runs = [r for r in records if "fixed_" in r["name"]]
        adaptive_runs = [r for r in records if r["name"] in ("baseline",) or "band" in r["name"]]
        if fixed_runs and adaptive_runs:
            best_fixed = max(fixed_runs, key=lambda r: r["score"] if r["feasible"] else -1)
            best_adaptive = max(
                [r for r in adaptive_runs if r.get("feasible")],
                key=lambda r: r["score"],
                default=None,
            )
            if best_adaptive:
                winner = "adaptive" if best_adaptive["score"] > best_fixed["score"] else "fixed"
                lines.append(
                    f"- On 2Y data, **{winner}** lookback outperformed "
                    f"(best adaptive/baseline: {best_adaptive['cagr']:.2%} vs "
                    f"best fixed: {best_fixed['cagr']:.2%})."
                )
        cadence_runs = [r for r in records if r["name"].startswith("cadence_")]
        if cadence_runs:
            best_cad = max(cadence_runs, key=lambda r: r["score"] if r.get("feasible") else -1)
            lines.append(
                f"- Best cadence mode: **{best_cad.get('cadence')}** "
                f"({best_cad['name']}, CAGR {best_cad['cagr']:.2%})."
            )

    lines += ["", "## Experiment log", ""]
    lines.append("| Iter | Name | CAGR | Max DD | Feasible | Cadence |")
    lines.append("|------|------|------|--------|----------|---------|")
    for r in records:
        lines.append(
            f"| {r['iteration']} | {r['name']} | {r['cagr']:.2%} | "
            f"{r['max_drawdown_pct']:.2f}% | {r['feasible']} | {r.get('cadence', 'daily')} |"
        )

    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}", flush=True)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-email", action="store_true", help="Skip email after each run")
    args = parser.parse_args()
    send_email = not args.no_email

    existing = load_log()
    start_iter = len(existing) + 1
    records = list(existing)

    if start_iter == 1:
        LOG_PATH.write_text("", encoding="utf-8")
        planned = phase1_experiments()
        for i, exp in enumerate(planned, start=1):
            records.append(run_experiment(exp, i, send_email=send_email))

        best = best_feasible(records) or records[0]
        iter_n = len(records) + 1
        for exp in phase2_experiments(best):
            records.append(run_experiment(exp, iter_n, send_email=send_email))
            iter_n += 1

        best = best_feasible(records) or records[0]
        for exp in phase3_cadence_experiments(best):
            records.append(run_experiment(exp, iter_n, send_email=send_email))
            iter_n += 1

        feasible_sorted = sorted(
            [r for r in records if r.get("feasible")],
            key=lambda r: r["score"],
            reverse=True,
        )
        top2 = feasible_sorted[:2] if len(feasible_sorted) >= 2 else feasible_sorted
        for exp in phase4_confirm_experiments(top2):
            records.append(run_experiment(exp, iter_n, send_email=send_email))
            iter_n += 1

    elif start_iter < 20:
        print(f"Resuming from iteration {start_iter} (partial log found)", flush=True)
        # Continue with remaining phases based on what's done
        names_done = {r["name"] for r in records}
        best = best_feasible(records) or records[-1]
        iter_n = start_iter
        for exp in phase2_experiments(best):
            if exp.name not in names_done:
                records.append(run_experiment(exp, iter_n, send_email=send_email))
                iter_n += 1
        best = best_feasible(records) or records[-1]
        for exp in phase3_cadence_experiments(best):
            if exp.name not in names_done:
                records.append(run_experiment(exp, iter_n, send_email=send_email))
                iter_n += 1
        feasible_sorted = sorted(
            [r for r in records if r.get("feasible")],
            key=lambda r: r["score"],
            reverse=True,
        )
        top2 = feasible_sorted[:2] if len(feasible_sorted) >= 2 else feasible_sorted
        for exp in phase4_confirm_experiments(top2):
            if exp.name not in names_done:
                records.append(run_experiment(exp, iter_n, send_email=send_email))
                iter_n += 1
    else:
        print(f"Already have {len(records)} iterations — writing results only", flush=True)

    write_results(records)
    best = best_feasible(records)
    if best:
        print(
            f"\nBEST: {best['name']} CAGR={best['cagr']:.2%} DD={best['max_drawdown_pct']:.2f}%",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
