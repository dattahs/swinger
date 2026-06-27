"""Tests for Darvas algo tuning registry in config.py."""

from __future__ import annotations

from datetime import date

from pathlib import Path

from src.config import (
    AppConfig,
    BacktestConfig,
    DarvasBoxConfig,
    SystemConfig,
    apply_darvas_algo_overrides,
    darvas_algo_fingerprint,
    darvas_algo_snapshot,
    darvas_price_history_days,
    load_config_relaxed,
)


def _minimal_app_config() -> AppConfig:
    return AppConfig.model_construct(
        system=SystemConfig(),
        backtest=BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            price_warmup_start_date=date(2023, 1, 1),
        ),
        darvas_box=DarvasBoxConfig(),
    )


def test_darvas_algo_snapshot_has_all_tuning_keys():
    cfg = _minimal_app_config()
    snap = darvas_algo_snapshot(cfg)
    assert "darvas_reversal_days" in snap
    assert "breakout_reset_above_top_pct" in snap
    assert "adaptive_sma_period" in snap
    assert "trail_max_risk_pct" in snap
    assert snap["darvas_reversal_days"] == 3


def test_apply_darvas_algo_overrides_short_keys():
    cfg = _minimal_app_config()
    updated = apply_darvas_algo_overrides(
        cfg,
        {"darvas_reversal_days": 5, "breakout_reset_above_top_pct": 4.0},
    )
    assert updated.darvas_box.darvas_reversal_days == 5
    assert updated.darvas_box.breakout_reset_above_top_pct == 4.0
    assert cfg.darvas_box.darvas_reversal_days == 3


def test_apply_darvas_algo_overrides_dot_paths():
    cfg = _minimal_app_config()
    updated = apply_darvas_algo_overrides(
        cfg, {"darvas_box.atr_multiplier": 2.5, "risk_management.entry_sma_period": 30}
    )
    assert updated.darvas_box.atr_multiplier == 2.5
    assert updated.risk_management.entry_sma_period == 30


def test_darvas_algo_fingerprint_changes_with_overrides():
    base = _minimal_app_config()
    tweaked = apply_darvas_algo_overrides(base, {"darvas_reversal_days": 4})
    assert darvas_algo_fingerprint(base) != darvas_algo_fingerprint(tweaked)


def test_darvas_price_history_days():
    cfg = _minimal_app_config()
    assert darvas_price_history_days(cfg) == 280 + 50


def test_load_config_yaml_includes_new_darvas_fields():
    cfg = load_config_relaxed(Path("config.yaml"))
    assert cfg.darvas_box.breakout_volume_sma_period == 20
    assert cfg.darvas_box.price_history_buffer_days == 50
