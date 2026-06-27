#!/usr/bin/env python3
"""Push a versioned Swinger release to the VPS over SSH."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from deploy_common import (
    REPO_ROOT,
    git_version,
    load_deploy_config,
    remote_root,
    run_local,
    scp_to_remote,
    ssh_run,
    sync_shared_files,
    write_local_json,
)


def _git_archive(version: str, dest: Path) -> None:
    with tarfile.open(dest, "w:gz") as tar:
        proc = subprocess.run(
            ["git", "archive", "--format=tar", "HEAD"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
        )
        import io

        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as inner:
            for member in inner.getmembers():
                tar.addfile(member, inner.extractfile(member))


def _filesystem_archive(version: str, dest: Path) -> None:
    exclude_dirs = {".git", ".venv", "venv", "__pycache__", "backtest_outputs", ".pytest_cache"}
    exclude_names = {".env"}

    def filt(ti: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = ti.name.removeprefix("./")
        parts = Path(name).parts
        if parts and parts[0] in exclude_dirs:
            return None
        if parts and parts[0] == "data":
            if len(parts) >= 2 and parts[1] != "instruments":
                return None
        if Path(name).name in exclude_names:
            return None
        return ti

    with tarfile.open(dest, "w:gz") as tar:
        tar.add(REPO_ROOT, arcname=".", filter=filt)


def _git_working_tree_dirty() -> bool:
    try:
        subprocess.check_call(["git", "diff", "--quiet"], cwd=REPO_ROOT)
        subprocess.check_call(["git", "diff", "--cached", "--quiet"], cwd=REPO_ROOT)
        out = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
        return bool(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return True


def create_archive(version: str, dest: Path) -> str:
    """Return archive source label: git | working-tree | filesystem."""
    if shutil.which("git") and (REPO_ROOT / ".git").exists() and not _git_working_tree_dirty():
        try:
            _git_archive(version, dest)
            return "git"
        except subprocess.CalledProcessError:
            pass
    _filesystem_archive(version, dest)
    return "working-tree" if (REPO_ROOT / ".git").exists() else "filesystem"


def main() -> int:
    parser = argparse.ArgumentParser(description="Push Swinger release to VPS")
    parser.add_argument("--version", help="Release id (default: git timestamp-sha)")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = load_deploy_config(args.config)
    root = remote_root(cfg)
    version = args.version or git_version()
    release_dir = f"{root}/releases/{version}"
    keep = int(cfg.get("release_keep", 5))
    py = str(cfg.get("python", "python3"))

    meta = {
        "version": version,
        "remote_root": root,
        "source": "pending",
    }
    write_local_json(REPO_ROOT / "scripts" / "deploy" / ".last-push.json", meta)
    print(f"Release: {version}")

    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / f"swinger-{version}.tar.gz"
        meta["source"] = create_archive(version, archive)
        print(f"Archive source: {meta['source']}")
        remote_archive = f"/tmp/swinger-{version}.tar.gz"

        if args.dry_run:
            print(f"Would upload {archive} -> {remote_archive}")
            sync_shared_files(cfg, dry_run=True)
            return 0

        scp_to_remote(cfg, archive, remote_archive)

        remote_script = f"""
set -euo pipefail
mkdir -p '{release_dir}'
tar -xzf '{remote_archive}' -C '{release_dir}'
find '{release_dir}' -name '*.sh' -exec sed -i 's/\\r$//' {{}} +
find '{release_dir}/scripts/deploy/on-server' -name '*.sh' -exec chmod +x {{}} + 2>/dev/null || true
rm -f '{remote_archive}'
echo '{version}' > '{release_dir}/DEPLOY_VERSION'
cat > '{release_dir}/DEPLOY_VERSION.json' <<'META'
{json.dumps(meta, indent=2)}
META
ln -sfn '{release_dir}' '{root}/current'
if [[ ! -d '{root}/venv' ]]; then
  {py} -m venv '{root}/venv'
fi
source '{root}/venv/bin/activate'
pip install -q --upgrade pip
pip install -q -r '{release_dir}/requirements.txt'
[[ -f '{release_dir}/requirements-live.txt' ]] && pip install -q -r '{release_dir}/requirements-live.txt'
cd '{root}/releases' && ls -1dt */ 2>/dev/null | tail -n +{keep + 1} | xargs -r rm -rf
echo 'Deployed {version} -> {root}/current'
"""
        ssh_run(cfg, remote_script)

    sync_shared_files(cfg)
    print(f"OK: {cfg['user']}@{cfg['host']}:{root}/current -> releases/{version}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
