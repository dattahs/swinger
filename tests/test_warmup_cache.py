"""Warmup cache round-trip."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.live.warmup_cache import (
    darvas_warmup_fingerprint,
    load_warmup_cache,
    save_warmup_cache,
)
from src.models import BoxState, BoxStateEnum
from tests.test_darvas import _minimal_config


def test_warmup_cache_round_trip(tmp_path: Path):
    cfg = _minimal_config()
    fp = darvas_warmup_fingerprint(cfg)
    registry = {
        "VBL": BoxState(
            symbol="VBL",
            box_state=BoxStateEnum.BREAKOUT,
            box_top=540.0,
            box_bottom=520.0,
            breakout_date=date(2026, 6, 17),
        )
    }
    blocked = {"OLD"}
    path = tmp_path / "cache.json"
    save_warmup_cache(
        path,
        registry,
        blocked,
        warmup_from=date(2025, 10, 1),
        warm_end=date(2026, 6, 19),
        fingerprint=fp,
    )
    loaded = load_warmup_cache(path)
    assert loaded is not None
    reg, blk = loaded
    assert blk == blocked
    assert reg["VBL"].box_state == BoxStateEnum.BREAKOUT
    assert reg["VBL"].breakout_date == date(2026, 6, 17)
