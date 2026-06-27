"""Tests for GTT alert formatting and Telegram settings."""

from __future__ import annotations

from datetime import date

from src.models import ActionType, PlannedGTTAction
from src.notify.gtt_alerts import GTTAlertEvent, format_gtt_alert
from src.notify.telegram import resolve_telegram_settings


def test_format_gtt_place_alert():
    action = PlannedGTTAction(
        symbol="SYRMA",
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=1433.05,
        stop_loss_price=1376.45,
        target_price=1489.5,
        quantity=14,
    )
    subject, body = format_gtt_alert(
        GTTAlertEvent(
            verb="placed",
            action=action,
            session_date=date(2026, 6, 26),
            gtt_order_id="GTT-123",
        )
    )
    assert "SYRMA" in subject
    assert "GTT-123" in body
    assert "1,433.05" in body


def test_resolve_telegram_missing_returns_none(monkeypatch):
    monkeypatch.delenv("SWINGER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SWINGER_TELEGRAM_CHAT_ID", raising=False)
    assert resolve_telegram_settings(bot_token="", chat_id="") is None


def test_send_telegram_no_config(monkeypatch):
    from src.notify import telegram as tg

    monkeypatch.setattr(tg, "resolve_telegram_settings", lambda **_: None)
    assert tg.send_telegram_message("hi", settings=None) is False
