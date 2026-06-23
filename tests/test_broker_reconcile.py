"""Tests for broker reconciliation."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.broker.reconcile import compute_equity, reconcile_broker_state
from src.broker.types import BrokerFill, BrokerFunds, BrokerGTT, BrokerPosition, BrokerSnapshot, GTTStatus
from src.broker.upstox import UpstoxGTTClient
from src.broker.instruments import InstrumentResolver
from src.models import OpenPosition, TradeLedgerRow
from src.repository.sqlite import SqliteBacktestRepository


class MockBroker:
    def __init__(self, snapshot: BrokerSnapshot) -> None:
        self.snapshot = snapshot

    def fetch_snapshot(self, session_date, *, tracked_gtt_ids, symbols=None):
        return self.snapshot


@pytest.fixture
def repo():
    r = SqliteBacktestRepository()
    yield r
    r.close()


@pytest.fixture
def instruments(tmp_path):
    path = tmp_path / "map.json"
    path.write_text('{"RELIANCE": "NSE_EQ|INE002A01018"}', encoding="utf-8")
    return InstrumentResolver(path)


def test_reconcile_adopts_broker_position(repo):
    snap = BrokerSnapshot(
        as_of=datetime.now(timezone.utc),
        funds=BrokerFunds(available_cash_inr=100_000.0),
        positions=[
            BrokerPosition(symbol="RELIANCE", quantity=10, average_price=2500.0),
        ],
        gtt_orders=[],
        fills_today=[],
    )
    result = reconcile_broker_state(
        date(2026, 6, 19),
        MockBroker(snap),
        repo,
        adopt_broker_truth=True,
    )
    assert result.settled_cash_inr == 100_000.0
    positions = repo.get_open_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "RELIANCE"
    assert positions[0].quantity == 10


def test_reconcile_buy_fill_clears_pending(repo):
    repo.set_system_state(
        "pending_gtts",
        {
            "RELIANCE": {
                "gtt_order_id": "GTT-1",
                "trigger_price": 2500,
                "stop_loss_price": 2400,
                "target_price": 2600,
                "quantity": 5,
                "placed_date": "2026-06-18",
            }
        },
    )
    snap = BrokerSnapshot(
        as_of=datetime.now(timezone.utc),
        funds=BrokerFunds(available_cash_inr=50_000.0),
        positions=[],
        gtt_orders=[],
        fills_today=[
            BrokerFill(
                symbol="RELIANCE",
                order_id="O1",
                trade_id="T1",
                transaction_type="BUY",
                quantity=5,
                price=2505.0,
            )
        ],
    )
    reconcile_broker_state(date(2026, 6, 19), MockBroker(snap), repo)
    pending = repo.get_system_state("pending_gtts")
    assert "RELIANCE" not in pending
    assert len(repo.get_open_positions()) == 1


def test_compute_equity():
    eq = compute_equity(
        100_000.0,
        [OpenPosition(symbol="X", quantity=10, entry_price=100, current_stop_loss=90, current_target=120)],
        {"X": 110.0},
    )
    assert eq == 101_100.0


def test_paper_place_buy(instruments):
    client = UpstoxGTTClient("", instruments, paper_mode=True)
    from src.models import ActionType, PlannedGTTAction

    action = PlannedGTTAction(
        symbol="RELIANCE",
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=2500,
        stop_loss_price=2400,
        target_price=2600,
        quantity=1,
    )
    gtt_id = client.place_buy_gtt(action, instruments.resolve("RELIANCE"))
    assert gtt_id.startswith("PAPER-BUY-")
