"""Tests for bhavcopy mock Upstox broker."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.broker.mock_upstox import MockUpstoxGTTClient
from src.broker.instruments import InstrumentResolver
from src.broker.reconcile import reconcile_broker_state
from src.models import ActionType, PlannedGTTAction
from src.repository.sqlite import SqliteBacktestRepository, SqliteDataLake


@pytest.fixture
def repo(tmp_path):
    r = SqliteBacktestRepository(tmp_path / "live.db")
    yield r
    r.close()


@pytest.fixture
def instruments(tmp_path):
    path = tmp_path / "map.json"
    path.write_text('{"RELIANCE": "NSE_EQ|INE002A01018"}', encoding="utf-8")
    return InstrumentResolver(path)


class _StubDataLake:
    def __init__(self, bars: dict[tuple[str, date], dict[str, float]]) -> None:
        self._bars = bars

    def get_daily_bars(self, symbol: str, end: date, days: int):
        import pandas as pd

        rows = []
        for (sym, d), bar in self._bars.items():
            if sym == symbol and d <= end:
                rows.append({"date": d, **bar})
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows).sort_values("date").tail(days)
        return df.reset_index(drop=True)


def test_mock_buy_fill_on_next_session(repo, instruments):
    dl = _StubDataLake(
        {
            ("RELIANCE", date(2026, 6, 19)): {
                "open": 2500,
                "high": 2520,
                "low": 2490,
                "close": 2510,
            },
        }
    )
    broker = MockUpstoxGTTClient(
        repo, dl, instruments, slippage_pct=0.0, initial_capital_inr=100_000.0
    )
    action = PlannedGTTAction(
        symbol="RELIANCE",
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=2510.0,
        stop_loss_price=2400.0,
        target_price=2700.0,
        quantity=10,
    )
    broker.fetch_snapshot(date(2026, 6, 18), tracked_gtt_ids=[], symbols=[])
    broker.place_buy_gtt(action, instruments.resolve("RELIANCE"))

    snap = broker.fetch_snapshot(date(2026, 6, 19), tracked_gtt_ids=[], symbols=["RELIANCE"])
    assert len(snap.fills_today) == 1
    assert snap.fills_today[0].transaction_type == "BUY"
    assert snap.fills_today[0].quantity == 10

    reconcile_broker_state(date(2026, 6, 19), broker, repo, adopt_broker_truth=True)
    assert len(repo.get_open_positions()) == 1


def test_same_day_gtt_not_filled(repo, instruments):
    dl = _StubDataLake(
        {
            ("RELIANCE", date(2026, 6, 19)): {
                "open": 2500,
                "high": 2600,
                "low": 2490,
                "close": 2550,
            },
        }
    )
    broker = MockUpstoxGTTClient(
        repo, dl, instruments, slippage_pct=0.0, initial_capital_inr=100_000.0
    )
    broker.fetch_snapshot(date(2026, 6, 19), tracked_gtt_ids=[], symbols=[])
    action = PlannedGTTAction(
        symbol="RELIANCE",
        action_type=ActionType.PLACE_BUY_GTT,
        trigger_price=2510.0,
        stop_loss_price=2400.0,
        target_price=2700.0,
        quantity=10,
    )
    broker.place_buy_gtt(action, instruments.resolve("RELIANCE"))

    snap = broker.fetch_snapshot(date(2026, 6, 19), tracked_gtt_ids=[], symbols=["RELIANCE"])
    assert snap.fills_today == []
