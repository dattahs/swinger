"""Tests for optional R-managed runner exit policy."""

from __future__ import annotations

from datetime import date

import pytest

from src.config import RManagedRunnerConfig
from src.engine.r_managed_runner import (
    apply_breakeven_floor,
    cap_target_at_r,
    initial_risk_per_share,
    unrealized_r,
)
from src.engine.risk import compute_entry_prices, compute_trail_action
from src.models import BoxState, BoxStateEnum, OpenPosition
from tests.test_darvas import _minimal_config


def _config_with_r_managed(**rrm_overrides) -> object:
    rrm = RManagedRunnerConfig(enabled=True, **rrm_overrides)
    return _minimal_config(r_managed_runner=rrm)


class TestRManagedRunnerHelpers:
    def test_unrealized_r_at_2r(self):
        assert unrealized_r(120.0, 100.0, 10.0) == pytest.approx(2.0)

    def test_cap_target_at_5r(self):
        cfg = _config_with_r_managed()
        # structural target 130, 5R cap at 160.25 for risk=10.05
        assert cap_target_at_r(110.0, 99.95, 130.0, cfg) == 130.0
        assert cap_target_at_r(110.0, 99.95, 200.0, cfg) == pytest.approx(160.25)

    def test_cap_disabled_passthrough(self):
        cfg = _minimal_config()
        assert cap_target_at_r(110.0, 99.95, 200.0, cfg) == 200.0


class TestRManagedRunnerEntry:
    def test_entry_target_unchanged_for_standard_box(self):
        """1:1 structural target is ~1R, so 5R cap is a ceiling that does not bind."""
        cfg = _config_with_r_managed(max_target_r=5.0)
        entry, _, stop, target = compute_entry_prices(110.0, 100.0, cfg)
        assert target == 120.0
        risk = initial_risk_per_share(entry, stop)
        assert target < entry + 5.0 * risk


class TestRManagedRunnerTrail:
    def test_breakeven_on_2r_even_when_scanning(self):
        cfg = _config_with_r_managed()
        pos = OpenPosition(
            symbol="X",
            quantity=10,
            entry_price=100.0,
            current_stop_loss=90.0,
            current_target=120.0,
            initial_stop_loss=90.0,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.SCANNING)
        action = compute_trail_action(
            pos,
            box,
            500_000,
            date(2018, 1, 1),
            cfg,
            last_close=120.0,
        )
        assert action is not None
        assert action.stop_loss_price == 100.0

    def test_breakeven_not_applied_below_threshold(self):
        cfg = _config_with_r_managed()
        pos = OpenPosition(
            symbol="X",
            quantity=10,
            entry_price=100.0,
            current_stop_loss=89.95,
            current_target=120.0,
            initial_stop_loss=89.95,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.SCANNING)
        action = compute_trail_action(
            pos,
            box,
            500_000,
            date(2018, 1, 1),
            cfg,
            last_close=115.0,
        )
        assert action is None

    def test_box_ratchet_then_breakeven_floor(self):
        cfg = _config_with_r_managed()
        pos = OpenPosition(
            symbol="X",
            quantity=10,
            entry_price=100.0,
            current_stop_loss=90.0,
            current_target=120.0,
            initial_stop_loss=90.0,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.BREAKOUT, box_bottom=95.0)
        action = compute_trail_action(
            pos,
            box,
            500_000,
            date(2018, 1, 1),
            cfg,
            last_close=120.0,
        )
        assert action is not None
        assert action.stop_loss_price == 100.0

    def test_baseline_scanning_still_no_trail(self):
        cfg = _minimal_config()
        pos = OpenPosition(
            symbol="X",
            quantity=10,
            entry_price=100.0,
            current_stop_loss=89.95,
            current_target=120.0,
            initial_stop_loss=89.95,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.SCANNING)
        action = compute_trail_action(
            pos,
            box,
            500_000,
            date(2018, 1, 1),
            cfg,
            last_close=120.0,
        )
        assert action is None
