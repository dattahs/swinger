"""Robustness validation suite for Darvas strategy."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from src.backtest.backtester import Backtester
from src.backtest.validation.metrics import (
    compute_trade_metrics,
    recovery_days,
    slice_equity_by_regime,
    slice_trades_by_entry_regime,
)
from src.backtest.validation.regimes import build_regime_map, regime_day_counts
from src.config import AppConfig, apply_darvas_algo_overrides, load_config_relaxed
from src.repository.sqlite import SqliteDataLake

PF_THRESHOLD = 1.3
DD_THRESHOLD = 25.0

# User-facing names → (config short key, base value from optimal config.yaml)
PERTURBATION_PARAMS: dict[str, tuple[str, float, Literal["int", "float"]]] = {
    "lookback": ("adaptive_sma_period", 80.0, "int"),
    "atr_mult_entry": ("atr_multiplier", 2.0, "float"),
    "atr_mult_exit": ("breakout_reset_above_top_pct", 4.0, "float"),
}

PERTURBATION_PCTS = (-0.10, -0.05, 0.0, 0.05, 0.10)

STRESS_PERIODS: list[tuple[str, date, date]] = [
    ("COVID crash", date(2020, 2, 1), date(2020, 4, 30)),
    ("Inflation / rate hike", date(2022, 8, 1), date(2022, 11, 30)),
    ("Banking sector stress", date(2023, 3, 1), date(2023, 4, 30)),
    ("Mid-2024 correction", date(2024, 9, 1), date(2024, 10, 31)),
    ("Jan 2025 pullback", date(2025, 1, 1), date(2025, 2, 28)),
]

REGIME_START = date(2019, 1, 1)
REGIME_END = date(2026, 5, 31)
VALIDATION_START = date(2024, 6, 1)
VALIDATION_END = date(2026, 5, 31)


@dataclass
class RunMetrics:
    label: str
    start: date
    end: date
    metrics: dict[str, Any]
    elapsed_sec: float = 0.0
    overrides: dict[str, Any] = field(default_factory=dict)


def _perturbed_value(base: float, pct: float, kind: Literal["int", "float"]) -> float | int:
    raw = base * (1.0 + pct)
    if kind == "int":
        return max(1, int(round(raw)))
    return round(raw, 4)


def _prepare_config(
    base_config_path: Path,
    *,
    overrides: dict[str, Any] | None = None,
) -> AppConfig:
    cfg = load_config_relaxed(base_config_path)
    if overrides:
        cfg = apply_darvas_algo_overrides(cfg, overrides)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False
    cfg.backtest.timestamped_runs = False
    return cfg


def _run_quiet(
    cfg: AppConfig,
    start: date,
    end: date,
    *,
    repo_root: Path,
    label: str,
    cache_holder: dict[str, Backtester | None],
) -> RunMetrics:
    t0 = time.monotonic()
    bt = cache_holder.get("bt")
    if bt is None:
        bt = Backtester(cfg, repo_root=repo_root)
        cache_holder["bt"] = bt
    else:
        bt.config = cfg
        bt._reset_for_run()

    result = bt.run(start=start, end=end, persist_outputs=False)
    metrics = compute_trade_metrics(
        result.closed_trades,
        result.equity_curve,
        initial_capital=cfg.backtest.initial_capital_inr,
        start_date=start,
        end_date=end,
    )
    elapsed = time.monotonic() - t0
    return RunMetrics(label=label, start=start, end=end, metrics=metrics, elapsed_sec=elapsed)


def run_regime_sensitivity(
    base_config_path: Path,
    *,
    repo_root: Path,
    cache_holder: dict[str, Backtester | None],
) -> list[RunMetrics]:
    cfg = _prepare_config(base_config_path)
    index = cfg.darvas_box.market_trend_filter.index
    db_path = repo_root / cfg.backtest.data_db_path
    lake = SqliteDataLake(db_path)
    regime_map = build_regime_map(lake, index, REGIME_START, REGIME_END)

    full = _run_quiet(
        cfg,
        REGIME_START,
        REGIME_END,
        repo_root=repo_root,
        label="full_history",
        cache_holder=cache_holder,
    )

    bt = cache_holder["bt"]
    assert bt is not None
    trades = pd.DataFrame(bt.broker.portfolio.closed_trades)
    equity = pd.DataFrame(bt.equity_curve)

    results: list[RunMetrics] = []
    for regime in ("BULL", "BEAR", "SIDEWAYS"):
        r_trades = slice_trades_by_entry_regime(trades, regime_map, regime)
        r_equity = slice_equity_by_regime(equity, regime_map, regime)
        m = compute_trade_metrics(
            r_trades,
            r_equity,
            initial_capital=cfg.backtest.initial_capital_inr,
        )
        if not r_equity.empty:
            start_eq = float(r_equity.iloc[0]["equity"])
            end_eq = float(r_equity.iloc[-1]["equity"])
            m["return_pct"] = round(100.0 * (end_eq - start_eq) / start_eq, 2) if start_eq > 0 else None
        else:
            m["return_pct"] = None
        m["regime_days"] = regime_day_counts(regime_map).get(regime, 0)
        results.append(
            RunMetrics(
                label=regime,
                start=REGIME_START,
                end=REGIME_END,
                metrics=m,
            )
        )
    return results


def run_parameter_perturbation(
    base_config_path: Path,
    *,
    repo_root: Path,
    cache_holder: dict[str, Backtester | None],
) -> list[RunMetrics]:
    results: list[RunMetrics] = []
    for param_name, (config_key, base_val, kind) in PERTURBATION_PARAMS.items():
        for pct in PERTURBATION_PCTS:
            value = _perturbed_value(base_val, pct, kind)
            overrides = {config_key: value}
            label = f"{param_name}@{pct:+.0%}"
            cfg = _prepare_config(base_config_path, overrides=overrides)
            run = _run_quiet(
                cfg,
                VALIDATION_START,
                VALIDATION_END,
                repo_root=repo_root,
                label=label,
                cache_holder=cache_holder,
            )
            run.overrides = {param_name: value, "pct_change": pct}
            results.append(run)
    return results


def run_stress_periods(
    base_config_path: Path,
    *,
    repo_root: Path,
    cache_holder: dict[str, Backtester | None],
) -> list[RunMetrics]:
    cfg = _prepare_config(base_config_path)
    results: list[RunMetrics] = []

    for name, stress_start, stress_end in STRESS_PERIODS:
        trading_days = SqliteDataLake(repo_root / cfg.backtest.data_db_path).get_trading_days(
            stress_start, stress_end
        )
        if not trading_days:
            continue

        pre_start = date(stress_start.year - 1, stress_start.month, stress_start.day)
        run = _run_quiet(
            cfg,
            pre_start,
            stress_end,
            repo_root=repo_root,
            label=name,
            cache_holder=cache_holder,
        )

        bt = cache_holder["bt"]
        assert bt is not None
        equity = pd.DataFrame(bt.equity_curve)
        equity["date"] = pd.to_datetime(equity["date"])
        pre_eq = equity[equity["date"] < pd.Timestamp(stress_start)]
        stress_eq = equity[
            (equity["date"] >= pd.Timestamp(stress_start))
            & (equity["date"] <= pd.Timestamp(stress_end))
        ]
        pre_high = float(pre_eq.iloc[-1]["equity"]) if not pre_eq.empty else cfg.backtest.initial_capital_inr

        trades = pd.DataFrame(bt.broker.portfolio.closed_trades)
        if not trades.empty:
            trades["exit_date"] = pd.to_datetime(trades["exit_date"])
            stress_trades = trades[
                (trades["exit_date"] >= pd.Timestamp(stress_start))
                & (trades["exit_date"] <= pd.Timestamp(stress_end))
            ]
        else:
            stress_trades = trades

        m = compute_trade_metrics(
            stress_trades,
            stress_eq,
            initial_capital=pre_high,
        )
        m["recovery_days"] = recovery_days(stress_eq, pre_crash_high=pre_high)
        m["pre_stress_equity_inr"] = round(pre_high, 2)

        results.append(
            RunMetrics(
                label=name,
                start=stress_start,
                end=stress_end,
                metrics=m,
                elapsed_sec=run.elapsed_sec,
            )
        )
    return results


def _is_robust_across_regimes(regime_results: list[RunMetrics]) -> bool:
    pfs = [r.metrics.get("profit_factor") for r in regime_results]
    valid = [p for p in pfs if p is not None and p != float("inf")]
    if len(valid) < 2:
        return False
    return min(valid) >= PF_THRESHOLD and (max(valid) - min(valid)) / max(valid) < 0.5


def _params_fragile_at_5pct(perturbation_results: list[RunMetrics]) -> bool:
    """True if any ±5% perturbation drops PF below threshold or >30% vs baseline."""
    baseline_pf: dict[str, float] = {}
    for r in perturbation_results:
        if r.overrides.get("pct_change") == 0.0:
            param = next(k for k in r.overrides if k != "pct_change")
            pf = r.metrics.get("profit_factor")
            if pf is not None and pf != float("inf"):
                baseline_pf[param] = pf

    for r in perturbation_results:
        pct = r.overrides.get("pct_change")
        if pct not in (-0.05, 0.05):
            continue
        param = next((k for k in r.overrides if k != "pct_change"), None)
        pf = r.metrics.get("profit_factor")
        if pf is None or pf == float("inf"):
            continue
        if pf < PF_THRESHOLD:
            return True
        base = baseline_pf.get(param or "")
        if base and pf < 0.7 * base:
            return True
    return False


def _survives_crashes(stress_results: list[RunMetrics]) -> bool:
    for r in stress_results:
        pf = r.metrics.get("profit_factor")
        dd = r.metrics.get("max_drawdown_pct", 0)
        if dd > DD_THRESHOLD:
            return False
        if pf is not None and pf != float("inf") and pf < 1.0:
            return False
    return True


def collect_red_flags(
    regime_results: list[RunMetrics],
    perturbation_results: list[RunMetrics],
    stress_results: list[RunMetrics],
) -> list[str]:
    flags: list[str] = []
    for r in regime_results:
        pf = r.metrics.get("profit_factor")
        dd = r.metrics.get("max_drawdown_pct", 0)
        if pf is not None and pf != float("inf") and pf < PF_THRESHOLD:
            flags.append(f"Regime {r.label}: PF {pf} < {PF_THRESHOLD}")
        if dd > DD_THRESHOLD:
            flags.append(f"Regime {r.label}: Max DD {dd}% > {DD_THRESHOLD}%")

    for r in stress_results:
        pf = r.metrics.get("profit_factor")
        dd = r.metrics.get("max_drawdown_pct", 0)
        if pf is not None and pf != float("inf") and pf < PF_THRESHOLD:
            flags.append(f"Stress {r.label}: PF {pf} < {PF_THRESHOLD}")
        if dd > DD_THRESHOLD:
            flags.append(f"Stress {r.label}: Max DD {dd}% > {DD_THRESHOLD}%")

    for r in perturbation_results:
        pct = r.overrides.get("pct_change")
        if pct not in (-0.05, 0.05):
            continue
        pf = r.metrics.get("profit_factor")
        if pf is not None and pf != float("inf") and pf < PF_THRESHOLD:
            param = next((k for k in r.overrides if k != "pct_change"), "?")
            flags.append(f"Perturbation {param} {pct:+.0%}: PF {pf} < {PF_THRESHOLD}")

    return flags


def _fmt_pf(pf: Any) -> str:
    if pf is None:
        return "n/a"
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


def build_markdown_report(
    regime_results: list[RunMetrics],
    perturbation_results: list[RunMetrics],
    stress_results: list[RunMetrics],
    *,
    config_path: str,
    invoked_at: datetime,
) -> str:
    robust = _is_robust_across_regimes(regime_results)
    fragile = _params_fragile_at_5pct(perturbation_results)
    survives = _survives_crashes(stress_results)
    red_flags = collect_red_flags(regime_results, perturbation_results, stress_results)

    lines = [
        "# Darvas Strategy — Robustness Validation Report",
        "",
        f"**Generated:** {invoked_at.isoformat(timespec='seconds')}",
        f"**Config:** `{config_path}`",
        f"**Optimization metric:** Profit Factor (threshold >= {PF_THRESHOLD})",
        "",
        "## 1. Regime Sensitivity",
        "",
        f"Period: {REGIME_START} → {REGIME_END} | Regime = NIFTY 50 trailing 252-day return "
        f"(BULL > +5%, BEAR < -5%, SIDEWAYS otherwise)",
        "",
        "| Regime | PF | Max DD % | Win Rate % | Payoff | Trades | Return % | Regime Days |",
        "|--------|-----|----------|------------|--------|--------|----------|-------------|",
    ]

    for r in regime_results:
        m = r.metrics
        lines.append(
            f"| {r.label} | {_fmt_pf(m.get('profit_factor'))} | {m.get('max_drawdown_pct', 'n/a')} "
            f"| {m.get('win_rate_pct', 'n/a')} | {m.get('payoff_ratio', 'n/a')} "
            f"| {m.get('num_trades', 0)} | {m.get('return_pct', 'n/a')} "
            f"| {m.get('regime_days', 'n/a')} |"
        )

    lines.extend(
        [
            "",
            "## 2. Parameter Perturbation (±10% / ±5%)",
            "",
            f"Validation window: {VALIDATION_START} → {VALIDATION_END}",
            "",
            "| Parameter | Δ% | Value | PF | Max DD % | Win Rate % | Trades | Stability |",
            "|-----------|-----|-------|-----|----------|------------|--------|-----------|",
        ]
    )

    baseline_by_param: dict[str, float] = {}
    for r in perturbation_results:
        if r.overrides.get("pct_change") == 0.0:
            param = next(k for k in r.overrides if k != "pct_change")
            pf = r.metrics.get("profit_factor")
            if pf is not None and pf != float("inf"):
                baseline_by_param[param] = pf

    for r in perturbation_results:
        param = next((k for k in r.overrides if k != "pct_change"), "?")
        pct = r.overrides.get("pct_change", 0)
        val = r.overrides.get(param)
        m = r.metrics
        pf = m.get("profit_factor")
        base = baseline_by_param.get(param)
        if pct == 0.0:
            stability = "baseline"
        elif base and pf is not None and pf != float("inf"):
            delta = (pf - base) / base
            if abs(delta) <= 0.15:
                stability = "plateau"
            elif abs(delta) <= 0.30:
                stability = "moderate"
            else:
                stability = "**sharp**"
        else:
            stability = "n/a"
        lines.append(
            f"| {param} | {pct:+.0%} | {val} | {_fmt_pf(pf)} | {m.get('max_drawdown_pct')} "
            f"| {m.get('win_rate_pct')} | {m.get('num_trades')} | {stability} |"
        )

    lines.extend(
        [
            "",
            "_Parameter mapping: `lookback` → adaptive SMA period; `atr_mult_entry` → `atr_multiplier`; "
            "`atr_mult_exit` → `breakout_reset_above_top_pct` (stale-breakout reset)._",
            "",
            "## 3. Stress Period Tests",
            "",
            "| Period | Window | PF | Max DD % | Win Rate % | Recovery Days | Trades |",
            "|--------|--------|-----|----------|------------|---------------|--------|",
        ]
    )

    for r in stress_results:
        m = r.metrics
        rec = m.get("recovery_days")
        rec_s = str(rec) if rec is not None else "not recovered"
        lines.append(
            f"| {r.label} | {r.start} → {r.end} | {_fmt_pf(m.get('profit_factor'))} "
            f"| {m.get('max_drawdown_pct')} | {m.get('win_rate_pct')} | {rec_s} "
            f"| {m.get('num_trades')} |"
        )

    lines.extend(
        [
            "",
            "## 4. Overall Assessment",
            "",
            f"| Criterion | Result |",
            f"|-----------|--------|",
            f"| Stable across regimes (BULL/BEAR/SIDEWAYS)? | **{'YES' if robust else 'NO'}** |",
            f"| Parameters fragile at ±5% (PF collapse)? | **{'YES' if fragile else 'NO'}** |",
            f"| Survives known crash windows? | **{'YES' if survives else 'NO'}** |",
            "",
            "### Red Flags",
            "",
        ]
    )

    if red_flags:
        for flag in red_flags:
            lines.append(f"- {flag}")
    else:
        lines.append("- None - all tests met PF >= 1.3 and Max DD <= 25% thresholds.")

    lines.extend(
        [
            "",
            "### Recommendation",
            "",
        ]
    )

    if not red_flags and robust and not fragile and survives:
        lines.append(
            "Strategy shows acceptable robustness for a 2-year live deployment pilot. "
            "Continue paper trading with fixed parameters; re-run this suite quarterly."
        )
    elif red_flags:
        lines.append(
            "Review red flags before live deployment. Consider widening parameter plateaus "
            "or adding regime filters if BEAR/SIDEWAYS underperform."
        )
    else:
        lines.append("Mixed results — proceed with caution and extended paper trading.")

    return "\n".join(lines)


def run_validation_suite(
    base_config_path: Path,
    *,
    repo_root: Path,
    output_dir: Path | None = None,
) -> tuple[Path, str]:
    """Run all validation tests and write markdown + JSON report."""
    invoked_at = datetime.now()
    out_base = output_dir or (repo_root / "validation_outputs")
    stamp = invoked_at.strftime("validation_%Y%m%d_%H%M%S")
    out_dir = out_base / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_holder: dict[str, Backtester | None] = {"bt": None}
    print("Running regime sensitivity...", flush=True)
    regime_results = run_regime_sensitivity(base_config_path, repo_root=repo_root, cache_holder=cache_holder)

    print("Running parameter perturbation...", flush=True)
    perturbation_results = run_parameter_perturbation(
        base_config_path, repo_root=repo_root, cache_holder=cache_holder
    )

    print("Running stress period tests...", flush=True)
    stress_results = run_stress_periods(base_config_path, repo_root=repo_root, cache_holder=cache_holder)

    report_md = build_markdown_report(
        regime_results,
        perturbation_results,
        stress_results,
        config_path=str(base_config_path),
        invoked_at=invoked_at,
    )

    def _serialize(results: list[RunMetrics]) -> list[dict]:
        return [
            {
                "label": r.label,
                "start": r.start.isoformat(),
                "end": r.end.isoformat(),
                "elapsed_sec": round(r.elapsed_sec, 1),
                "overrides": r.overrides,
                "metrics": {
                    k: (None if v == float("inf") else v)
                    for k, v in r.metrics.items()
                    if not k.startswith("_")
                },
            }
            for r in results
        ]

    payload = {
        "invoked_at": invoked_at.isoformat(timespec="seconds"),
        "config_path": str(base_config_path),
        "pf_threshold": PF_THRESHOLD,
        "dd_threshold_pct": DD_THRESHOLD,
        "regime": _serialize(regime_results),
        "perturbation": _serialize(perturbation_results),
        "stress": _serialize(stress_results),
        "assessment": {
            "robust_across_regimes": _is_robust_across_regimes(regime_results),
            "params_fragile_at_5pct": _params_fragile_at_5pct(perturbation_results),
            "survives_crashes": _survives_crashes(stress_results),
            "red_flags": collect_red_flags(regime_results, perturbation_results, stress_results),
        },
    }

    md_path = out_dir / "validation_report.md"
    json_path = out_dir / "validation_results.json"
    md_path.write_text(report_md, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"Report written to {md_path}", flush=True)
    return out_dir, report_md
