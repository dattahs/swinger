#!/usr/bin/env python3
"""A/B backtests: sector regime gate ON vs OFF on blind-spot and primary 2Y windows."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.sector_regime_council import CouncilRequest, run_sector_regime_council
from src.backtest.backtester import Backtester
from src.config import AppConfig, load_config
from src.engine.sector_regime_gate import evaluate_gate_from_summary

BLIND_SPOT = (date(2018, 7, 6), date(2019, 12, 4))
PRIMARY_2Y = (date(2024, 6, 1), date(2026, 5, 31))

LOG_PATH = ROOT / "src" / "agentic-loop" / "sector-regime-gate-log.jsonl"
RESULTS_PATH = ROOT / "src" / "agentic-loop" / "sector-regime-gate-experiment.md"
REGIME_JSON = ROOT / "src" / "agentic-loop" / "sector-regime-gate-2y-regime.json"


@dataclass
class RunRow:
    window: str
    gate: str
    start: date
    end: date
    cagr: float
    max_drawdown_pct: float
    total_closed_trades: int
    win_rate: float
    elapsed_sec: float

    @property
    def label(self) -> str:
        return f"{self.window} / gate {self.gate}"


def append_log(row: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def trading_days(conn: sqlite3.Connection, start: date, end: date) -> list[date]:
    rows = conn.execute(
        """
        SELECT DISTINCT date FROM daily_bars
        WHERE symbol = 'NIFTY 50' AND date >= ? AND date <= ?
        ORDER BY date
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def month_end_samples(days: list[date]) -> list[date]:
    by_month: dict[tuple[int, int], date] = {}
    for d in days:
        by_month[(d.year, d.month)] = d
    return [by_month[k] for k in sorted(by_month)]


def run_backtest(
    cfg: AppConfig,
    *,
    start: date,
    end: date,
    gate_enabled: bool,
    window: str,
) -> RunRow:
    cfg = cfg.model_copy(deep=True)
    cfg.sector_regime_gate.enabled = gate_enabled
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False

    gate_label = "ON" if gate_enabled else "OFF"
    print(f"  {window} gate {gate_label}: {start} -> {end} ...", flush=True)
    t0 = time.monotonic()
    bt = Backtester(cfg, repo_root=ROOT)
    result = bt.run(start=start, end=end, persist_outputs=False)
    elapsed = time.monotonic() - t0
    summary = result.summary

    row = RunRow(
        window=window,
        gate=gate_label,
        start=start,
        end=end,
        cagr=float(summary["cagr"]),
        max_drawdown_pct=float(summary["max_drawdown_pct"]),
        total_closed_trades=int(summary["total_closed_trades"]),
        win_rate=float(summary.get("win_rate", 0)),
        elapsed_sec=round(elapsed, 1),
    )
    append_log(
        {
            "window": window,
            "gate": gate_label,
            "start": start.isoformat(),
            "end": end.isoformat(),
            **summary,
            "elapsed_sec": row.elapsed_sec,
        }
    )
    print(
        f"    CAGR={row.cagr:.2%} DD={row.max_drawdown_pct:.2f}% trades={row.total_closed_trades} "
        f"({elapsed:.0f}s)",
        flush=True,
    )
    return row


def analyze_2y_regime(
    db_path: Path,
    vix_path: Path,
    gate_cfg,
    start: date,
    end: date,
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path)
    days = trading_days(conn, start, end)
    conn.close()
    samples = month_end_samples(days)

    regime_counts: Counter[str] = Counter()
    dispersion_counts: Counter[str] = Counter()
    gate_blocked = 0
    exposures: list[float] = []
    monthly: list[dict[str, Any]] = []

    for d in samples:
        result = run_sector_regime_council(
            CouncilRequest(
                as_of=d,
                window_months=gate_cfg.council_window_months,
                db_path=db_path,
                vix_csv_path=vix_path,
                skip_breadth=gate_cfg.skip_breadth,
            )
        )
        cs = result["council_summary"]
        dominant = str(cs.get("dominant_regime", ""))
        dispersion = str(cs.get("regime_dispersion", ""))
        exposure = float(cs.get("recommended_overall_exposure", 0))
        blocked, reason = evaluate_gate_from_summary(cs, gate_cfg.model_copy(update={"enabled": True}))

        regime_counts[dominant] += 1
        dispersion_counts[dispersion] += 1
        exposures.append(exposure)
        if blocked:
            gate_blocked += 1
        monthly.append(
            {
                "as_of": d.isoformat(),
                "dominant_regime": dominant,
                "regime_dispersion": dispersion,
                "recommended_exposure": round(exposure, 4),
                "gate_would_block": blocked,
                "gate_reason": reason,
            }
        )

    return {
        "window": f"{start.isoformat()} -> {end.isoformat()}",
        "month_end_samples": len(samples),
        "dominant_regime_counts": dict(regime_counts),
        "dispersion_counts": dict(dispersion_counts),
        "avg_recommended_exposure": round(sum(exposures) / len(exposures), 4) if exposures else 0,
        "gate_blocked_month_ends": gate_blocked,
        "gate_blocked_pct": round(100 * gate_blocked / len(samples), 1) if samples else 0,
        "monthly": monthly,
    }


def write_report(
    rows: list[RunRow],
    regime_2y: dict[str, Any],
    gate_cfg,
) -> None:
    blind_off = next(r for r in rows if r.window == "blind_spot" and r.gate == "OFF")
    blind_on = next(r for r in rows if r.window == "blind_spot" and r.gate == "ON")
    primary_off = next(r for r in rows if r.window == "primary_2y" and r.gate == "OFF")
    primary_on = next(r for r in rows if r.window == "primary_2y" and r.gate == "ON")

    blind_delta_cagr = blind_on.cagr - blind_off.cagr
    primary_delta_cagr = primary_on.cagr - primary_off.cagr

    lines = [
        "# Sector Regime Gate Experiment",
        "",
        "Production `config.yaml` with modular `sector_regime_gate.enabled` toggle.",
        "",
        "## Gate logic",
        "",
        f"- dominant_regime == `{gate_cfg.require_dominant_regime}`",
        f"- regime_dispersion == `{gate_cfg.require_dispersion}`",
        f"- recommended_overall_exposure < {gate_cfg.max_recommended_exposure:.0%}",
        f"- council_window_months: {gate_cfg.council_window_months}",
        f"- skip_breadth (backtest speed): {gate_cfg.skip_breadth}",
        "",
        "## Backtest A/B",
        "",
        "| Window | Gate | CAGR | Max DD % | Trades | Win rate |",
        "|--------|------|------|----------|--------|----------|",
    ]
    for r in rows:
        lines.append(
            f"| {r.window} | {r.gate} | {r.cagr:.2%} | {r.max_drawdown_pct:.2f} | "
            f"{r.total_closed_trades} | {r.win_rate:.1%} |"
        )

    lines.extend(
        [
            "",
            "### Blind-spot window (index analog #3)",
            "",
            f"- Gate OFF: {blind_off.cagr:.2%} CAGR, {blind_off.max_drawdown_pct:.2f}% DD, {blind_off.total_closed_trades} trades",
            f"- Gate ON:  {blind_on.cagr:.2%} CAGR, {blind_on.max_drawdown_pct:.2f}% DD, {blind_on.total_closed_trades} trades",
            f"- Delta CAGR (ON - OFF): **{blind_delta_cagr:+.2%}**",
            "",
            "### Primary 2Y screen (production winner window)",
            "",
            f"- Gate OFF (baseline winner): {primary_off.cagr:.2%} CAGR, {primary_off.max_drawdown_pct:.2f}% DD",
            f"- Gate ON:  {primary_on.cagr:.2%} CAGR, {primary_on.max_drawdown_pct:.2f}% DD",
            f"- Delta CAGR (ON - OFF): **{primary_delta_cagr:+.2%}**",
            f"- Trade count: {primary_off.total_closed_trades} -> {primary_on.total_closed_trades}",
            "",
            "## Primary 2Y regime council (month-ends)",
            "",
            f"- Samples: {regime_2y['month_end_samples']} month-ends",
            f"- Dominant regime counts: {regime_2y['dominant_regime_counts']}",
            f"- Dispersion counts: {regime_2y['dispersion_counts']}",
            f"- Avg recommended exposure: {regime_2y['avg_recommended_exposure']:.0%}",
            f"- Month-ends gate would block: {regime_2y['gate_blocked_month_ends']}/{regime_2y['month_end_samples']} "
            f"({regime_2y['gate_blocked_pct']}%)",
            "",
            "See `sector-regime-gate-2y-regime.json` for per-month council detail.",
        ]
    )

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    REGIME_JSON.write_text(json.dumps(regime_2y, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--db", type=Path, default=None, help="Override data_db_path")
    parser.add_argument("--skip-backtests", action="store_true", help="Only run 2Y regime analysis")
    parser.add_argument("--skip-regime-analysis", action="store_true")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.db is not None:
        cfg.backtest.data_db_path = str(args.db).replace("\\", "/")

    db_path = ROOT / cfg.backtest.data_db_path
    vix_path = ROOT / cfg.sector_regime_gate.vix_csv_path
    if not db_path.is_file():
        print(f"Missing database: {db_path}", file=sys.stderr)
        return 1

    gate_cfg = cfg.sector_regime_gate
    rows: list[RunRow] = []

    if not args.skip_backtests:
        print("=== Sector regime gate A/B backtests ===", flush=True)
        for window, (start, end) in [("blind_spot", BLIND_SPOT), ("primary_2y", PRIMARY_2Y)]:
            for gate_on in (False, True):
                rows.append(run_backtest(cfg, start=start, end=end, gate_enabled=gate_on, window=window))

    regime_2y: dict[str, Any] = {}
    if not args.skip_regime_analysis:
        print("\n=== Primary 2Y regime council analysis ===", flush=True)
        regime_2y = analyze_2y_regime(db_path, vix_path, gate_cfg, *PRIMARY_2Y)
        print(
            f"  {regime_2y['gate_blocked_month_ends']}/{regime_2y['month_end_samples']} month-ends "
            f"would block entries ({regime_2y['gate_blocked_pct']}%)",
            flush=True,
        )

    if rows or regime_2y:
        write_report(rows, regime_2y, gate_cfg)
        print(f"\nWrote {RESULTS_PATH}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
