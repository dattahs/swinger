"""Virtual broker fill and exit rules."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.backtest.virtual_broker import VirtualBroker
from src.models import ActionType, ExitReason, PlannedGTTAction


def _bar(*, open_: float, high: float, low: float, close: float) -> pd.Series:
    return pd.Series({"open": open_, "high": high, "low": low, "close": close})


def _place_buy(broker: VirtualBroker, session: date, *, trigger: float, stop: float, target: float) -> None:
    broker.apply_actions(
        session,
        [
            PlannedGTTAction(
                symbol="TEST",
                action_type=ActionType.PLACE_BUY_GTT,
                trigger_price=trigger,
                stop_loss_price=stop,
                target_price=target,
                quantity=10,
                entry_box_top=trigger,
                entry_box_bottom=stop,
            )
        ],
    )


def _establish_oco(broker: VirtualBroker, session: date, *, stop: float, target: float) -> None:
    broker.apply_actions(
        session,
        [
            PlannedGTTAction(
                symbol="TEST",
                action_type=ActionType.ESTABLISH_OCO,
                stop_loss_price=stop,
                target_price=target,
                quantity=10,
            )
        ],
    )


def test_same_day_stop_deferred_until_next_session():
    broker = VirtualBroker(0.0)
    broker.set_initial_cash(100_000.0)
    fill_day = date(2025, 6, 10)
    _place_buy(broker, date(2025, 6, 9), trigger=100.0, stop=95.0, target=110.0)

    events = broker.process_session(
        fill_day,
        {"TEST": _bar(open_=100.0, high=101.0, low=94.0, close=96.0)},
    )
    _establish_oco(broker, fill_day, stop=95.0, target=110.0)

    assert len(events) == 1
    assert events[0].direction == "BUY"
    assert "TEST" in broker.portfolio.positions
    assert not broker.portfolio.closed_trades


def test_same_day_target_ignored_position_carried():
    broker = VirtualBroker(0.0)
    broker.set_initial_cash(100_000.0)
    fill_day = date(2025, 6, 10)
    _place_buy(broker, date(2025, 6, 9), trigger=100.0, stop=95.0, target=110.0)

    events = broker.process_session(
        fill_day,
        {"TEST": _bar(open_=100.0, high=115.0, low=99.0, close=112.0)},
    )
    _establish_oco(broker, fill_day, stop=95.0, target=110.0)

    assert len(events) == 1
    assert events[0].direction == "BUY"
    assert "TEST" in broker.portfolio.positions
    assert not broker.portfolio.closed_trades


def test_target_hits_on_day_after_entry():
    broker = VirtualBroker(0.0)
    broker.set_initial_cash(100_000.0)
    fill_day = date(2025, 6, 10)
    next_day = date(2025, 6, 11)
    _place_buy(broker, date(2025, 6, 9), trigger=100.0, stop=95.0, target=110.0)

    broker.process_session(
        fill_day,
        {"TEST": _bar(open_=100.0, high=115.0, low=99.0, close=112.0)},
    )
    _establish_oco(broker, fill_day, stop=95.0, target=110.0)
    events = broker.process_session(
        next_day,
        {"TEST": _bar(open_=112.0, high=111.0, low=108.0, close=110.0)},
    )

    assert len(events) == 1
    assert events[0].direction == "SELL"
    assert events[0].exit_reason == ExitReason.TARGET_HIT
    assert broker.portfolio.closed_trades[0]["entry_date"] == fill_day
    assert broker.portfolio.closed_trades[0]["exit_date"] == next_day
