#!/usr/bin/env python3
"""Restore SQLite databases from a collect-bundle tarball into local data paths."""

from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
from pathlib import Path

from deploy_common import REPO_ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore Swinger DBs from a VPS bundle")
    parser.add_argument("bundle", type=Path, help="swinger-bundle-*.tar.gz from collect.py")
    parser.add_argument(
        "--into",
        type=Path,
        default=REPO_ROOT / "data",
        help="Local data root (default: repo data/)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing .db files")
    args = parser.parse_args()

    if not args.bundle.exists():
        raise SystemExit(f"Bundle not found: {args.bundle}")

    extract_root = args.bundle.parent / f".restore-{args.bundle.stem}"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)

    with tarfile.open(args.bundle, "r:gz") as tar:
        tar.extractall(extract_root)

    # Bundle layout: swinger-bundle-<stamp>/data/live/... inside tar root folder
    inner_dirs = [p for p in extract_root.iterdir() if p.is_dir()]
    if len(inner_dirs) == 1:
        content = inner_dirs[0]
    else:
        content = extract_root

    mappings = [
        (content / "data" / "live" / "swinger_live.db", args.into / "live" / "swinger_live.db"),
        (content / "data" / "processed" / "swinger_data.db", args.into / "processed" / "swinger_data.db"),
    ]

    for src, dest in mappings:
        if not src.exists():
            print(f"Skip (not in bundle): {src.name}")
            continue
        if dest.exists() and not args.force:
            raise SystemExit(f"Refusing to overwrite {dest} — pass --force")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"Restored {dest}")

    meta = content / "meta"
    if meta.exists():
        print(f"Bundle meta: {meta}")
    print("Done. Point config.yaml data paths at the restored files if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
