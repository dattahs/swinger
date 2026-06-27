"""AWS Lambda entry point — DEPRECATED (v1.3). Use scripts/run_live.py on VPS instead."""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def handler(event: dict | None = None, context: object | None = None) -> dict:
    """EventBridge 16:30 IST trigger."""
    from src.config import load_config
    from src.live.runner import LiveRunner

    cfg = load_config("config.yaml")
    session = None
    if event and event.get("session_date"):
        session = date.fromisoformat(str(event["session_date"]))
    runner = LiveRunner(cfg)
    report = runner.run(session)
    return {
        "status": "ok",
        "session_date": report.session_date.isoformat(),
        "equity_inr": report.equity_inr,
        "actions_planned": report.actions_planned,
        "execution_failures": report.execution_failures,
    }
