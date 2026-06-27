#!/usr/bin/env python3
"""Run Swinger commands on the remote VPS over SSH."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from deploy_common import load_deploy_config, remote_root, ssh_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Remote Swinger operations over SSH")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("setup", help="Bootstrap VPS (deps, venv, cron, dirs)")

    p_live = sub.add_parser("live", help="Run daily live pipeline")
    p_live.add_argument("--date", help="Session YYYY-MM-DD")
    p_live.add_argument("--login", action="store_true")
    p_live.add_argument("--no-warmup", action="store_true")
    p_live.add_argument("-v", "--verbose", action="store_true")
    p_live.add_argument("extra", nargs=argparse.REMAINDER, help="Extra args passed to run_live.py")

    p_ingest = sub.add_parser("ingest", help="Run data ingest")
    p_ingest.add_argument("extra", nargs=argparse.REMAINDER, help="e.g. -- --download-bhavcopy")

    sub.add_parser("test-mock", help="Demo ingest + paper live run (mock Upstox GTTs)")
    p_gtt = sub.add_parser("test-live-gtt", help="Place and verify a live Upstox buy GTT")
    p_gtt.add_argument("--symbol", default="HDFCBANK")
    p_gtt.add_argument("--trigger", type=float, default=720.0)
    p_gtt.add_argument("--qty", type=int, default=1)
    p_gtt.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("test-sandbox", help="Place a sandbox Upstox order (no real market fills)")
    sub.add_parser("login", help="Force Upstox browser login on VPS")

    p_sh = sub.add_parser("shell", help="Open SSH session to app directory")
    p_sh.add_argument("cmd", nargs=argparse.REMAINDER, help="Optional remote command")

    args = parser.parse_args()
    cfg = load_deploy_config()
    root = remote_root(cfg)
    wrapper = f"{root}/bin/swinger-remote-exec.sh"

    if args.command == "shell":
        remote = f"cd {root}/current && bash"
        if args.cmd:
            remote = " ".join([remote, "-lc", repr(" ".join(args.cmd))])
        ssh_run(cfg, remote, check=False)
        return 0

    remote_args: list[str] = []
    if args.command == "live":
        cmd = "live"
        if args.date:
            remote_args.extend(["--date", args.date])
        if args.login:
            remote_args.append("--login")
        if args.no_warmup:
            remote_args.append("--no-warmup")
        if args.verbose:
            remote_args.append("-v")
        remote_args.extend(_strip_leading_extra(args.extra))
    elif args.command == "login":
        cmd = "login"
    elif args.command == "ingest":
        cmd = "ingest"
        remote_args.extend(_strip_leading_extra(args.extra))
    elif args.command == "setup":
        cron_login = str(cfg.get("cron_login", "45 8 * * 1-5"))
        cron_live = str(cfg.get("cron_live", "30 16 * * 1-5"))
        cron_ingest = str(cfg.get("cron_ingest", "0 17 * * 1-5"))
        remote = (
            f"SWINGER_CRON_LOGIN='{cron_login}' SWINGER_CRON_LIVE='{cron_live}' "
            f"SWINGER_CRON_INGEST='{cron_ingest}' bash {root}/current/scripts/deploy/setup-vps.sh"
        )
        return ssh_run(cfg, remote, check=False).returncode
    elif args.command == "test-live-gtt":
        remote_args = [
            "--symbol", args.symbol,
            "--trigger", str(args.trigger),
            "--qty", str(args.qty),
        ]
        if args.verbose:
            remote_args.append("-v")
        quoted = " ".join(_shell_quote(a) for a in remote_args)
        return ssh_run(cfg, f"{wrapper} test-live-gtt {quoted}", check=False).returncode
    elif args.command == "test-sandbox":
        return ssh_run(cfg, f"{wrapper} test-sandbox", check=False).returncode
    elif args.command == "test-mock":
        # Demo data lake + paper live (mock GTTs; no Upstox credentials required)
        remote = (
            f"{wrapper} ingest --demo && "
            f"{wrapper} live --no-warmup -v --date 2018-06-29"
        )
        return ssh_run(cfg, remote, check=False).returncode
    else:
        raise SystemExit(f"Unknown command: {args.command}")

    quoted = " ".join(_shell_quote(a) for a in remote_args)
    remote = f"{wrapper} {cmd} {quoted}".strip()
    return ssh_run(cfg, remote, check=False).returncode


def _strip_leading_extra(extra: list[str]) -> list[str]:
    if extra and extra[0] == "--":
        return extra[1:]
    return extra


def _shell_quote(s: str) -> str:
    if not s:
        return "''"
    if all(c.isalnum() or c in "/._-:" for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
