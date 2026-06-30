#!/usr/bin/env python3
"""March-to-March yearwise backtests for top 2Y configs; emails each run + summary."""

from __future__ import annotations

import argparse
import calendar
import json
import os
import smtplib
import sys
import tempfile
import time
import zipfile
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester
from src.broker.env import load_dotenv
from src.config import apply_darvas_algo_overrides, load_config_relaxed
from src.notify.backtest_email import resolve_email_settings, send_backtest_results_email
from src.repository.sqlite import SqliteDataLake

LOG_PATH = ROOT / "src" / "agentic-loop" / "march-yearwise-log.jsonl"
DEFAULT_EMAIL_TO: tuple[str, ...] = ()

# Best two configs on 2024-06-01 → 2026-06-19 (all local backtests to date).
CHAMPION_CONFIGS: list[dict[str, Any]] = [
    {
        "id": "box_dur4_sma80",
        "label": "Box min_duration=4 + SMA80 reset 4%",
        "base_config": "configs/opt-iter09-sma80.yaml",
        "overrides": {
            "breakout_reset_above_top_pct": 4.0,
            "adaptive_sma_period": 80,
            "darvas_reversal_days": 3,
            "min_box_duration_days": 4,
            "min_box_height_pct": 3.0,
            "dynamic_atr_target_enabled": False,
        },
        "two_y_note": "22.60% CAGR, 1.70% max DD (box-shape tuning champion)",
    },
    {
        "id": "static_target_1.2x",
        "label": "Static target 1.2× box height",
        "base_config": "configs/baseline-next-best.yaml",
        "overrides": {
            "target_box_height_multiplier": 1.2,
            "dynamic_atr_target_enabled": False,
        },
        "two_y_note": "21.89% CAGR, 2.62% max DD (best static target multiplier)",
    },
]


def _prime_email_env() -> None:
    for path in (
        Path(os.environ["SWINGER_ENV_FILE"]) if os.environ.get("SWINGER_ENV_FILE") else None,
        ROOT / ".env",
        Path("/opt/swinger/shared/.env"),
    ):
        if path and path.is_file():
            load_dotenv(path, override=True)
            return


def _email_to() -> tuple[str, ...]:
    raw = os.environ.get("SWINGER_EMAIL_TO", "").strip()
    if raw:
        return tuple(a.strip() for a in raw.split(",") if a.strip())
    return DEFAULT_EMAIL_TO


def _feb_end(year: int) -> date:
    last_day = 29 if calendar.isleap(year) else 28
    return date(year, 2, last_day)


def march_year_windows(
    data_lake: SqliteDataLake,
    *,
    first_year: int = 2018,
    min_sessions: int = 120,
) -> list[tuple[str, date, date]]:
    """Mar Y → last session in Feb Y+1, for every year with enough data."""
    probe_end = date(2030, 12, 31)
    all_days = data_lake.get_trading_days(date(first_year, 1, 1), probe_end)
    if not all_days:
        return []
    last_data = all_days[-1]
    windows: list[tuple[str, date, date]] = []
    y = first_year
    while True:
        period_start = date(y, 3, 1)
        period_end = min(_feb_end(y + 1), last_data)
        if period_start > last_data:
            break
        days = [d for d in all_days if period_start <= d <= period_end]
        if len(days) >= min_sessions:
            label = f"Mar{y}-Feb{y + 1}"
            windows.append((label, days[0], days[-1]))
        y += 1
        if date(y, 3, 1) > last_data:
            break
    return windows


def append_log(record: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


def apply_shared_paths(cfg, shared: Any) -> Any:
    """Use VPS/shared data and output paths when running remotely."""
    cfg.backtest.data_db_path = shared.backtest.data_db_path
    cfg.backtest.export_directory = shared.backtest.export_directory
    if shared.backtest.initial_capital_inr:
        cfg.backtest.initial_capital_inr = shared.backtest.initial_capital_inr
    return cfg


def run_one(
    cfg_path: Path,
    overrides: dict[str, Any],
    *,
    config_id: str,
    config_label: str,
    period_label: str,
    start: date,
    end: date,
    send_email: bool,
    shared_cfg: Any | None = None,
) -> dict:
    cfg = load_config_relaxed(cfg_path)
    if shared_cfg is not None:
        cfg = apply_shared_paths(cfg, shared_cfg)
    cfg = apply_darvas_algo_overrides(cfg, overrides)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False

    t0 = time.monotonic()
    bt = Backtester(cfg, repo_root=ROOT)
    run_dir = bt.run(start=start, end=end)
    elapsed = time.monotonic() - t0

    summary = json.loads((run_dir / "summary_report.json").read_text(encoding="utf-8"))
    record = {
        "config_id": config_id,
        "config_label": config_label,
        "period_label": period_label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "params": overrides,
        "run_dir": str(run_dir),
        "cagr": float(summary["cagr"]),
        "max_drawdown_pct": float(summary["max_drawdown_pct"]),
        "win_rate": summary.get("win_rate"),
        "total_closed_trades": summary.get("total_closed_trades"),
        "final_equity_inr": summary.get("final_equity_inr"),
        "elapsed_sec": round(elapsed, 1),
    }
    append_log(record)

    if send_email:
        _prime_email_env()
        settings = resolve_email_settings(to_addrs=_email_to())
        send_backtest_results_email(
            run_dir,
            settings,
            experiment={
                "name": f"{config_id}_{period_label}",
                "hypothesis": f"{config_label} — {period_label} ({start} → {end})",
                **record,
            },
        )
    return record


def _send_summary_email(records: list[dict], windows: list[tuple[str, date, date]]) -> None:
    _prime_email_env()
    settings = resolve_email_settings(to_addrs=_email_to())

    lines = [
        "Swinger March-to-March yearwise backtests",
        f"Configs: {', '.join(c['id'] for c in CHAMPION_CONFIGS)}",
        f"Periods: {len(windows)} fiscal years",
        "",
    ]
    for cfg in CHAMPION_CONFIGS:
        lines.append(f"=== {cfg['label']} ({cfg['id']}) ===")
        lines.append(f"  2Y benchmark: {cfg['two_y_note']}")
        subset = [r for r in records if r["config_id"] == cfg["id"]]
        for r in subset:
            lines.append(
                f"  {r['period_label']:16}  CAGR {100 * r['cagr']:6.2f}%  "
                f"DD {r['max_drawdown_pct']:5.2f}%  trades {r['total_closed_trades']}"
            )
        if subset:
            avg_cagr = sum(r["cagr"] for r in subset) / len(subset)
            lines.append(f"  Mean CAGR across years: {100 * avg_cagr:.2f}%")
        lines.append("")

    plain = "\n".join(lines)
    msg = EmailMessage()
    msg["Subject"] = f"Swinger March yearwise — {len(records)} runs, {len(CHAMPION_CONFIGS)} configs"
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(plain)
    msg.add_alternative(f"<html><body><pre>{escape(plain)}</pre></body></html>", subtype="html")

    run_dirs = [Path(r["run_dir"]) for r in records if Path(r["run_dir"]).is_dir()]
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)
    try:
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for run_dir in run_dirs:
                for path in sorted(run_dir.glob("*")):
                    if path.is_file() and path.name in {
                        "summary_report.json",
                        "closed_trades.csv",
                        "equity_curve.csv",
                    }:
                        zf.write(path, arcname=f"{run_dir.name}/{path.name}")
        msg.add_attachment(
            zip_path.read_bytes(),
            maintype="application",
            subtype="zip",
            filename="march_yearwise_summaries.zip",
        )
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=120) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    finally:
        zip_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Override data config path for DB probe")
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="List windows only")
    args = parser.parse_args()

    probe_cfg = args.config or ROOT / "configs/baseline-next-best.yaml"
    shared_cfg = load_config_relaxed(probe_cfg) if args.config else None
    cfg = shared_cfg or load_config_relaxed(probe_cfg)
    db_path = ROOT / cfg.backtest.data_db_path
    if not db_path.is_file():
        db_path = Path(cfg.backtest.data_db_path)
    data_lake = SqliteDataLake(db_path)
    windows = march_year_windows(data_lake)
    if not windows:
        print("No March-year windows found.", file=sys.stderr)
        return 1

    print("Champion configs (best on last 2Y):")
    for c in CHAMPION_CONFIGS:
        print(f"  - {c['id']}: {c['label']} — {c['two_y_note']}")
    print(f"\nMarch-to-March windows ({len(windows)}):")
    for label, start, end in windows:
        print(f"  {label}: {start} → {end}")

    if args.dry_run:
        return 0

    records: list[dict] = []
    send_email = not args.no_email
    total = len(CHAMPION_CONFIGS) * len(windows)
    n = 0
    for champ in CHAMPION_CONFIGS:
        base = ROOT / champ["base_config"]
        for period_label, start, end in windows:
            n += 1
            print(f"\n[{n}/{total}] {champ['id']} {period_label}...", flush=True)
            rec = run_one(
                base,
                champ["overrides"],
                config_id=champ["id"],
                config_label=champ["label"],
                period_label=period_label,
                start=start,
                end=end,
                send_email=send_email,
                shared_cfg=shared_cfg,
            )
            records.append(rec)
            print(
                f"  CAGR={100 * rec['cagr']:.2f}% DD={rec['max_drawdown_pct']:.2f}% "
                f"trades={rec['total_closed_trades']}",
                flush=True,
            )

    if send_email and records:
        _send_summary_email(records, windows)
        print(f"\nSummary email sent to {', '.join(_email_to())}", flush=True)

    print(f"\nDone. {len(records)} runs logged to {LOG_PATH}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
