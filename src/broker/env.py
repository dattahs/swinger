"""Load Upstox credentials from a local .env file (never commit .env)."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path, *, override: bool = False) -> bool:
    """Parse KEY=VALUE lines into os.environ. Returns True if file existed."""
    path = Path(path)
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
    return True
