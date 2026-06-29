"""Shared helpers for Swinger VPS deploy scripts (laptop-side)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

DEPLOY_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEPLOY_DIR.parents[1]


def load_deploy_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or DEPLOY_DIR / "deploy.config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"Missing {cfg_path}. Copy deploy.config.example.yaml to deploy.config.yaml and edit."
        )
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return raw


def expand_path(p: str) -> str:
    return os.path.expanduser(os.path.expandvars(p))


def ssh_base(cfg: dict[str, Any]) -> list[str]:
    key = expand_path(str(cfg.get("ssh_key", "")))
    cmd = [
        "ssh",
        "-p",
        str(cfg.get("port", 22)),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if key:
        cmd.extend(["-i", key])
    cmd.append(f"{cfg['user']}@{cfg['host']}")
    return cmd


def scp_base(cfg: dict[str, Any]) -> list[str]:
    key = expand_path(str(cfg.get("ssh_key", "")))
    cmd = [
        "scp",
        "-P",
        str(cfg.get("port", 22)),
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if key:
        cmd.extend(["-i", key])
    return cmd


def run_local(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=False)


def ssh_run(cfg: dict[str, Any], remote_cmd: str, *, check: bool = True) -> subprocess.CompletedProcess:
    return run_local(ssh_base(cfg) + [remote_cmd], check=check)


def scp_to_remote(cfg: dict[str, Any], local_path: Path, remote_path: str) -> None:
    dest = f"{cfg['user']}@{cfg['host']}:{remote_path}"
    run_local(scp_base(cfg) + [str(local_path), dest])


def scp_from_remote(cfg: dict[str, Any], remote_path: str, local_path: Path) -> None:
    src = f"{cfg['user']}@{cfg['host']}:{remote_path}"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    run_local(scp_base(cfg) + [src, str(local_path)])


def remote_root(cfg: dict[str, Any]) -> str:
    return str(cfg["remote_root"]).rstrip("/")


def laptop_config_path(cfg: dict[str, Any]) -> Path:
    rel = cfg.get("laptop_config", "scripts/deploy/vps/config.yaml")
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def laptop_env_path(cfg: dict[str, Any]) -> Path:
    rel = cfg.get("laptop_env", "scripts/deploy/vps/.env")
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def _upstox_env_lines(env_path: Path) -> str:
    """Extract UPSTOX_* lines for VPS shared/.env (avoids sourcing SMTP etc.)."""
    lines: list[str] = [
        "# Synced from laptop — UPSTOX_* only (see deploy_common._upstox_env_lines)",
    ]
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("UPSTOX_") and "=" in line:
                if line.startswith("UPSTOX_ACCESS_TOKEN="):
                    continue  # VPS uses token_file refreshed by login cron
                lines.append(line)
    return "\n".join(lines) + "\n"


def _notify_env_lines(env_path: Path) -> str:
    """Telegram + SMTP lines for VPS GTT alerts."""
    prefixes = ("SWINGER_TELEGRAM_", "SWINGER_SMTP_", "SWINGER_EMAIL_")
    lines: list[str] = ["# Synced from laptop — live GTT alerts"]
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith(prefixes) and "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                value = value.strip()
                if " " in value and not (
                    (value.startswith('"') and value.endswith('"'))
                    or (value.startswith("'") and value.endswith("'"))
                ):
                    value = f'"{value}"'
                lines.append(f"{key.strip()}={value}")
    return "\n".join(lines) + "\n"


def sync_shared_files(cfg: dict[str, Any], *, dry_run: bool = False) -> None:
    """Push laptop config.yaml and .env to VPS shared/ (survives release rotations)."""
    root = remote_root(cfg)
    cfg_local = laptop_config_path(cfg)
    env_local = laptop_env_path(cfg)

    if not cfg_local.exists():
        raise FileNotFoundError(
            f"Missing {cfg_local}. Copy scripts/deploy/vps/config.yaml from config.vps.template.yaml"
        )

    remote_dirs = (
        f"mkdir -p {root}/shared/data/processed {root}/shared/data/live "
        f"{root}/shared/data/live/warmup_cache {root}/shared/logs {root}/shared/bundles"
    )
    if dry_run:
        print(f"Would sync {cfg_local} -> {root}/shared/config.yaml")
        if env_local.exists():
            print(f"Would sync {env_local} -> {root}/shared/.env")
        else:
            print("WARN: no laptop .env — Upstox secrets not pushed")
        return

    ssh_run(cfg, remote_dirs)
    scp_to_remote(cfg, cfg_local, f"{root}/shared/config.yaml")
    print(f"Synced config -> {root}/shared/config.yaml")

    if env_local.exists():
        import tempfile

        upstox_env = _upstox_env_lines(env_local)
        notify_env = _notify_env_lines(env_local)
        merged = upstox_env.rstrip() + "\n\n" + notify_env
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", suffix=".env", delete=False
        ) as tmp:
            tmp.write(merged)
            tmp_path = Path(tmp.name)
        try:
            scp_to_remote(cfg, tmp_path, f"{root}/shared/.env")
        finally:
            tmp_path.unlink(missing_ok=True)
        ssh_run(cfg, f"sed -i 's/\\r$//' {root}/shared/.env && chmod 600 {root}/shared/.env")
        print(
            f"Synced secrets -> {root}/shared/.env "
            f"(UPSTOX_* + SWINGER_TELEGRAM/SMTP from {env_local.name})"
        )
    else:
        print(
            f"WARN: {env_local} missing — set laptop_env in deploy.config.yaml, then push again",
            file=sys.stderr,
        )


def git_version() -> str:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = "nogit"
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dirty = ""
    try:
        subprocess.check_call(["git", "diff", "--quiet"], cwd=REPO_ROOT)
    except (subprocess.CalledProcessError, FileNotFoundError):
        dirty = "-dirty"
    return f"{ts}-{sha}{dirty}"


def write_local_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
