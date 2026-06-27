"""Email + Telegram alerts when live Upstox GTT orders change."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage

from src.models import ActionType, PlannedGTTAction
from src.notify.backtest_email import BacktestEmailSettings, resolve_email_settings
from src.notify.telegram import TelegramSettings, resolve_telegram_settings, send_telegram_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GTTAlertEvent:
    verb: str  # placed | updated | cancelled
    action: PlannedGTTAction
    session_date: date
    gtt_order_id: str | None = None
    replaced_gtt_id: str | None = None


def _format_inr(value: float | int | None) -> str:
    if value is None:
        return "—"
    return f"₹{float(value):,.2f}"


def format_gtt_alert(event: GTTAlertEvent) -> tuple[str, str]:
    """Return (subject, plain body) for email and Telegram."""
    a = event.action
    verb = event.verb.upper()
    lines = [
        f"Swinger live GTT — {verb}",
        f"Session: {event.session_date.isoformat()}",
        f"Symbol: {a.symbol}",
        f"Action: {a.action_type.value}",
    ]
    if event.gtt_order_id:
        lines.append(f"GTT ID: {event.gtt_order_id}")
    if event.replaced_gtt_id:
        lines.append(f"Replaced GTT: {event.replaced_gtt_id}")
    if a.action_type in (ActionType.PLACE_BUY_GTT, ActionType.ESTABLISH_OCO, ActionType.TRAIL_OCO):
        if a.quantity:
            lines.append(f"Qty: {a.quantity}")
        if a.trigger_price:
            lines.append(f"Trigger: {_format_inr(a.trigger_price)}")
        if a.stop_loss_price:
            lines.append(f"Stop: {_format_inr(a.stop_loss_price)}")
        if a.target_price:
            lines.append(f"Target: {_format_inr(a.target_price)}")
    subject = f"Swinger GTT {verb}: {a.symbol}"
    return subject, "\n".join(lines)


def _send_email(subject: str, body: str, settings: BacktestEmailSettings) -> bool:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to_addrs)
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=60) as smtp:
            if settings.use_tls:
                smtp.starttls()
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return True
    except OSError as exc:
        logger.warning("GTT alert email failed: %s", exc)
        return False


def notify_gtt_event(
    event: GTTAlertEvent,
    *,
    paper_mode: bool = False,
    telegram: TelegramSettings | None = None,
    email: BacktestEmailSettings | None = None,
) -> None:
    """Send email + Telegram for a real broker GTT change. Never raises."""
    if paper_mode:
        return
    subject, body = format_gtt_alert(event)
    tg = telegram if telegram is not None else resolve_telegram_settings()
    if tg is not None:
        send_telegram_message(body, tg)
    try:
        mail = email if email is not None else resolve_email_settings()
    except ValueError as exc:
        logger.debug("Email not configured — skip GTT alert email: %s", exc)
    else:
        _send_email(subject, body, mail)
