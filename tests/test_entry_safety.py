"""Tests for entry safety and GTT overcommit."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.config import RiskManagementConfig
from src.engine.entry_safety import check_entry_safety
from src.engine.ranking import select_candidates
from src.models import BreakoutCandidate, MarketContext
from tests.test_darvas import _minimal_config


def _vbl_bars_jun() -> pd.DataFrame:
    rows = []
    for i in range(19):
        d = date(2026, 5, 27) + timedelta(days=i)
        rows.append(
            {
                "date": d,
                "open": 520.0,
                "high": 525.0,
                "low": 515.0,
                "close": 526.0,
                "volume": 5_000_000,
            }
        )
    rows[-3] = {
        "date": date(2026, 6, 17),
        "open": 541.2,
        "high": 548.0,
        "low": 538.0,
        "close": 544.05,
        "volume": 7_532_315,
    }
    rows[-2] = {
        "date": date(2026, 6, 18),
        "open": 547.7,
        "high": 550.0,
        "low": 528.0,
        "close": 531.55,
        "volume": 8_122_763,
    }
    rows[-1] = {
        "date": date(2026, 6, 19),
        "open": 535.9,
        "high": 540.0,
        "low": 525.0,
        "close": 532.0,
        "volume": 9_495_702,
    }
    return pd.DataFrame(rows)


def test_vbl_post_breakout_two_red_high_vol_days():
    """Breakout 17-Jun; 18–19 consecutive red on high volume → antipattern."""
    bars = _vbl_bars_jun()
    cfg = _minimal_config(
        risk_management=RiskManagementConfig(
            min_structural_r_ratio=0.99,
            entry_sma_period=10,
            entry_post_breakout_consecutive_red_high_vol=2,
        )
    )
    ok, reason = check_entry_safety(bars, cfg, breakout_date=date(2026, 6, 17))
    assert not ok
    assert reason == "DISTRIBUTION_ANTIPATTERN"


def test_breakout_day_only_not_antipattern():
    bars = _vbl_bars_jun().iloc[:-2]
    cfg = _minimal_config(
        risk_management=RiskManagementConfig(entry_sma_period=10),
    )
    ok, reason = check_entry_safety(bars, cfg, breakout_date=date(2026, 6, 17))
    assert ok
    assert reason is None


def test_one_red_day_after_breakout_ok():
    bars = _vbl_bars_jun().iloc[:-1]
    cfg = _minimal_config(
        risk_management=RiskManagementConfig(entry_sma_period=10),
    )
    ok, _ = check_entry_safety(bars, cfg, breakout_date=date(2026, 6, 17))
    assert ok


def test_vbl_close_below_sma():
    bars = _vbl_bars_jun()
    bars.loc[bars.index[-1], "close"] = 500.0
    cfg = _minimal_config(
        risk_management=RiskManagementConfig(
            entry_sma_period=10,
            entry_post_breakout_consecutive_red_high_vol=99,
        ),
    )
    ok, reason = check_entry_safety(bars, cfg, breakout_date=date(2026, 6, 17))
    assert not ok
    assert reason == "PRICE_BELOW_SMA"


def test_gtt_overcommit_allows_more_than_cash():
    cfg = _minimal_config(
        risk_management=RiskManagementConfig(
            min_structural_r_ratio=0.99,
            gtt_capital_overcommit_factor=1.3,
            max_concurrent_positions=10,
            max_sector_exposure_pct=100.0,
        )
    )
    ctx = MarketContext(
        target_date=date(2026, 6, 22),
        account_equity=100_000,
        settled_cash_inr=100_000,
    )
    mk = lambda sym, rr, qty: BreakoutCandidate(  # noqa: E731
        symbol=sym,
        box_top=100,
        box_bottom=90,
        entry_price=100,
        trigger_price=100.05,
        stop_loss_price=89.95,
        target_price=110,
        structural_rr=rr,
        sector=f"S{sym}",
        quantity=qty,
    )
    candidates = [
        mk("A", 5.0, 300),
        mk("B", 4.0, 300),
        mk("C", 3.0, 300),
        mk("D", 2.0, 300),
    ]
    actions, _ = select_candidates(candidates, ctx, cfg, {s: 100 for s in "ABCD"}, set())
    total_cost = sum(a.quantity * 100 for a in actions)
    assert total_cost > 100_000
    assert total_cost <= 130_000 + 1
