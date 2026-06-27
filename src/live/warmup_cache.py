"""Persist Darvas state-registry warmup across live dev runs."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from src.config import AppConfig, darvas_algo_fingerprint
from src.models import BoxState, BoxStateEnum

logger = logging.getLogger(__name__)


def darvas_warmup_fingerprint(cfg: AppConfig) -> str:
    """Hash config fields that affect Darvas state during warmup replay."""
    return darvas_algo_fingerprint(cfg)


def warmup_cache_path(
    repo_root: Path,
    cache_dir: str,
    warmup_from: date,
    warm_end: date,
    fingerprint: str,
) -> Path:
    name = f"{warmup_from.isoformat()}_{warm_end.isoformat()}_{fingerprint}.json"
    return repo_root / cache_dir / name


def save_warmup_cache(
    path: Path,
    registry: dict[str, BoxState],
    breakout_reentry_blocked: set[str],
    *,
    warmup_from: date,
    warm_end: date,
    fingerprint: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "warmup_from": warmup_from.isoformat(),
        "warm_end": warm_end.isoformat(),
        "fingerprint": fingerprint,
        "breakout_reentry_blocked": sorted(breakout_reentry_blocked),
        "registry": [st.model_dump(mode="json") for st in registry.values()],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    logger.info("Saved warmup cache %s (%d symbols)", path.name, len(registry))


def load_warmup_cache(path: Path) -> tuple[dict[str, BoxState], set[str]] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        registry = {
            row["symbol"]: BoxState(
                symbol=row["symbol"],
                box_state=BoxStateEnum(row["box_state"]),
                box_top=row.get("box_top"),
                box_bottom=row.get("box_bottom"),
                box_start_date=(
                    date.fromisoformat(row["box_start_date"]) if row.get("box_start_date") else None
                ),
                box_end_date=(
                    date.fromisoformat(row["box_end_date"]) if row.get("box_end_date") else None
                ),
                volume_sma_20=row.get("volume_sma_20"),
                days_in_box=int(row.get("days_in_box") or 0),
                reversal_high=row.get("reversal_high"),
                last_close=row.get("last_close"),
                breakout_date=(
                    date.fromisoformat(row["breakout_date"]) if row.get("breakout_date") else None
                ),
            )
            for row in payload["registry"]
        }
        blocked = set(payload.get("breakout_reentry_blocked") or [])
        return registry, blocked
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Ignoring corrupt warmup cache %s: %s", path, exc)
        return None


def try_load_warmup_cache(
    repo_root: Path,
    cfg: AppConfig,
    cache_dir: str,
    warmup_from: date,
    warm_end: date,
) -> tuple[dict[str, BoxState], set[str]] | None:
    fp = darvas_warmup_fingerprint(cfg)
    path = warmup_cache_path(repo_root, cache_dir, warmup_from, warm_end, fp)
    loaded = load_warmup_cache(path)
    if loaded is None:
        return None
    logger.info("Loaded warmup cache %s", path.name)
    return loaded
