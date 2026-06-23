"""Acceptance tests — REQUIREMENTS v1.2 Section 15."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.config import AppConfig, DarvasBoxConfig, RiskManagementConfig, TrailingStopConfig, UniverseFilters
from src.engine.darvas import update_box_state
from src.engine.risk import (
    compute_entry_prices,
    compute_structural_rr,
    compute_trail_action,
    passes_structural_r_min,
    size_position,
)
from src.models import BoxState, BoxStateEnum, OpenPosition


def _minimal_config(**overrides) -> AppConfig:
    from src.config import (
        BacktestConfig,
        CandidateRankingConfig,
        FundamentalFilters,
        SystemConfig,
    )

    rm = RiskManagementConfig(min_structural_r_ratio=3.0, gtt_trigger_buffer_inr=0.05)
    if "risk_management" in overrides:
        rm = overrides.pop("risk_management")
    dbox = DarvasBoxConfig()
    if "darvas_box" in overrides:
        dbox = overrides.pop("darvas_box")
    return AppConfig.model_construct(
        system=SystemConfig(),
        backtest=BacktestConfig(
            start_date=date(2018, 1, 1),
            end_date=date(2018, 12, 31),
            price_warmup_start_date=date(2016, 9, 1),
        ),
        universe_filters=UniverseFilters(),
        fundamental_filters=FundamentalFilters(),
        darvas_box=dbox,
        risk_management=rm,
        trailing_stop=TrailingStopConfig(max_trail_risk_pct=10.0),
        candidate_ranking=CandidateRankingConfig(),
        **overrides,
    )


def _trend_bars(n: int = 300, start: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        p = start + i * 0.5
        rows.append(
            {"date": date(2016, 1, 1), "open": p, "high": p + 1, "low": p - 1, "close": p, "volume": 1_000_000}
        )
    return pd.DataFrame(rows)


class TestDarvas:
    def test_breakout_requires_volume(self):
        cfg = _minimal_config()
        bars = _trend_bars(300)
        bars.loc[bars.index[-1], "close"] = bars.iloc[-1]["high"] + 5
        bars.loc[bars.index[-1], "volume"] = 100
        state = BoxState(symbol="X", box_state=BoxStateEnum.VALIDATED, box_top=100, box_bottom=90, days_in_box=10)
        state.volume_sma_20 = 1_000_000
        out = update_box_state(state, bars, cfg, True, False, date(2018, 1, 15))
        assert out.box_state != BoxStateEnum.BREAKOUT

    def test_target_price_formula(self):
        cfg = _minimal_config()
        entry, trigger, stop, target = compute_entry_prices(110, 100, cfg)
        assert entry == 110
        assert target == 120
        assert trigger == pytest.approx(110.05)

    def test_breakout_resets_when_close_below_box_bottom(self):
        cfg = _minimal_config(darvas_box=DarvasBoxConfig(breakout_reset_above_top_pct=2.0))
        bars = _trend_bars(300, start=5000)
        bars.loc[bars.index[-1], ["close", "low", "high"]] = [5200, 5100, 5250]
        state = BoxState(
            symbol="BRIT",
            box_state=BoxStateEnum.BREAKOUT,
            box_top=5891.5,
            box_bottom=5620.0,
            reversal_high=5900.0,
            days_in_box=5,
        )
        out = update_box_state(state, bars, cfg, True, False, date(2026, 6, 19))
        assert out.box_state == BoxStateEnum.SCANNING
        assert out.box_top is None
        assert out.reversal_high is None

    def test_breakout_resets_when_close_far_above_box_top(self):
        cfg = _minimal_config(darvas_box=DarvasBoxConfig(breakout_reset_above_top_pct=2.0))
        bars = _trend_bars(300, start=8000)
        bars.loc[bars.index[-1], ["close", "low", "high"]] = [10083, 9831, 10120]
        state = BoxState(
            symbol="POLY",
            box_state=BoxStateEnum.BREAKOUT,
            box_top=8168.65,
            box_bottom=7897.0,
            reversal_high=7619.5,
            days_in_box=5,
        )
        out = update_box_state(state, bars, cfg, True, False, date(2026, 6, 19))
        assert out.box_state == BoxStateEnum.SCANNING
        assert out.box_top is None


class TestRisk:
    def test_per_share_risk_zero_rejected(self):
        cfg = _minimal_config()
        assert size_position(100, 100, 500_000, 500_000, cfg) is None

    def test_portfolio_loss_cap(self):
        cfg = _minimal_config()
        qty = size_position(110, 100, 500_000, 500_000, cfg)
        assert qty is not None
        loss = qty * (110 - 100)
        assert loss <= 500_000 * 0.10 + 1

    def test_structural_r_minimum(self):
        cfg = _minimal_config()
        assert not passes_structural_r_min(2.5, cfg)
        assert passes_structural_r_min(3.0, cfg)

    def test_trail_10pct_gate_blocks(self):
        cfg = _minimal_config()
        pos = OpenPosition(
            symbol="X",
            quantity=1000,
            entry_price=100,
            current_stop_loss=80,
            current_target=120,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.BREAKOUT, box_bottom=85)
        action = compute_trail_action(pos, box, 100_000, date(2018, 1, 1), cfg)
        assert action is None

    def test_trail_emits_when_gate_passes(self):
        cfg = _minimal_config()
        pos = OpenPosition(
            symbol="X",
            quantity=10,
            entry_price=100,
            current_stop_loss=80,
            current_target=120,
        )
        box = BoxState(symbol="X", box_state=BoxStateEnum.BREAKOUT, box_bottom=90)
        action = compute_trail_action(pos, box, 500_000, date(2018, 1, 1), cfg)
        assert action is not None
        assert action.stop_loss_price == 90


class TestStructuralRR:
    def test_rr_calculation(self):
        rr = compute_structural_rr(110, 99.95, 120)
        assert rr > 0
