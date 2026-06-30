#!/usr/bin/env python3
"""Darvas regime-analog parameter search — multi-round screening + validation."""

from __future__ import annotations

import argparse
import copy
import json
import random
import shutil
import smtplib
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.index_curve_match import find_index_analogs, load_index_daily_bars
from src.analysis.vix_curve_match import find_vix_analogs
from src.backtest.backtester import Backtester
from src.config import apply_darvas_algo_overrides, load_config, load_config_relaxed
from src.data.vix_data import load_or_download_vix
from src.notify.backtest_email import load_email_settings_from_env

PRIMARY_START = date(2024, 6, 1)
PRIMARY_END = date(2026, 5, 31)
REF_START = date(2024, 12, 1)
REF_END = date(2026, 5, 31)
SEARCH_START = date(2017, 1, 1)
MAX_ROUNDS = 3
SHORTLIST_K = 5
RELATIVE_TOP_K = 2
MAX_DD_FEASIBLE = 10.0
MAX_DD_ANALOG = 8.0
BEAT_CAGR_PP = 0.005
BEAT_DD_PP = 0.5

LOG_PATH = ROOT / "src" / "agentic-loop" / "regime-search-log.jsonl"
RESULTS_PATH = ROOT / "src" / "agentic-loop" / "regime-search-results.md"


@dataclass
class RunMetrics:
    config_name: str
    cagr: float
    max_drawdown_pct: float
    total_closed_trades: int
    win_rate: float
    run_dir: str
    feasible: bool
    score: float

    @classmethod
    def from_summary(cls, config_name: str, summary: dict, run_dir: str) -> RunMetrics:
        cagr = float(summary["cagr"])
        max_dd = float(summary["max_drawdown_pct"])
        feasible = max_dd <= MAX_DD_FEASIBLE
        return cls(
            config_name=config_name,
            cagr=cagr,
            max_drawdown_pct=max_dd,
            total_closed_trades=int(summary.get("total_closed_trades", 0)),
            win_rate=float(summary.get("win_rate", 0)),
            run_dir=run_dir,
            feasible=feasible,
            score=cagr if feasible else -1.0,
        )


@dataclass
class ExperimentSpec:
    name: str
    overrides: dict[str, Any] = field(default_factory=dict)
    source_path: str | None = None


ROUND1_SPECS: list[ExperimentSpec] = [
    ExperimentSpec("baseline"),
    ExperimentSpec("sma30-reset4", {"universe_filters.adaptive_new_high_lookback.sma_period": 30}),
    ExperimentSpec("sma80-reset4", {"universe_filters.adaptive_new_high_lookback.sma_period": 80}),
    ExperimentSpec("reset-tight-0.5", {"darvas_box.breakout_reset_above_top_pct": 0.5}),
    ExperimentSpec("reset-loose-4.0", {"darvas_box.breakout_reset_above_top_pct": 4.0}),
    ExperimentSpec("box-dur-5", {"darvas_box.min_box_duration_days": 5}),
    ExperimentSpec("box-height-4pct", {"darvas_box.min_box_height_pct": 4.0}),
    ExperimentSpec("trail-risk-8", {"trailing_stop.max_trail_risk_pct": 8.0}),
    ExperimentSpec(
        "r-managed-on",
        {
            "r_managed_runner.enabled": True,
            "r_managed_runner.breakeven_r_threshold": 0.8,
            "r_managed_runner.max_target_r": 5.0,
        },
    ),
    ExperimentSpec(
        "lookback-narrow",
        {
            "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 12,
            "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 30,
        },
    ),
    ExperimentSpec(
        "lookback-wide",
        {
            "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 6,
            "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 45,
        },
    ),
    ExperimentSpec(
        "baseline-next-best",
        source_path="configs/baseline-next-best.yaml",
    ),
]

MUTATION_AXES: dict[str, list[Any]] = {
    "sma": [30, 50, 80],
    "band": [
        (12, 30),
        (9, 39),
        (6, 45),
    ],
    "reset": [1.0, 2.0, 4.0],
    "box_dur": [4, 5, 6],
    "box_height": [3.0, 4.0, 5.0],
    "trail_risk": [8.0, 10.0],
    "r_managed": [False, True],
    "stale_tsl": [8.0, 10.0, 12.0],
}


def _deep_set(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _disable_experiment_logging(raw: dict) -> None:
    raw.setdefault("backtest", {})
    raw["backtest"]["send_email_on_complete"] = False
    raw["backtest"].setdefault("progress_log", {})["enabled"] = False
    raw["backtest"].setdefault("debug_log", {})["enabled"] = False


def write_experiment_yaml(
    spec: ExperimentSpec,
    *,
    round_num: int,
    export_subdir: str,
    db_path: Path | None = None,
) -> Path:
    round_dir = ROOT / "configs" / "experiments" / f"round-{round_num:02d}"
    round_dir.mkdir(parents=True, exist_ok=True)
    dest = round_dir / f"{spec.name}.yaml"

    if spec.source_path:
        src = ROOT / spec.source_path
        shutil.copy2(src, dest)
        raw = yaml.safe_load(dest.read_text(encoding="utf-8"))
        _disable_experiment_logging(raw)
        raw["backtest"]["export_directory"] = export_subdir
        if db_path is not None:
            raw["backtest"]["data_db_path"] = str(db_path).replace("\\", "/")
        dest.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        return dest

    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    _disable_experiment_logging(base)
    base["backtest"]["export_directory"] = export_subdir
    if db_path is not None:
        base["backtest"]["data_db_path"] = str(db_path).replace("\\", "/")
    for key, value in spec.overrides.items():
        _deep_set(base, key, value)
    dest.write_text(yaml.safe_dump(base, sort_keys=False), encoding="utf-8")
    return dest


def append_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def load_log() -> list[dict]:
    if not LOG_PATH.is_file():
        return []
    return [json.loads(line) for line in LOG_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize_completed_round(records: list[dict], round_num: int) -> list[str]:
    """Rebuild markdown sections for a round already present in the JSONL log."""
    rnd = [r for r in records if r.get("round") == round_num]
    screening = [r for r in rnd if r.get("phase") == "screening"]
    if not screening:
        return [f"## Round {round_num:02d} — (no log data)", ""]

    ranked = sorted(
        screening,
        key=lambda r: (-r.get("score", -1), r.get("max_drawdown_pct", 99), -r.get("total_closed_trades", 0)),
    )
    shortlist = [r["config"] for r in ranked[:SHORTLIST_K]]

    lines = [
        f"## Round {round_num:02d} — Screening (resumed from log)",
        "",
        "### Leaderboard (primary window)",
        "| Rank | Config | CAGR | Max DD | Trades | Feasible | Score |",
        "|------|--------|------|--------|--------|----------|-------|",
    ]
    for i, r in enumerate(ranked, 1):
        lines.append(
            f"| {i} | {r['config']} | {r['cagr']:.2%} | {r['max_drawdown_pct']:.2f}% | "
            f"{r.get('total_closed_trades', '')} | {r.get('feasible', '')} | {r.get('score', 0):.4f} |"
        )
    lines += ["", f"**Top {SHORTLIST_K} shortlist:** {', '.join(shortlist)}", ""]

    for phase, title in (("index-analog", "Index analog"), ("vix-analog", "VIX analog")):
        phase_rows = [r for r in rnd if r.get("phase") == phase]
        if not phase_rows:
            continue
        analog_data: dict[str, list[dict]] = {n: [] for n in shortlist}
        for r in phase_rows:
            if r["config"] in analog_data:
                analog_data[r["config"]].append(r)
        verdict = _assess_consistency_from_log(analog_data, shortlist)
        lines += [
            f"## Round {round_num:02d} — {title} consistency (resumed from log)",
            "",
            f"| Config | Window | CAGR | Max DD | Trades | Consistent? |",
            f"|--------|--------|------|--------|--------|-------------|",
        ]
        for name in shortlist:
            for i, r in enumerate(analog_data.get(name, []), 1):
                lines.append(
                    f"| {name} | {i} | {r['cagr']:.2%} | {r['max_drawdown_pct']:.2f}% | "
                    f"{r.get('total_closed_trades', '')} | {'✓' if verdict.get(name) else '✗'} |"
                )
        lines.append("")

    index_ok = _assess_consistency_from_log(
        {n: [r for r in rnd if r.get("phase") == "index-analog" and r["config"] == n] for n in shortlist},
        shortlist,
    )
    vix_ok = _assess_consistency_from_log(
        {n: [r for r in rnd if r.get("phase") == "vix-analog" and r["config"] == n] for n in shortlist},
        shortlist,
    )
    promoted = [n for n in shortlist if index_ok.get(n) and vix_ok.get(n)]
    lines += [f"## Round {round_num:02d} — Winners promoted", ""]
    if promoted:
        for n in promoted:
            lines.append(f"- **{n}** (passed both analog tests)")
    else:
        lines.append("_No configs passed both analog tests._")
    lines.append("")
    return lines


def _assess_consistency_from_log(
    analog_rows: dict[str, list[dict]],
    shortlist: list[str],
) -> dict[str, bool]:
    verdict: dict[str, bool] = {name: True for name in shortlist}
    if not any(analog_rows.values()):
        return {name: False for name in shortlist}
    window_count = max(len(v) for v in analog_rows.values())
    for w_idx in range(window_count):
        window_metrics = [analog_rows[name][w_idx] for name in shortlist if w_idx < len(analog_rows[name])]
        if not window_metrics:
            continue
        ranked = sorted(window_metrics, key=lambda r: -r["cagr"])
        top_names = {ranked[i]["config"] for i in range(min(RELATIVE_TOP_K, len(ranked)))}
        for r in window_metrics:
            if r["cagr"] < 0 or r["max_drawdown_pct"] > MAX_DD_ANALOG:
                verdict[r["config"]] = False
            elif r["config"] not in top_names:
                verdict[r["config"]] = False
    return verdict


def rank_metrics(rows: list[RunMetrics]) -> list[RunMetrics]:
    return sorted(
        rows,
        key=lambda r: (-r.score, r.max_drawdown_pct, -r.total_closed_trades),
    )


def run_backtest(
    config_path: Path,
    *,
    start: date,
    end: date,
    phase: str,
    round_num: int,
    config_name: str,
    db_path: Path | None = None,
) -> RunMetrics:
    cfg = load_config(config_path)
    if db_path is not None:
        cfg.backtest.data_db_path = str(db_path).replace("\\", "/")
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False

    t0 = time.monotonic()
    bt = Backtester(cfg, repo_root=ROOT)
    run_dir = Path(bt.run(start=start, end=end))
    elapsed = time.monotonic() - t0

    summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
    metrics = RunMetrics.from_summary(config_name, summary, str(run_dir))
    append_log(
        {
            "phase": phase,
            "round": round_num,
            "config": config_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "cagr": metrics.cagr,
            "max_drawdown_pct": metrics.max_drawdown_pct,
            "total_closed_trades": metrics.total_closed_trades,
            "win_rate": metrics.win_rate,
            "feasible": metrics.feasible,
            "score": metrics.score,
            "run_dir": metrics.run_dir,
            "elapsed_sec": round(elapsed, 1),
        }
    )
    print(
        f"  {config_name} [{phase}]: CAGR={metrics.cagr:.2%} DD={metrics.max_drawdown_pct:.2f}% "
        f"({elapsed:.0f}s)",
        flush=True,
    )
    return metrics


def assess_consistency(
    analog_rows: dict[str, list[RunMetrics]],
    shortlist: list[str],
) -> dict[str, bool]:
    """Per-config consistency across all analog windows for one analog type."""
    verdict: dict[str, bool] = {name: True for name in shortlist}
    window_count = len(next(iter(analog_rows.values())))
    for w_idx in range(window_count):
        window_metrics = [analog_rows[name][w_idx] for name in shortlist]
        ranked = sorted(window_metrics, key=lambda m: -m.cagr)
        top2 = {ranked[i].config_name for i in range(min(RELATIVE_TOP_K, len(ranked)))}
        for m in window_metrics:
            if m.cagr < 0 or m.max_drawdown_pct > MAX_DD_ANALOG:
                verdict[m.config_name] = False
            elif m.config_name not in top2:
                verdict[m.config_name] = False
    return verdict


def beats_incumbents(candidate: RunMetrics, incumbents: list[RunMetrics]) -> bool:
    if not incumbents:
        return True
    best = max(incumbents, key=lambda m: m.cagr)
    return (
        candidate.cagr >= best.cagr + BEAT_CAGR_PP
        and candidate.max_drawdown_pct <= best.max_drawdown_pct + BEAT_DD_PP
    )


def load_overrides_from_yaml(path: Path) -> dict[str, Any]:
    from src.config import DARVAS_ALGO_PARAM_PATHS, darvas_algo_snapshot

    base_snap = darvas_algo_snapshot(load_config_relaxed(ROOT / "config.yaml"))
    cand_snap = darvas_algo_snapshot(load_config_relaxed(path))
    overrides: dict[str, Any] = {}
    for key, val in cand_snap.items():
        if base_snap.get(key) != val:
            overrides[DARVAS_ALGO_PARAM_PATHS.get(key, key)] = val
    return overrides


def generate_mutations(
    base_name: str,
    base_overrides: dict[str, Any],
    round_num: int,
    *,
    count: int = 5,
) -> list[ExperimentSpec]:
    rng = random.Random(42 + round_num)
    specs: list[ExperimentSpec] = []
    seen: set[str] = set()

    def add(name_suffix: str, overrides: dict[str, Any]) -> None:
        key = name_suffix
        if key in seen:
            return
        seen.add(key)
        merged = copy.deepcopy(base_overrides)
        merged.update(overrides)
        specs.append(
            ExperimentSpec(
                f"round-{round_num:02d}-{base_name}-{name_suffix}",
                merged,
            )
        )

    combos = [
        ("sma50", {"universe_filters.adaptive_new_high_lookback.sma_period": 50}),
        ("reset-2", {"darvas_box.breakout_reset_above_top_pct": 2.0}),
        ("box-dur-6", {"darvas_box.min_box_duration_days": 6}),
        ("trail-8", {"trailing_stop.max_trail_risk_pct": 8.0}),
        ("stale-tsl-12", {"risk_management.stale_box_tsl_daily_pct": 12.0}),
        ("r-managed", {
            "r_managed_runner.enabled": True,
            "r_managed_runner.breakeven_r_threshold": 0.8,
            "r_managed_runner.max_target_r": 5.0,
        }),
        ("band-narrow", {
            "universe_filters.adaptive_new_high_lookback.min_lookback_weeks": 12,
            "universe_filters.adaptive_new_high_lookback.max_lookback_weeks": 30,
        }),
        ("box-height-5", {"darvas_box.min_box_height_pct": 5.0}),
    ]
    rng.shuffle(combos)
    for suffix, ov in combos[:count]:
        add(suffix, ov)

    return specs[:count]


def write_winner_summary(
    *,
    round_num: int,
    config_name: str,
    overrides: dict[str, Any],
    primary: RunMetrics,
    index_rows: list[tuple[Any, RunMetrics]],
    vix_rows: list[tuple[Any, RunMetrics]],
    index_ok: bool,
    vix_ok: bool,
) -> Path:
    winner_dir = ROOT / "configs" / "winners" / f"round-{round_num:02d}"
    winner_dir.mkdir(parents=True, exist_ok=True)
    md_path = winner_dir / f"{config_name}-summary.md"
    lines = [
        f"# Winner: {config_name} (round {round_num:02d})",
        "",
        "## Parameter overrides",
        "```yaml",
    ]
    for k, v in sorted(overrides.items()):
        lines.append(f"{k}: {v}")
    lines += [
        "```",
        "",
        "## Primary window",
        f"- Window: {PRIMARY_START} → {PRIMARY_END}",
        f"- CAGR: {primary.cagr:.2%}",
        f"- Max DD: {primary.max_drawdown_pct:.2f}%",
        f"- Trades: {primary.total_closed_trades}",
        f"- Summary: `{primary.run_dir}/summary_report.json`",
        "",
        "## Index analog windows",
        "| # | Index window | Backtest window | CAGR | Max DD | Trades |",
        "|---|--------------|-----------------|------|--------|--------|",
    ]
    for i, (match, m) in enumerate(index_rows, 1):
        lines.append(
            f"| {i} | {match.analog_start}→{match.analog_end} | "
            f"{match.backtest_start}→{match.backtest_end} | {m.cagr:.2%} | "
            f"{m.max_drawdown_pct:.2f}% | {m.total_closed_trades} |"
        )
    lines += [
        "",
        "## VIX analog windows",
        "| # | VIX window | Backtest window | CAGR | Max DD | Trades |",
        "|---|------------|-----------------|------|--------|--------|",
    ]
    for i, (match, m) in enumerate(vix_rows, 1):
        lines.append(
            f"| {i} | {match.analog_start}→{match.analog_end} | "
            f"{match.backtest_start}→{match.backtest_end} | {m.cagr:.2%} | "
            f"{m.max_drawdown_pct:.2f}% | {m.total_closed_trades} |"
        )
    lines += [
        "",
        "## Consistency",
        f"- Index analogs: {'PASS' if index_ok else 'FAIL'}",
        f"- VIX analogs: {'PASS' if vix_ok else 'FAIL'}",
    ]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return md_path


def send_completion_email(body: str, attachments: list[Path]) -> None:
    settings = load_email_settings_from_env()
    msg = EmailMessage()
    msg["Subject"] = "Swinger regime-analog search — experiment complete"
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(body)
    msg.add_alternative(f"<html><body><pre>{escape(body)}</pre></body></html>", subtype="html")

    if attachments:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            zip_path = Path(tmp.name)
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in attachments:
                if path.is_file():
                    zf.write(path, arcname=path.name)
                elif path.is_dir():
                    for f in path.rglob("*"):
                        if f.is_file():
                            zf.write(f, arcname=str(f.relative_to(path.parent.parent)))
        msg.add_attachment(
            zip_path.read_bytes(),
            maintype="application",
            subtype="zip",
            filename="regime-search-artifacts.zip",
        )
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=120) as smtp:
                if settings.use_tls:
                    smtp.starttls()
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.send_message(msg)
        finally:
            zip_path.unlink(missing_ok=True)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=120) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    print(f"Completion email sent to {', '.join(settings.to_addrs)}", flush=True)


def verify_prerequisites(db_path: Path) -> date | None:
    if not db_path.is_file():
        raise FileNotFoundError(f"Missing data lake: {db_path}")
    bars = load_index_daily_bars(db_path, start=SEARCH_START, end=PRIMARY_END)
    if bars.empty:
        raise ValueError("No index bars in database")
    data_end = bars["date"].iloc[-1]
    if data_end < PRIMARY_END:
        print(f"WARN: data ends {data_end}, expected at least {PRIMARY_END}", flush=True)
    print(f"Data lake OK: {len(bars)} NIFTY 50 sessions through {data_end}", flush=True)
    vix = load_or_download_vix(ROOT, SEARCH_START, REF_END)
    print(f"India VIX OK: {len(vix)} sessions", flush=True)
    return data_end


def run_round(
    round_num: int,
    specs: list[ExperimentSpec],
    *,
    db_path: Path,
    incumbents: list[dict],
    results_sections: list[str],
) -> tuple[list[dict], bool, str]:
    """Run one full round. Returns updated incumbents and whether to continue."""
    export_base = f"./backtest_outputs/experiments/round-{round_num:02d}"
    screening_dir = f"{export_base}/screening"
    index_dir = f"{export_base}/index-analogs"
    vix_dir = f"{export_base}/vix-analogs"

    print(f"\n{'='*60}\nRound {round_num:02d} — screening ({len(specs)} configs)\n{'='*60}", flush=True)
    config_paths: dict[str, Path] = {}
    for spec in specs:
        config_paths[spec.name] = write_experiment_yaml(
            spec, round_num=round_num, export_subdir=screening_dir, db_path=db_path
        )

    screening: list[RunMetrics] = []
    for spec in specs:
        screening.append(
            run_backtest(
                config_paths[spec.name],
                start=PRIMARY_START,
                end=PRIMARY_END,
                phase="screening",
                round_num=round_num,
                config_name=spec.name,
                db_path=db_path,
            )
        )

    ranked = rank_metrics(screening)
    shortlist = [m.config_name for m in ranked[:SHORTLIST_K]]
    section = [
        f"## Round {round_num:02d} — Screening",
        "",
        "### Leaderboard (primary window)",
        "| Rank | Config | CAGR | Max DD | Trades | Feasible | Score |",
        "|------|--------|------|--------|--------|----------|-------|",
    ]
    for i, m in enumerate(ranked, 1):
        section.append(
            f"| {i} | {m.config_name} | {m.cagr:.2%} | {m.max_drawdown_pct:.2f}% | "
            f"{m.total_closed_trades} | {m.feasible} | {m.score:.4f} |"
        )
    section += ["", f"**Top {SHORTLIST_K} shortlist:** {', '.join(shortlist)}", ""]
    results_sections.extend(section)

    # Discover analogs once per round
    index_bars = load_index_daily_bars(db_path, end=REF_END)
    index_matches = find_index_analogs(
        index_bars,
        reference_start=REF_START,
        reference_end=REF_END,
        top_k=3,
        search_start=SEARCH_START,
    )
    vix = load_or_download_vix(ROOT, SEARCH_START, REF_END)
    vix_matches = find_vix_analogs(
        vix,
        reference_start=REF_START,
        reference_end=REF_END,
        top_k=3,
        search_start=SEARCH_START,
    )

    # Index analog backtests
    print(f"\nRound {round_num:02d} — index analog validation", flush=True)
    index_analog_data: dict[str, list[RunMetrics]] = {n: [] for n in shortlist}
    index_section = [
        f"## Round {round_num:02d} — Index analog consistency",
        "",
        "| Config | Analog # | Index window | Backtest window | CAGR | Max DD | Trades | Consistent? |",
        "|--------|----------|--------------|-----------------|------|--------|--------|-------------|",
    ]
    for name in shortlist:
        analog_cfg = write_experiment_yaml(
            next(s for s in specs if s.name == name),
            round_num=round_num,
            export_subdir=index_dir,
            db_path=db_path,
        )
        for m in index_matches:
            metrics = run_backtest(
                analog_cfg,
                start=m.backtest_start,
                end=m.backtest_end,
                phase="index-analog",
                round_num=round_num,
                config_name=name,
                db_path=db_path,
            )
            index_analog_data[name].append(metrics)

    index_verdict = assess_consistency(index_analog_data, shortlist)
    for name in shortlist:
        for i, (match, metrics) in enumerate(zip(index_matches, index_analog_data[name]), 1):
            index_section.append(
                f"| {name} | {i} | {match.analog_start}→{match.analog_end} | "
                f"{match.backtest_start}→{match.backtest_end} | {metrics.cagr:.2%} | "
                f"{metrics.max_drawdown_pct:.2f}% | {metrics.total_closed_trades} | "
                f"{'✓' if index_verdict[name] else '✗'} |"
            )
    index_section.append("")
    results_sections.extend(index_section)

    # VIX analog backtests
    print(f"\nRound {round_num:02d} — VIX analog validation", flush=True)
    vix_analog_data: dict[str, list[RunMetrics]] = {n: [] for n in shortlist}
    vix_section = [
        f"## Round {round_num:02d} — VIX analog consistency",
        "",
        "| Config | Analog # | VIX window | Backtest window | CAGR | Max DD | Trades | Consistent? |",
        "|--------|----------|------------|-----------------|------|--------|--------|-------------|",
    ]
    for name in shortlist:
        vix_cfg = write_experiment_yaml(
            next(s for s in specs if s.name == name),
            round_num=round_num,
            export_subdir=vix_dir,
            db_path=db_path,
        )
        for m in vix_matches:
            metrics = run_backtest(
                vix_cfg,
                start=m.backtest_start,
                end=m.backtest_end,
                phase="vix-analog",
                round_num=round_num,
                config_name=name,
                db_path=db_path,
            )
            vix_analog_data[name].append(metrics)

    vix_verdict = assess_consistency(vix_analog_data, shortlist)
    for name in shortlist:
        for i, (match, metrics) in enumerate(zip(vix_matches, vix_analog_data[name]), 1):
            vix_section.append(
                f"| {name} | {i} | {match.analog_start}→{match.analog_end} | "
                f"{match.backtest_start}→{match.backtest_end} | {metrics.cagr:.2%} | "
                f"{metrics.max_drawdown_pct:.2f}% | {metrics.total_closed_trades} | "
                f"{'✓' if vix_verdict[name] else '✗'} |"
            )
    vix_section.append("")
    results_sections.extend(vix_section)

    # Promote winners
    promoted: list[str] = []
    winner_section = [f"## Round {round_num:02d} — Winners promoted", ""]
    incumbent_metrics = [
        RunMetrics(
            config_name=inc["name"],
            cagr=inc["cagr"],
            max_drawdown_pct=inc["max_drawdown_pct"],
            total_closed_trades=inc["total_closed_trades"],
            win_rate=inc["win_rate"],
            run_dir=inc["run_dir"],
            feasible=inc["feasible"],
            score=inc["score"],
        )
        for inc in incumbents
    ]

    primary_by_name = {m.config_name: m for m in screening}
    for name in shortlist:
        if not (index_verdict.get(name) and vix_verdict.get(name)):
            continue
        primary = primary_by_name[name]
        if incumbent_metrics and not beats_incumbents(primary, incumbent_metrics):
            continue

        spec = next((s for s in specs if s.name == name), ExperimentSpec(name))
        src_yaml = ROOT / "configs" / "experiments" / f"round-{round_num:02d}" / f"{name}.yaml"
        winner_dir = ROOT / "configs" / "winners" / f"round-{round_num:02d}"
        winner_dir.mkdir(parents=True, exist_ok=True)
        dest_yaml = winner_dir / f"{name}.yaml"
        shutil.copy2(src_yaml, dest_yaml)

        overrides = spec.overrides if spec.overrides else load_overrides_from_yaml(src_yaml)
        write_winner_summary(
            round_num=round_num,
            config_name=name,
            overrides=overrides,
            primary=primary,
            index_rows=list(zip(index_matches, index_analog_data[name])),
            vix_rows=list(zip(vix_matches, vix_analog_data[name])),
            index_ok=True,
            vix_ok=True,
        )
        promoted.append(name)
        incumbents.append(
            {
                "round": round_num,
                "name": name,
                "cagr": primary.cagr,
                "max_drawdown_pct": primary.max_drawdown_pct,
                "total_closed_trades": primary.total_closed_trades,
                "win_rate": primary.win_rate,
                "run_dir": primary.run_dir,
                "feasible": primary.feasible,
                "score": primary.score,
                "config_name": name,
                "overrides": overrides,
                "yaml": str(dest_yaml),
            }
        )
        winner_section.append(f"- **{name}** → `{dest_yaml}`")

    if not promoted:
        winner_section.append("_No configs passed both analog tests (or beat incumbents)._")
    winner_section.append("")
    results_sections.extend(winner_section)

    new_winners_beating_incumbents = any(
        beats_incumbents(primary_by_name[n], incumbent_metrics)
        for n in promoted
    )
    return incumbents, new_winners_beating_incumbents or (len(promoted) > 0 and not incumbent_metrics), ranked[0].config_name


def build_final_recommendation(incumbents: list[dict]) -> list[str]:
    lines = ["## Final recommendation", ""]
    if not incumbents:
        lines.append("_No validated winners across all rounds._")
        return lines

    base = load_config_relaxed(ROOT / "config.yaml")
    base_snap = apply_darvas_algo_overrides(base, {})

    lines.append(
        "| Config | Primary CAGR | Primary DD | Min analog CAGR | Max analog DD | Key deltas vs config.yaml |"
    )
    lines.append("|--------|--------------|------------|-----------------|---------------|---------------------------|")
    for inc in incumbents:
        deltas = []
        for k, v in sorted((inc.get("overrides") or {}).items()):
            short = k.split(".")[-1]
            deltas.append(f"{short}={v}")
        delta_str = ", ".join(deltas) if deltas else "(baseline)"
        lines.append(
            f"| {inc['name']} | {inc['cagr']:.2%} | {inc['max_drawdown_pct']:.2f}% | "
            f"n/a | n/a | {delta_str} |"
        )
    lines.append("")
    lines.append(
        "_Human approval required before promoting any winner to production `config.yaml`._"
    )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--sanity-only", action="store_true", help="Run baseline sanity check only")
    parser.add_argument("--db", default=None)
    parser.add_argument(
        "--resume-from-round",
        type=int,
        default=0,
        help="Skip earlier rounds; rebuild their summary from regime-search-log.jsonl",
    )
    args = parser.parse_args()

    cfg = load_config(ROOT / "config.yaml")
    db_path = Path(args.db) if args.db else ROOT / cfg.backtest.data_db_path

    if args.sanity_only:
        print("Sanity check: config.yaml on primary window...", flush=True)
        run_backtest(
            ROOT / "config.yaml",
            start=PRIMARY_START,
            end=PRIMARY_END,
            phase="sanity",
            round_num=0,
            config_name="baseline",
            db_path=db_path if args.db else None,
        )
        return 0

    verify_prerequisites(db_path)

    results_sections: list[str] = [
        "# Regime-analog parameter search",
        "",
        f"**Primary window:** {PRIMARY_START} → {PRIMARY_END}",
        f"**Reference analog window:** {REF_START} → {REF_END}",
        "",
    ]

    incumbents: list[dict] = []
    top_screening = "baseline"
    existing_log = load_log()
    start_round = max(1, args.resume_from_round)

    if start_round > 1:
        if not existing_log:
            print(f"No log at {LOG_PATH}; cannot resume from round {start_round}", file=sys.stderr)
            return 1
        for r in range(1, start_round):
            results_sections.extend(summarize_completed_round(existing_log, r))
        top_from_prior = [
            r for r in existing_log if r.get("round") == start_round - 1 and r.get("phase") == "screening"
        ]
        top_from_prior.sort(
            key=lambda r: (-r.get("score", -1), r.get("max_drawdown_pct", 99), -r.get("total_closed_trades", 0))
        )
        top_screening = top_from_prior[0]["config"] if top_from_prior else "baseline"
        src = ROOT / "configs" / "experiments" / f"round-{start_round - 1:02d}" / f"{top_screening}.yaml"
        base_overrides = load_overrides_from_yaml(src) if src.is_file() else {}
        specs = generate_mutations(top_screening, base_overrides, start_round)
        results_sections.append(
            f"## Resuming at round {start_round:02d}\n\n"
            f"Mutation base: **{top_screening}** (top screening config from round {start_round - 1:02d})\n\n"
            + "\n".join(f"- `{s.name}`" for s in specs)
            + "\n"
        )
        print(f"Resuming from round {start_round:02d} (log has {len(existing_log)} entries)", flush=True)
    else:
        specs = ROUND1_SPECS

    for round_num in range(start_round, MAX_ROUNDS + 1):
        incumbents, had_new_winners, top_screening = run_round(
            round_num, specs, db_path=db_path, incumbents=incumbents, results_sections=results_sections
        )

        if round_num >= MAX_ROUNDS:
            break

        if round_num > 1 and not had_new_winners:
            results_sections.append(
                f"## Round {round_num + 1:02d} — Skipped\n\n"
                "Stop: no new winner beat incumbents and passed both analog tests.\n"
            )
            break

        # Mutation plan for next round — seed from best incumbent or top screening config
        if incumbents:
            best = max(incumbents, key=lambda x: x["cagr"])
            base_name = best["name"]
            base_overrides = best.get("overrides") or {}
        else:
            base_name = top_screening
            src = ROOT / "configs" / "experiments" / f"round-{round_num:02d}" / f"{base_name}.yaml"
            base_overrides = load_overrides_from_yaml(src) if src.is_file() else {}

        specs = generate_mutations(base_name, base_overrides, round_num + 1)
        results_sections.append(
            f"## Next round ({round_num + 1:02d}) mutation plan\n\n"
            f"Base: **{base_name}**\n\n"
            + "\n".join(f"- `{s.name}`" for s in specs)
            + "\n"
        )

    # Cumulative winners
    results_sections.append("## Cumulative winners\n")
    if incumbents:
        results_sections.append("| Round | Config | CAGR | Max DD | YAML |")
        results_sections.append("|-------|--------|------|--------|------|")
        for inc in incumbents:
            results_sections.append(
                f"| {inc['round']} | {inc['name']} | {inc['cagr']:.2%} | "
                f"{inc['max_drawdown_pct']:.2f}% | `{inc.get('yaml', '')}` |"
            )
    else:
        results_sections.append("_None promoted._")
    results_sections.append("")

    results_sections.extend(build_final_recommendation(incumbents))
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text("\n".join(results_sections) + "\n", encoding="utf-8")
    print(f"\nWrote {RESULTS_PATH}", flush=True)

    if not args.no_email:
        attachments = [RESULTS_PATH, LOG_PATH]
        winners_root = ROOT / "configs" / "winners"
        if winners_root.is_dir():
            attachments.append(winners_root)
        send_completion_email(RESULTS_PATH.read_text(encoding="utf-8"), attachments)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
