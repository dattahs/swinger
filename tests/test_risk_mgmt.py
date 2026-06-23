"""Tests for GTT expiry, re-entry throttle, and stale-box TSL."""

from __future__ import annotations

from datetime import date

from src.backtest.virtual_broker import VirtualBroker, count_sessions_waiting
from src.config import RiskManagementConfig
from src.engine.risk import boxes_match, compute_trail_action, count_hold_sessions
from src.models import ActionType, BoxState, BoxStateEnum, OpenPosition, PlannedGTTAction
from tests.test_darvas import _minimal_config


def test_count_sessions_waiting():
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    assert count_sessions_waiting(date(2025, 1, 1), date(2025, 1, 3), days) == 3
    assert count_sessions_waiting(date(2025, 1, 2), date(2025, 1, 6), days) == 3


def test_expire_stale_pending_buys():
    broker = VirtualBroker(0.05)
    broker.set_initial_cash(1_000_000)
    placed = date(2025, 1, 1)
    broker.apply_actions(
        placed,
        [
            PlannedGTTAction(
                symbol="ABC",
                action_type=ActionType.PLACE_BUY_GTT,
                trigger_price=101.0,
                stop_loss_price=95.0,
                target_price=110.0,
                quantity=10,
                entry_box_top=100.0,
                entry_box_bottom=95.0,
            )
        ],
    )
    days = [date(2025, 1, d) for d in range(1, 8)]
    session = date(2025, 1, 7)
    cancels = broker.expire_stale_pending_buys(session, max_sessions=5, trading_days=days)
    assert len(cancels) == 1
    assert cancels[0].action_type == ActionType.CANCEL_BUY_GTT
    broker.apply_actions(session, cancels)
    assert "ABC" not in broker.pending_symbols()


def test_boxes_match_within_tolerance():
    assert boxes_match(100.0, 90.0, 101.0, 91.0, 2.0)
    assert not boxes_match(100.0, 90.0, 105.0, 91.0, 2.0)


def test_stale_box_tsl_escalation():
    rm = RiskManagementConfig(
        max_hold_sessions=63,
        stale_box_tsl_daily_pct=10.0,
        box_same_tolerance_pct=2.0,
    )
    cfg = _minimal_config(risk_management=rm)
    pos = OpenPosition(
        symbol="XYZ",
        quantity=100,
        entry_price=110.0,
        current_stop_loss=95.0,
        current_target=125.0,
        entry_date=date(2025, 1, 1),
        entry_box_top=100.0,
        entry_box_bottom=90.0,
        hold_anchor_date=date(2025, 1, 1),
    )
    box = BoxState(
        symbol="XYZ",
        box_state=BoxStateEnum.VALIDATED,
        box_top=100.0,
        box_bottom=90.0,
    )
    trail = compute_trail_action(
        pos,
        box,
        account_equity=500_000,
        target_date=date(2025, 6, 1),
        cfg=cfg,
        hold_sessions=70,
    )
    assert trail is not None
    assert trail.stop_loss_price == 96.0  # 95 + 10% of 10 height
    assert pos.stale_escalation_active


def test_breakout_resets_stale_hold_anchor():
    rm = RiskManagementConfig(max_hold_sessions=63, stale_box_tsl_daily_pct=10.0)
    cfg = _minimal_config(risk_management=rm)
    pos = OpenPosition(
        symbol="XYZ",
        quantity=100,
        entry_price=110.0,
        current_stop_loss=98.0,
        current_target=125.0,
        entry_date=date(2025, 1, 1),
        entry_box_top=100.0,
        entry_box_bottom=90.0,
        hold_anchor_date=date(2025, 1, 1),
        stale_escalation_active=True,
    )
    box = BoxState(
        symbol="XYZ",
        box_state=BoxStateEnum.BREAKOUT,
        box_top=100.0,
        box_bottom=90.0,
    )
    compute_trail_action(
        pos,
        box,
        account_equity=500_000,
        target_date=date(2025, 6, 2),
        cfg=cfg,
        hold_sessions=70,
    )
    assert pos.hold_anchor_date == date(2025, 6, 2)
    assert not pos.stale_escalation_active


def test_count_hold_sessions():
    days = [date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)]
    assert count_hold_sessions(date(2025, 1, 1), date(2025, 1, 3), days) == 3
