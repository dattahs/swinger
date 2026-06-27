#!/usr/bin/env python3
"""Refresh Upstox access token (Playwright + optional TOTP). No strategy run."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.broker.env import load_dotenv
from src.config import load_config_relaxed
from src.live.runner import LiveRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Upstox OAuth token refresh")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument(
        "--env-file",
        default=".env.live",
        help="Credentials file (default: .env.live for live Upstox login)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force browser login even if today's token exists",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    load_dotenv(ROOT / args.env_file, override=True)
    cfg = load_config_relaxed(Path(args.config))
    if os.environ.get("UPSTOX_TOTP_SECRET", "").strip():
        cfg.system.auth.token_refresh_strategy = "totp_automated_login"
    runner = LiveRunner(cfg, repo_root=ROOT, force_login=args.force)
    token = runner.refresh_upstox_token(force=args.force)
    print(f"OK: access token refreshed ({len(token)} chars) -> {cfg.live.token_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
