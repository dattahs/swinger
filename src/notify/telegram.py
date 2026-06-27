"""Telegram alerts for live trading — REQUIREMENTS v1 Section 10."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from src.broker.env import load_dotenv

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TelegramSettings:
    bot_token: str
    chat_id: str


def resolve_telegram_settings(
    *,
    bot_token: str | None = None,
    chat_id: str | None = None,
    dotenv_path: str | None = None,
) -> TelegramSettings | None:
    """Return settings when bot token and chat id are configured; else None."""
    if dotenv_path:
        load_dotenv(dotenv_path)
    else:
        load_dotenv(_project_root() / ".env")
    token = (bot_token or os.environ.get("SWINGER_TELEGRAM_BOT_TOKEN", "")).strip()
    cid = (chat_id or os.environ.get("SWINGER_TELEGRAM_CHAT_ID", "")).strip()
    if not token or not cid:
        return None
    return TelegramSettings(bot_token=token, chat_id=cid)


def send_telegram_message(
    text: str,
    settings: TelegramSettings | None = None,
    *,
    parse_mode: str | None = None,
    timeout_sec: int = 30,
) -> bool:
    """Send a message; returns True on success. Logs and returns False if unconfigured or on error."""
    cfg = settings or resolve_telegram_settings()
    if cfg is None:
        logger.debug("Telegram not configured — skip send")
        return False
    url = _TELEGRAM_API.format(token=cfg.bot_token)
    payload: dict[str, str] = {"chat_id": cfg.chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, json=payload, timeout=timeout_sec)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            logger.warning("Telegram API error: %s", body)
            return False
        return True
    except requests.RequestException as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False
