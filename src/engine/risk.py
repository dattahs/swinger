"""Position sizing, structural R, TRAIL_OCO — REQUIREMENTS v1.2 Section 7."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

from src.config import AppConfig
from src.engine.darvas import compute_atr
from src.engine.r_managed_runner import apply_breakeven_floor, cap_target_at_r
from src.models import ActionType, BoxState, BoxStateEnum, OpenPosition, PlannedGTTAction, make_idempotency_key


def compute_entry_prices(box_top: float, box_bottom: float, cfg: AppConfig) -> tuple[float, float, float, float]:
    rm = cfg.risk_management
    entry_price = box_top
    trigger_price = box_top + rm.gtt_trigger_buffer_inr
    stop_loss_price = box_bottom - rm.stop_loss_buffer_fraction_inr
    box_height = box_top - box_bottom
    target_price = box_top + rm.target_box_height_multiplier * box_height
    target_price = cap_target_at_r(entry_price, stop_loss_price, target_price, cfg)
    return entry_price, trigger_price, stop_loss_price, target_price


def compute_structural_rr(entry_price: float, stop_loss_price: float, target_price: float) -> float:
    per_share_risk = entry_price - stop_loss_price
    if per_share_risk <= 0:
        return 0.0
    reward = target_price - entry_price
    return round(reward / per_share_risk, 4)


def size_position(
    entry_price: float,
    stop_loss_price: float,
    account_equity: float,
    settled_cash_inr: float,
    cfg: AppConfig,
) -> int | None:
    rm = cfg.risk_management
    per_share_risk = entry_price - stop_loss_price
    if per_share_risk <= 0:
        return None

    raw_qty = math.floor(account_equity * rm.account_risk_pct / 100 / per_share_risk)
    cap_qty = math.floor(account_equity * rm.max_capital_per_trade_pct / 100 / entry_price)
    port_qty = math.floor(
        account_equity * rm.max_portfolio_loss_per_trade_pct / 100 / per_share_risk
    )
    final_qty = min(raw_qty, cap_qty, port_qty)
    if final_qty < 1:
        return None
    if final_qty * entry_price > settled_cash_inr:
        return None
    return int(final_qty)


def passes_structural_r_min(structural_rr: float, cfg: AppConfig) -> bool:
    return structural_rr >= cfg.risk_management.min_structural_r_ratio


def boxes_match(
    top1: float | None,
    bottom1: float | None,
    top2: float | None,
    bottom2: float | None,
    tolerance_pct: float,
) -> bool:
    if top1 is None or bottom1 is None or top2 is None or bottom2 is None:
        return False
    if top1 <= 0 or top2 <= 0:
        return False
    top_diff = abs(top1 - top2) / top1 * 100
    bottom_diff = abs(bottom1 - bottom2) / bottom1 * 100
    return top_diff <= tolerance_pct and bottom_diff <= tolerance_pct


def count_hold_sessions(anchor: date, target_date: date, trading_days: list[date]) -> int:
    return sum(1 for d in trading_days if anchor <= d <= target_date)


def compute_atr_band_bounds(
    reversal_high: float,
    bars: pd.DataFrame,
    cfg: AppConfig,
) -> tuple[float, float]:
    dcfg = cfg.darvas_box
    atr = compute_atr(bars, dcfg.atr_period)
    atr_top = reversal_high + dcfg.atr_multiplier * atr
    atr_bottom = reversal_high - dcfg.atr_multiplier * atr
    return atr_top, atr_bottom


def atr_band_target_price(atr_top: float, atr_bottom: float, band_pct: float) -> float:
    """Target at band_pct below the ATR top within the ATR band."""
    return atr_top - (band_pct / 100.0) * (atr_top - atr_bottom)


def compute_dynamic_atr_target_action(
    position: OpenPosition,
    box_state: BoxState,
    bars: pd.DataFrame,
    cfg: AppConfig,
    target_date: date,
) -> PlannedGTTAction | None:
    rm = cfg.risk_management
    if not rm.dynamic_atr_target_enabled or box_state.reversal_high is None or bars.empty:
        return None
    atr_top, atr_bottom = compute_atr_band_bounds(box_state.reversal_high, bars, cfg)
    if atr_top <= atr_bottom:
        return None
    candidate = atr_band_target_price(atr_top, atr_bottom, rm.dynamic_atr_target_band_pct)
    initial_target = position.initial_target if position.initial_target is not None else position.current_target
    if atr_top <= initial_target or candidate <= position.current_target:
        return None
    return PlannedGTTAction(
        symbol=position.symbol,
        action_type=ActionType.TRAIL_OCO,
        trigger_price=0.0,
        stop_loss_price=position.current_stop_loss,
        target_price=candidate,
        quantity=position.quantity,
        idempotency_key=make_idempotency_key(
            position.symbol, target_date, f"ATR_TARGET_{candidate:.2f}"
        ),
    )


def compute_trail_action(
    position: OpenPosition,
    box_state: BoxState,
    account_equity: float,
    target_date: date,
    cfg: AppConfig,
    *,
    hold_sessions: int = 0,
    last_close: float | None = None,
) -> PlannedGTTAction | None:
    ts = cfg.trailing_stop
    rm = cfg.risk_management
    symbol = position.symbol

    if box_state.box_state == BoxStateEnum.BREAKOUT:
        position.hold_anchor_date = target_date
        position.stale_escalation_active = False

    has_active_box = (
        box_state.box_bottom is not None and box_state.box_state != BoxStateEnum.SCANNING
    )
    r_managed = cfg.r_managed_runner.enabled

    if not has_active_box and not (r_managed and last_close is not None):
        return None

    if has_active_box:
        new_stop = max(position.current_stop_loss, box_state.box_bottom)
    else:
        new_stop = position.current_stop_loss

    if (
        has_active_box
        and hold_sessions >= rm.max_hold_sessions
        and boxes_match(
            position.entry_box_top,
            position.entry_box_bottom,
            box_state.box_top,
            box_state.box_bottom,
            rm.box_same_tolerance_pct,
        )
        and box_state.box_state != BoxStateEnum.BREAKOUT
    ):
        position.stale_escalation_active = True
        height = (position.entry_box_top or 0.0) - (position.entry_box_bottom or 0.0)
        if height > 0:
            daily_bump = height * (rm.stale_box_tsl_daily_pct / 100.0)
            new_stop = max(new_stop, position.current_stop_loss + daily_bump)

    if r_managed and last_close is not None:
        new_stop = apply_breakeven_floor(new_stop, last_close, position, cfg)

    risk_at_stop_inr = position.quantity * max(0.0, position.entry_price - new_stop)
    risk_pct = 100.0 * risk_at_stop_inr / account_equity if account_equity > 0 else 100.0

    if risk_pct > ts.max_trail_risk_pct:
        return None
    if new_stop <= position.current_stop_loss + ts.min_ratchet_inr:
        return None

    return PlannedGTTAction(
        symbol=symbol,
        action_type=ActionType.TRAIL_OCO,
        trigger_price=0.0,
        stop_loss_price=new_stop,
        target_price=position.current_target,
        quantity=position.quantity,
        idempotency_key=make_idempotency_key(symbol, target_date, ActionType.TRAIL_OCO.value),
    )
