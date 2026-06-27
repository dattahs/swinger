"""Email validation suite markdown report."""

from __future__ import annotations

import smtplib
import zipfile
from email.message import EmailMessage
from html import escape
from pathlib import Path

from src.notify.backtest_email import BacktestEmailSettings, resolve_email_settings


def send_validation_report_email(
    out_dir: Path,
    report_md: str,
    settings: BacktestEmailSettings,
    *,
    dry_run: bool = False,
) -> None:
    """Send validation report as email body with zipped artifacts."""
    out_dir = out_dir.resolve()
    md_path = out_dir / "validation_report.md"
    json_path = out_dir / "validation_results.json"

    zip_path = out_dir / f"{out_dir.name}_artifacts.zip"
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in (md_path, json_path):
            if path.is_file():
                zf.write(path, arcname=path.name)

    subject = f"Swinger Validation Suite — {out_dir.name}"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(report_md)
    msg.add_alternative(
        f"<html><body><pre style='font-family: Consolas, monospace;'>{escape(report_md)}</pre></body></html>",
        subtype="html",
    )
    if zip_path.is_file():
        msg.add_attachment(
            zip_path.read_bytes(),
            maintype="application",
            subtype="zip",
            filename=zip_path.name,
        )

    if dry_run:
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=120) as smtp:
        if settings.use_tls:
            smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
