"""Email backtest run results with insights in the body and raw artifacts as a zip."""

from __future__ import annotations

import json
import os
import smtplib
import tempfile
import zipfile
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from pathlib import Path

import pandas as pd

from scripts.analyze_backtest import analyze
from scripts.monthly_analysis import build_monthly_table, load_run_frames
from src.broker.env import load_dotenv

REQUIRED_SUMMARY = "summary_report.json"
ARTIFACT_GLOB = (
    "*.csv",
    "*.json",
    "*.log",
    "*.txt",
)


@dataclass(frozen=True)
class BacktestEmailSettings:
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_addr: str
    to_addrs: tuple[str, ...]
    use_tls: bool = True


class BacktestRunNotFoundError(FileNotFoundError):
    """Raised when a backtest run directory cannot be resolved."""


class BacktestRunIncompleteError(ValueError):
    """Raised when expected backtest artifacts are missing."""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_email_settings_from_env(*, dotenv_path: str | Path | None = None) -> BacktestEmailSettings:
    """Load SMTP settings from environment (optionally primed from .env)."""
    root = _project_root()
    env_path = Path(dotenv_path) if dotenv_path else root / ".env"
    load_dotenv(env_path)
    return resolve_email_settings()


def resolve_email_settings(
    *,
    smtp_host: str | None = None,
    smtp_port: int | None = None,
    smtp_user: str | None = None,
    smtp_password: str | None = None,
    from_addr: str | None = None,
    to_addrs: tuple[str, ...] | list[str] | None = None,
    use_tls: bool | None = None,
) -> BacktestEmailSettings:
    """Merge CLI overrides with SWINGER_* environment variables."""
    load_dotenv(_project_root() / ".env")
    host = (smtp_host or os.environ.get("SWINGER_SMTP_HOST", "")).strip()
    port_raw = str(smtp_port if smtp_port is not None else os.environ.get("SWINGER_SMTP_PORT", "587")).strip()
    user = (smtp_user or os.environ.get("SWINGER_SMTP_USER", "")).strip()
    password = (smtp_password or os.environ.get("SWINGER_SMTP_PASSWORD", "")).strip()
    sender = (from_addr or os.environ.get("SWINGER_EMAIL_FROM", user)).strip()

    if to_addrs:
        recipients = tuple(addr.strip() for addr in to_addrs if addr and str(addr).strip())
    else:
        to_raw = os.environ.get("SWINGER_EMAIL_TO", "").strip()
        recipients = tuple(addr.strip() for addr in to_raw.split(",") if addr.strip())

    if use_tls is None:
        use_tls = os.environ.get("SWINGER_SMTP_USE_TLS", "true").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    missing = [
        name
        for name, value in (
            ("SWINGER_SMTP_HOST / --smtp-host", host),
            ("SWINGER_SMTP_USER / --smtp-user", user),
            ("SWINGER_SMTP_PASSWORD / --smtp-password", password),
            ("SWINGER_EMAIL_TO / --to", recipients),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"Missing required email settings: {', '.join(missing)}")

    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"Invalid SMTP port: {port_raw}") from exc

    return BacktestEmailSettings(
        smtp_host=host,
        smtp_port=port,
        smtp_user=user,
        smtp_password=password,
        from_addr=sender or user,
        to_addrs=recipients,
        use_tls=use_tls,
    )


def resolve_run_directory(
    run_dir: str | Path | None = None,
    *,
    export_directory: str | Path | None = None,
    latest: bool = False,
) -> Path:
    """Resolve a backtest output directory by explicit path or newest run_* folder."""
    if run_dir is not None and latest:
        raise ValueError("Pass either run_dir or latest=True, not both")

    if run_dir is not None:
        path = Path(run_dir).resolve()
        if not path.is_dir():
            raise BacktestRunNotFoundError(f"Run directory not found: {path}")
        return path

    base = Path(export_directory or _project_root() / "backtest_outputs").resolve()
    if not base.is_dir():
        raise BacktestRunNotFoundError(f"Export directory not found: {base}")

    runs = sorted(base.glob("run_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for candidate in runs:
        if (candidate / REQUIRED_SUMMARY).is_file():
            return candidate
    if runs:
        raise BacktestRunIncompleteError(
            f"Found {len(runs)} run_* folder(s) under {base}, but none contain {REQUIRED_SUMMARY}"
        )
    raise BacktestRunNotFoundError(f"No run_* directories under {base}")


def validate_run_directory(run_dir: Path) -> None:
    summary = run_dir / REQUIRED_SUMMARY
    if not summary.is_file():
        raise BacktestRunIncompleteError(
            f"{run_dir} is missing {REQUIRED_SUMMARY} — not a completed backtest run"
        )


def _format_inr(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"INR {value:,.0f}"


def _format_pct(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{100 * value:.{digits}f}%"


def _decision_log_insights(run_dir: Path) -> list[str]:
    path = run_dir / "decision_log.csv"
    if not path.is_file():
        return ["Decision log: not exported"]

    dec = pd.read_csv(path, parse_dates=["date"])
    lines = [
        "",
        "Decision funnel",
        f"  Trading sessions: {dec['date'].nunique():,}",
    ]
    for state, count in dec.groupby("box_state").size().sort_values(ascending=False).items():
        lines.append(f"  {state}: {count:,} symbol-days")

    bo = dec[dec["box_state"] == "BREAKOUT"]
    if not bo.empty:
        lines.append(f"  Unique BREAKOUT symbols: {bo['symbol'].nunique():,}")
        if "filter_fail_reason" in bo.columns:
            fails = bo["filter_fail_reason"].value_counts(dropna=False).head(5)
            if not fails.empty:
                lines.append("  Top breakout filter failures:")
                for reason, n in fails.items():
                    label = reason if pd.notna(reason) and str(reason).strip() else "(none)"
                    lines.append(f"    {label}: {n:,}")

    selected = (
        dec[dec["selected"].astype(int) == 1]
        if "selected" in dec.columns
        else pd.DataFrame()
    )
    if not selected.empty:
        lines.append(f"  Selected entries (decision log): {len(selected):,}")

    skip = (
        dec[dec["skip_reason"].notna() & (dec["skip_reason"].astype(str).str.len() > 0)]
        if "skip_reason" in dec.columns
        else pd.DataFrame()
    )
    if not skip.empty:
        lines.append("  Top skip reasons:")
        for reason, n in skip["skip_reason"].value_counts().head(5).items():
            lines.append(f"    {reason}: {n:,}")

    return lines


def _closed_trade_insights(analysis: dict) -> list[str]:
    if analysis.get("error"):
        return ["", "Trade analysis", f"  {analysis['error']}"]

    lines = [
        "",
        "Trade analysis",
        f"  Closed trades: {analysis['total_closed']}",
        f"  Win rate: {analysis['win_rate_pct']}%",
        f"  Total P&L: {_format_inr(analysis['total_pnl_inr'])}",
        f"  Avg P&L / trade: {_format_inr(analysis['avg_pnl_per_trade_inr'])}",
        f"  Avg win: {_format_inr(analysis['avg_profit_per_win_inr'])}",
        f"  Avg loss: {_format_inr(analysis['avg_loss_per_loss_inr'])}",
    ]

    winner = analysis.get("biggest_winner") or {}
    loser = analysis.get("biggest_loser") or {}
    if winner:
        lines.append(
            "  Biggest winner: "
            f"{winner.get('symbol')} {_format_inr(winner.get('pnl_inr'))} "
            f"({winner.get('entry_date')} -> {winner.get('exit_date')}, {winner.get('exit_reason', '')})"
        )
    if loser:
        lines.append(
            "  Biggest loser: "
            f"{loser.get('symbol')} {_format_inr(loser.get('pnl_inr'))} "
            f"({loser.get('entry_date')} -> {loser.get('exit_date')}, {loser.get('exit_reason', '')})"
        )

    monthly = analysis.get("monthly") or []
    if monthly:
        lines.extend(["", "Monthly P&L (by exit month)"])
        for row in monthly[-6:]:
            lines.append(
                f"  {row['month']}: "
                f"{int(row['trades_closed'])} trades, "
                f"P&L {_format_inr(row['total_pnl'])}, "
                f"W/L {int(row['wins'])}/{int(row['losses'])}"
            )

    return lines


def _exit_reason_breakdown(run_dir: Path) -> list[str]:
    path = run_dir / "closed_trades.csv"
    if not path.is_file():
        return []
    closed = pd.read_csv(path)
    if closed.empty or "exit_reason" not in closed.columns:
        return []
    lines = ["", "Exit reasons"]
    for reason, n in closed["exit_reason"].value_counts().items():
        lines.append(f"  {reason}: {n}")
    return lines


def _monthly_equity_insights(run_dir: Path) -> list[str]:
    try:
        eq, closed, open_buys = load_run_frames(run_dir)
    except FileNotFoundError:
        return []

    table = build_monthly_table(eq, closed, open_buys)
    if table.empty:
        return []

    lines = ["", "Monthly equity snapshot (last 6 months)"]
    for period, row in table.tail(6).iterrows():
        lines.append(
            f"  {period}: equity {_format_inr(float(row['equity']))}, "
            f"max DD {float(row['max_dd']):.2f}%, "
            f"new trades {int(row['new_trades_taken'])}, "
            f"closed W/L {int(row['trades_with_gain'])}/{int(row['trades_with_loss'])}"
        )
    return lines


def build_insights(
    run_dir: str | Path,
    *,
    experiment: dict | None = None,
) -> tuple[str, str]:
    """Build plain-text and HTML insight bodies from a completed backtest run."""
    run_dir = Path(run_dir).resolve()
    validate_run_directory(run_dir)

    summary = json.loads((run_dir / REQUIRED_SUMMARY).read_text(encoding="utf-8"))
    manifest_path = run_dir / "run_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.is_file()
        else {}
    )

    try:
        analysis = analyze(run_dir)
    except FileNotFoundError:
        analysis = {"error": "closed_trades.csv not found", "summary": summary}

    run_name = run_dir.name
    invoked = summary.get("invoked_at") or manifest.get("invoked_at") or "unknown"
    start = summary.get("start_date", "?")
    end = summary.get("end_date", "?")

    lines: list[str] = []
    if experiment:
        lines.extend(
            [
                f"Optimization experiment #{experiment.get('iteration', '?')}: "
                f"{experiment.get('name', 'unnamed')}",
                f"Hypothesis: {experiment.get('hypothesis', '')}",
                f"Cadence: {experiment.get('cadence', 'daily')}",
            ]
        )
        params = experiment.get("params") or {}
        if params:
            lines.append("Parameter overrides:")
            for key, value in sorted(params.items()):
                lines.append(f"  {key}: {value}")
        if "feasible" in experiment:
            lines.append(
                f"Feasible (DD <= 10%): {experiment['feasible']}  |  "
                f"Score: {experiment.get('score', 'n/a')}"
            )
        lines.append("")

    lines.extend(
        [
            f"Swinger backtest results — {run_name}",
            f"Invoked: {invoked}",
            f"Period: {start} -> {end}",
            "",
            "Performance",
            f"  Initial capital: {_format_inr(summary.get('initial_capital_inr'))}",
            f"  Final equity: {_format_inr(summary.get('final_equity_inr'))}",
            f"  CAGR: {_format_pct(summary.get('cagr'))}",
            f"  Max drawdown: {summary.get('max_drawdown_pct', 'n/a')}%",
            f"  Closed trades: {summary.get('total_closed_trades', 'n/a')}",
            f"  Win rate: {_format_pct(summary.get('win_rate'))}",
        ]
    )
    lines.extend(_closed_trade_insights(analysis))
    lines.extend(_exit_reason_breakdown(run_dir))
    lines.extend(_monthly_equity_insights(run_dir))
    lines.extend(_decision_log_insights(run_dir))
    lines.extend(
        [
            "",
            "Raw CSV/JSON/log files for this run are attached as a zip archive.",
        ]
    )

    plain = "\n".join(lines)
    html_parts = ["<html><body><pre style='font-family: Consolas, monospace;'>"]
    html_parts.append(escape(plain))
    html_parts.append("</pre></body></html>")
    return plain, "".join(html_parts)


def _artifact_files(run_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ARTIFACT_GLOB:
        files.extend(run_dir.glob(pattern))
    return sorted({path.resolve() for path in files if path.is_file()})


def create_run_zip(run_dir: str | Path, dest: str | Path | None = None) -> Path:
    """Zip all flat run artifacts; returns path to the zip file."""
    run_dir = Path(run_dir).resolve()
    validate_run_directory(run_dir)

    files = _artifact_files(run_dir)
    if not files:
        raise BacktestRunIncompleteError(f"No artifact files to zip in {run_dir}")

    if dest is None:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"{run_dir.name}_",
            suffix=".zip",
            delete=False,
        )
        tmp.close()
        zip_path = Path(tmp.name)
    else:
        zip_path = Path(dest).resolve()
        zip_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)

    return zip_path


def send_backtest_results_email(
    run_dir: str | Path,
    settings: BacktestEmailSettings,
    *,
    dry_run: bool = False,
    keep_zip: bool = False,
    experiment: dict | None = None,
) -> Path | None:
    """Send backtest insights email with zipped run artifacts attached."""
    run_dir = Path(run_dir).resolve()
    validate_run_directory(run_dir)

    plain, html = build_insights(run_dir, experiment=experiment)
    zip_path = create_run_zip(run_dir)
    summary = json.loads((run_dir / REQUIRED_SUMMARY).read_text(encoding="utf-8"))
    cagr_pct = _format_pct(summary.get("cagr"))
    max_dd = summary.get("max_drawdown_pct", "n/a")
    if experiment:
        subject = (
            f"Swinger Optimization #{experiment.get('iteration', '?')} "
            f"{experiment.get('name', run_dir.name)} — "
            f"CAGR {cagr_pct} DD {max_dd}%"
        )
    else:
        subject = (
            f"Swinger Backtest — {run_dir.name} "
            f"({summary.get('start_date')} -> {summary.get('end_date')})"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")
    msg.add_attachment(
        zip_path.read_bytes(),
        maintype="application",
        subtype="zip",
        filename=f"{run_dir.name}_artifacts.zip",
    )

    if dry_run:
        if not keep_zip:
            zip_path.unlink(missing_ok=True)
        return None

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    finally:
        if not keep_zip:
            zip_path.unlink(missing_ok=True)

    return zip_path if keep_zip else None
