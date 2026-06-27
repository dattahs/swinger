#!/usr/bin/env python3
"""Collect logs + DB snapshots from VPS for local troubleshooting."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from deploy_common import REPO_ROOT, load_deploy_config, remote_root, scp_from_remote, ssh_run


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull diagnostic bundle from VPS")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "deploy-bundles",
        help="Local folder for downloaded tarballs",
    )
    args = parser.parse_args()

    cfg = load_deploy_config(args.config)
    root = remote_root(cfg)
    wrapper = f"{root}/bin/swinger-remote-exec.sh"

    import subprocess

    from deploy_common import ssh_base

    cap = subprocess.run(
        ssh_base(cfg) + [f"{wrapper} collect-bundle"],
        check=True,
        text=True,
        capture_output=True,
    )
    remote_archive = cap.stdout.strip().splitlines()[-1]
    if not remote_archive.endswith(".tar.gz"):
        print(cap.stdout, file=sys.stderr)
        print(cap.stderr, file=sys.stderr)
        raise RuntimeError(f"Unexpected collect-bundle output: {remote_archive!r}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    local_name = f"swinger-bundle-{ts}.tar.gz"
    local_path = args.output_dir / local_name
    scp_from_remote(cfg, remote_archive, local_path)
    print(f"Saved: {local_path}")
    print(f"Restore: python scripts/deploy/restore.py {local_path}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
