#!/usr/bin/env python3
"""Send a test Telegram message using SWINGER_TELEGRAM_* from .env."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.broker.env import load_dotenv
from src.notify.telegram import resolve_telegram_settings, send_telegram_message


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Swinger Telegram bot")
    parser.add_argument(
        "--env",
        default=str(ROOT / ".env"),
        help="Path to .env with SWINGER_TELEGRAM_BOT_TOKEN and SWINGER_TELEGRAM_CHAT_ID",
    )
    parser.add_argument(
        "--message",
        default="Swinger test — Telegram alerts are working.",
        help="Message text to send",
    )
    args = parser.parse_args()
    load_dotenv(args.env)
    settings = resolve_telegram_settings()
    if settings is None:
        print(
            "ERROR: Set SWINGER_TELEGRAM_BOT_TOKEN and SWINGER_TELEGRAM_CHAT_ID in .env",
            file=sys.stderr,
        )
        return 1
    ok = send_telegram_message(args.message, settings)
    if ok:
        print("OK: message sent")
        return 0
    print("ERROR: Telegram API call failed (see logs)", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
