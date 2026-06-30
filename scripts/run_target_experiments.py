#!/usr/bin/env python3
"""Target-setting experiments — box-height multiplier × dynamic ATR band target.

Phase 1: 20 param combos on last 2 years (anchor: 1.9× box height, 80% ATR band).
Phase 2: Promising 2Y runs (~20% CAGR, ~3% DD) repeated on 2021-02 → 2023-02.
Phase 3: Shortlisted configs (≤2% CAGR gap, ≤1% DD gap) on 3 India-VIX analog
         windows of ≥18 months.

Each backtest emails results when SWINGER_EMAIL_TO is set in .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.vix_curve_match import VixWindowMatch, find_vix_analogs
from src.backtest.backtester import Backtester
from src.config import apply_darvas_algo_overrides, load_config_relaxed
from src.data.vix_data import load_or_download_vix
from src.broker.env import load_dotenv
from src.notify.backtest_email import resolve_email_settings, send_backtest_results_email

LOG_PATH = ROOT / "src" / "agentic-loop" / "target-experiments-log.jsonl"
RESULTS_PATH = ROOT / "src" / "agentic-loop" / "target-experiments-results.md"
DEFAULT_CONFIG = ROOT / "configs" / "baseline-next-best.yaml"
DEFAULT_EMAIL_TO: tuple[str, ...] = ()

TWO_YEAR = (date(2024, 6, 1), date(2026, 6, 19))
VALIDATION = (date(2021, 2, 1), date(2023, 2, 28))
REF_VIX = TWO_YEAR

CAGR_TARGET = 0.20
DD_TARGET = 3.0
CAGR_SCREEN_TOL = 0.02
DD_SCREEN_TOL = 0.01
CAGR_PASS_LO = CAGR_TARGET - 0.02
CAGR_PASS_HI = CAGR_TARGET + 0.02
DD_PASS_LO = DD_TARGET - 1.0
DD_PASS_HI = DD_TARGET + 1.0

VIX_MIN_SESSIONS = 378  # ~18 months of trading sessions
VIX_ANALOG_COUNT = 3


def _prime_email_env() -> None:
    """Load SMTP settings from laptop .env or VPS shared/.env."""
    candidates = [
        Path(os.environ["SWINGER_ENV_FILE"]) if os.environ.get("SWINGER_ENV_FILE") else None,
        ROOT / ".env",
        Path("/opt/swinger/shared/.env"),
    ]
    for path in candidates:
        if path and path.is_file():
            load_dotenv(path, override=True)
            return


def _email_to() -> tuple[str, ...]:
    raw = os.environ.get("SWINGER_EMAIL_TO", "").strip()
    if not raw:
        return DEFAULT_EMAIL_TO
    return tuple(addr.strip() for addr in raw.split(",") if addr.strip())


def build_param_grid() -> list[tuple[float, float]]:
    """20 (multiplier, atr_band_pct) pairs centered on anchor (1.9, 80)."""
    anchor = (1.9, 80.0)
    mults = [1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2, 2.3, 2.4]
    bands = [60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0]
    grid: list[tuple[float, float]] = [anchor]
    for m in mults:
        for b in bands:
            pair = (m, b)
            if pair not in grid:
                grid.append(pair)
            if len(grid) >= 20:
                return grid
    return grid


@dataclass
class Experiment:
    name: str
    hypothesis: str
    start: date
    end: date
    overrides: dict[str, Any] = field(default_factory=dict)
    phase: str = ""
    param_key: str = ""


def _param_key(mult: float, band_pct: float) -> str:
    return f"mult{mult:.1f}_atr{band_pct:.0f}"


def _overrides(mult: float, band_pct: float) -> dict[str, Any]:
    return {
        "target_box_height_multiplier": mult,
        "dynamic_atr_target_enabled": True,
        "dynamic_atr_target_band_pct": band_pct,
    }


def append_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def passes_2y_screen(cagr: float, max_dd: float) -> bool:
    return CAGR_PASS_LO <= cagr <= CAGR_PASS_HI and DD_PASS_LO <= max_dd <= DD_PASS_HI


def passes_consistency(two_y: dict, validation: dict) -> bool:
    cagr_gap = abs(float(two_y["cagr"]) - float(validation["cagr"]))
    dd_gap = abs(float(two_y["max_drawdown_pct"]) - float(validation["max_drawdown_pct"]))
    return cagr_gap <= CAGR_SCREEN_TOL and dd_gap <= DD_SCREEN_TOL


def find_vix_analog_windows() -> list[VixWindowMatch]:
    vix = load_or_download_vix(ROOT, date(2017, 1, 1), REF_VIX[1])
    return find_vix_analogs(
        vix,
        reference_start=REF_VIX[0],
        reference_end=REF_VIX[1],
        window_sessions=VIX_MIN_SESSIONS,
        top_k=VIX_ANALOG_COUNT,
        search_start=date(2017, 1, 1),
    )


class Runner:
    def __init__(self, config_path: Path, *, send_email: bool = True) -> None:
        self.config_path = config_path
        self.send_email = send_email
        self.iteration = 0
        self.records: list[dict] = []

    def run_experiment(self, exp: Experiment) -> dict:
        self.iteration += 1
        cfg = load_config_relaxed(self.config_path)
        cfg = apply_darvas_algo_overrides(cfg, exp.overrides)
        cfg.backtest.progress_log.enabled = False
        cfg.backtest.debug_log.enabled = False
        cfg.backtest.send_email_on_complete = False

        t0 = time.monotonic()
        bt = Backtester(cfg, repo_root=ROOT)
        run_dir = bt.run(start=exp.start, end=exp.end)
        elapsed = time.monotonic() - t0

        summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
        cagr = float(summary["cagr"])
        max_dd = float(summary["max_drawdown_pct"])
        record = {
            "iteration": self.iteration,
            "phase": exp.phase,
            "param_key": exp.param_key,
            "name": exp.name,
            "hypothesis": exp.hypothesis,
            "start_date": exp.start.isoformat(),
            "end_date": exp.end.isoformat(),
            "params": exp.overrides,
            "run_dir": str(run_dir),
            "cagr": cagr,
            "max_drawdown_pct": max_dd,
            "win_rate": summary.get("win_rate"),
            "total_closed_trades": summary.get("total_closed_trades"),
            "elapsed_sec": round(elapsed, 1),
        }
        append_log(record)
        self.records.append(record)

        if self.send_email:
            try:
                _prime_email_env()
                settings = resolve_email_settings(to_addrs=_email_to())
                send_backtest_results_email(run_dir, settings, experiment=record)
                print(f"  Email sent to {', '.join(_email_to())}", flush=True)
            except Exception as exc:
                print(f"  Email failed for iteration {self.iteration}: {exc}", flush=True)

        print(
            f"[{self.iteration:02d}] {exp.phase} {exp.name}: "
            f"CAGR={cagr:.2%} DD={max_dd:.2f}% trades={record['total_closed_trades']} ({elapsed:.0f}s)",
            flush=True,
        )
        return record


def write_summary(
    records: list[dict],
    *,
    shortlisted: list[dict],
    vix_matches: list[VixWindowMatch] | None = None,
) -> None:
    lines = [
        "# Target setting experiments",
        "",
        f"Anchor: **1.9× box height + 80% ATR band** (dynamic ratchet enabled).",
        f"Screen: ~{CAGR_TARGET:.0%} CAGR / ~{DD_PASS_HI:.0f}% DD on 2Y; "
        f"consistency ≤{CAGR_SCREEN_TOL:.0%} CAGR / ≤{DD_SCREEN_TOL:.0f}% DD vs validation.",
        "",
        "| # | Phase | Name | Window | CAGR | Max DD | Trades | Win rate |",
        "|---|-------|------|--------|------|--------|--------|----------|",
    ]
    for r in records:
        lines.append(
            f"| {r['iteration']} | {r.get('phase', '')} | {r['name']} | "
            f"{r['start_date']} → {r['end_date']} | "
            f"{100 * r['cagr']:.2f}% | {r['max_drawdown_pct']:.2f}% | "
            f"{r['total_closed_trades']} | {100 * float(r.get('win_rate') or 0):.1f}% |"
        )

    if shortlisted:
        lines.extend(["", "## Shortlisted candidates", ""])
        for s in shortlisted:
            lines.append(
                f"- **{s['param_key']}**: 2Y CAGR {100 * s['two_y']['cagr']:.2f}% / "
                f"DD {s['two_y']['max_drawdown_pct']:.2f}% | "
                f"val CAGR {100 * s['validation']['cagr']:.2f}% / "
                f"DD {s['validation']['max_drawdown_pct']:.2f}%"
            )

    if vix_matches:
        lines.extend(["", "## VIX analog windows (≥18 months)", ""])
        for m in vix_matches:
            lines.append(
                f"- #{m.rank} VIX {m.analog_start} → {m.analog_end} "
                f"(score {m.score:.4f}, corr {m.corr_close:.3f})"
            )

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--phase", choices=("all", "1", "2", "3"), default="all")
    args = parser.parse_args()

    runner = Runner(args.config.resolve(), send_email=not args.no_email)
    grid = build_param_grid()
    two_y_by_key: dict[str, dict] = {}
    shortlisted: list[dict] = []

    print(f"Config: {args.config}", flush=True)
    print(f"Param grid ({len(grid)} combos), anchor {grid[0]}", flush=True)
    print(f"2Y window: {TWO_YEAR[0]} → {TWO_YEAR[1]}", flush=True)
    print(f"Validation: {VALIDATION[0]} → {VALIDATION[1]}", flush=True)

    if args.phase in ("all", "1"):
        print("\n=== Phase 1: 2-year screen (20 combos) ===", flush=True)
        for mult, band in grid:
            key = _param_key(mult, band)
            exp = Experiment(
                name=f"target_{key}_2y",
                hypothesis=(
                    f"Box target {mult:.1f}× height + dynamic ATR at {band:.0f}% band "
                    f"({TWO_YEAR[0]} → {TWO_YEAR[1]})"
                ),
                start=TWO_YEAR[0],
                end=TWO_YEAR[1],
                overrides=_overrides(mult, band),
                phase="2y_screen",
                param_key=key,
            )
            rec = runner.run_experiment(exp)
            if passes_2y_screen(rec["cagr"], rec["max_drawdown_pct"]):
                two_y_by_key[key] = rec
                print(f"  -> passes 2Y screen ({rec['cagr']:.2%} CAGR, {rec['max_drawdown_pct']:.2f}% DD)", flush=True)

    if args.phase in ("all", "2"):
        if not two_y_by_key and args.phase == "2":
            print("Phase 2 requires phase-1 results in memory; run --phase all", file=sys.stderr)
            return 1
        print(f"\n=== Phase 2: validation window ({len(two_y_by_key)} candidates) ===", flush=True)
        for key, two_y_rec in two_y_by_key.items():
            mult = two_y_rec["params"]["target_box_height_multiplier"]
            band = two_y_rec["params"]["dynamic_atr_target_band_pct"]
            exp = Experiment(
                name=f"target_{key}_val",
                hypothesis=(
                    f"Validation repeat {mult:.1f}× / {band:.0f}% ATR "
                    f"({VALIDATION[0]} → {VALIDATION[1]})"
                ),
                start=VALIDATION[0],
                end=VALIDATION[1],
                overrides=_overrides(mult, band),
                phase="validation",
                param_key=key,
            )
            val_rec = runner.run_experiment(exp)
            if passes_consistency(two_y_rec, val_rec):
                shortlisted.append({"param_key": key, "two_y": two_y_rec, "validation": val_rec})
                print(f"  -> SHORTLISTED {key}", flush=True)

    vix_matches: list[VixWindowMatch] | None = None
    if args.phase in ("all", "3"):
        if not shortlisted and args.phase == "3":
            print("Phase 3 requires shortlisted candidates; run --phase all", file=sys.stderr)
            return 1
        if shortlisted:
            print(f"\n=== Phase 3: VIX analog windows ({len(shortlisted)} shortlisted) ===", flush=True)
            vix_matches = find_vix_analog_windows()
            for m in vix_matches:
                print(
                    f"VIX analog #{m.rank}: {m.analog_start} → {m.analog_end} "
                    f"(score {m.score:.4f})",
                    flush=True,
                )
            for entry in shortlisted:
                key = entry["param_key"]
                mult = entry["two_y"]["params"]["target_box_height_multiplier"]
                band = entry["two_y"]["params"]["dynamic_atr_target_band_pct"]
                for m in vix_matches:
                    exp = Experiment(
                        name=f"target_{key}_vix{m.rank}",
                        hypothesis=(
                            f"Shortlist {key} on VIX analog #{m.rank} "
                            f"({m.analog_start} → {m.analog_end})"
                        ),
                        start=m.analog_start,
                        end=m.analog_end,
                        overrides=_overrides(mult, band),
                        phase=f"vix_analog_{m.rank}",
                        param_key=key,
                    )
                    runner.run_experiment(exp)

    write_summary(runner.records, shortlisted=shortlisted, vix_matches=vix_matches)
    print(f"\nDone. {len(runner.records)} runs.", flush=True)
    print(f"Shortlisted: {len(shortlisted)}", flush=True)
    print(f"Log: {LOG_PATH}", flush=True)
    print(f"Summary: {RESULTS_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
