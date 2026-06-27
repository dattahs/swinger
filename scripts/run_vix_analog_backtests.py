#!/usr/bin/env python3
"""Find India VIX analog periods and backtest the subsequent year with optimal config."""

from __future__ import annotations

import argparse
import json
import smtplib
import sys
import tempfile
import zipfile
from datetime import date
from email.message import EmailMessage
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.vix_curve_match import VixWindowMatch, find_vix_analogs
from src.backtest.backtester import Backtester
from src.config import load_config
from src.data.vix_data import load_or_download_vix
from src.notify.backtest_email import (
    load_email_settings_from_env,
)


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
    return date(y, m, day)


def _default_reference_window(today: date | None = None) -> tuple[date, date]:
    """Jun (Y-1) through May Y — e.g. Jun 2025 – May 2026 when today is Jun 2026."""
    today = today or date.today()
    ref_end = date(today.year, today.month, 1) - __import__("datetime").timedelta(days=1)
    if today.month >= 6:
        ref_start = date(today.year - 1, 6, 1)
    else:
        ref_start = date(today.year - 2, 6, 1)
        ref_end = date(today.year - 1, 5, 31)
    return ref_start, ref_end


def _format_match_table(
    matches: list[VixWindowMatch],
    reference: tuple[date, date],
    *,
    session_count: int,
) -> list[str]:
    ref_start, ref_end = reference
    lines = [
        "India VIX analog study",
        f"Reference VIX window: {ref_start} -> {ref_end} ({session_count} trading sessions, shape-matched)",
        "",
        "Matching method: z-scored Pearson on close/returns/range/body + DTW on closes.",
        "",
        "Top analog windows (similar VIX pattern) and subsequent-year backtest ranges:",
    ]
    for m in matches:
        lines.append(
            f"  #{m.rank}  VIX analog {m.analog_start} -> {m.analog_end}  "
            f"score={m.score:.4f}  corr={m.corr_close:.3f}  dtw_sim={m.dtw_similarity:.3f}"
        )
        lines.append(f"       Backtest window: {m.backtest_start} -> {m.backtest_end}")
    return lines


def _send_combined_email(
    *,
    settings,
    subject: str,
    plain: str,
    run_dirs: list[Path],
) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(plain)
    msg.add_alternative(f"<html><body><pre>{escape(plain)}</pre></body></html>", subtype="html")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        zip_path = Path(tmp.name)
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for run_dir in run_dirs:
            for path in sorted(run_dir.glob("*")):
                if path.is_file():
                    zf.write(path, arcname=f"{run_dir.name}/{path.name}")

    msg.add_attachment(
        zip_path.read_bytes(),
        maintype="application",
        subtype="zip",
        filename="vix_analog_backtests.zip",
    )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=120) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    finally:
        zip_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--ref-start", help="Reference window start YYYY-MM-DD (default: Jun prior year)")
    parser.add_argument("--ref-end", help="Reference window end YYYY-MM-DD (default: May current year)")
    parser.add_argument("--vix-download-start", default="2017-01-01")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Find analogs only; skip backtests")
    args = parser.parse_args()

    ref_start, ref_end = _default_reference_window()
    if args.ref_start:
        ref_start = date.fromisoformat(args.ref_start)
    if args.ref_end:
        ref_end = date.fromisoformat(args.ref_end)

    print(f"Loading India VIX data ({args.vix_download_start} -> {ref_end})...", flush=True)
    vix = load_or_download_vix(
        ROOT,
        date.fromisoformat(args.vix_download_start),
        ref_end,
    )
    print(f"  {len(vix)} VIX sessions loaded", flush=True)

    ref_mask = (vix["date"] >= ref_start) & (vix["date"] <= ref_end)
    ref_sessions = len(vix.loc[ref_mask])

    matches = find_vix_analogs(
        vix,
        reference_start=ref_start,
        reference_end=ref_end,
        top_k=args.top_k,
        search_start=date.fromisoformat(args.vix_download_start),
    )
    if not matches:
        print("No VIX analog windows found.", file=sys.stderr)
        return 1

    report_lines = _format_match_table(matches, (ref_start, ref_end), session_count=ref_sessions)
    for line in report_lines:
        print(line, flush=True)

    if args.dry_run:
        return 0

    config_path = ROOT / args.config
    cfg = load_config(config_path)
    cfg.backtest.progress_log.enabled = False
    cfg.backtest.debug_log.enabled = False
    cfg.backtest.send_email_on_complete = False
    cfg.backtest.timestamped_runs = True

    run_dirs: list[Path] = []
    summary_rows: list[dict] = []

    for m in matches:
        label = f"vix_analog{m.rank}_{m.backtest_start}_{m.backtest_end}"
        print(f"\nRunning backtest {label}...", flush=True)
        bt = Backtester(cfg, repo_root=ROOT)
        out_dir = bt.run(start=m.backtest_start, end=m.backtest_end)
        run_dirs.append(Path(out_dir))
        summary = json.loads((Path(out_dir) / "summary_report.json").read_text(encoding="utf-8"))
        summary_rows.append({"match": m, "summary": summary, "run_dir": str(out_dir)})

    report_lines.extend(["", "Backtest results (optimal config.yaml):"])
    for row in summary_rows:
        m: VixWindowMatch = row["match"]
        s = row["summary"]
        report_lines.extend(
            [
                "",
                f"Analog #{m.rank}: VIX {m.analog_start} -> {m.analog_end} "
                f"(score {m.score:.4f}, corr {m.corr_close:.3f})",
                f"  Backtest {s.get('start_date')} -> {s.get('end_date')}",
                f"  CAGR: {100 * float(s.get('cagr', 0)):.2f}%  "
                f"Max DD: {s.get('max_drawdown_pct')}%  "
                f"Trades: {s.get('total_closed_trades')}  "
                f"Win rate: {100 * float(s.get('win_rate', 0)):.1f}%  "
                f"Final equity: INR {float(s.get('final_equity_inr', 0)):,.0f}",
                f"  Output: {row['run_dir']}",
            ]
        )

    plain_report = "\n".join(report_lines)
    out_report = ROOT / "backtest_outputs" / f"vix_analog_study_{ref_start}_{ref_end}.txt"
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(plain_report, encoding="utf-8")
    print(f"\nReport written to {out_report}", flush=True)

    if args.no_email:
        print("Skipped email (--no-email).", flush=True)
        return 0

    settings = load_email_settings_from_env()
    subject = (
        f"Swinger VIX Analog Backtests — ref {ref_start} to {ref_end} "
        f"({len(matches)} subsequent-year runs)"
    )
    _send_combined_email(settings=settings, subject=subject, plain=plain_report, run_dirs=run_dirs)
    print(f"Emailed results to {', '.join(settings.to_addrs)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
