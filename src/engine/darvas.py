"""Darvas box state machine — REQUIREMENTS v1.2 Section 6."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.config import AppConfig, DarvasBoxConfig
from src.models import BoxState, BoxStateEnum

if TYPE_CHECKING:
    from src.debug_log import ActionDebugLogger


def compute_atr(bars: pd.DataFrame, period: int) -> float:
    if len(bars) < 2:
        return 0.0
    col = f"atr_{period}"
    if col in bars.columns:
        vals = bars[col].dropna()
        if not vals.empty:
            return float(vals.iloc[-1])
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    if len(tr) < period:
        return float(np.mean(tr)) if len(tr) else 0.0
    return float(np.mean(tr[-period:]))


def volume_sma(bars: pd.DataFrame, period: int = 20) -> float:
    if "vol_sma_20" in bars.columns and period == 20:
        val = bars.iloc[-1]["vol_sma_20"]
        if pd.notna(val):
            return float(val)
    if len(bars) < period:
        return float(bars["volume"].mean()) if len(bars) else 0.0
    return float(bars["volume"].tail(period).mean())


def _box_height_pct(box_top: float, box_bottom: float) -> float:
    if box_bottom <= 0:
        return 0.0
    return 100.0 * (box_top - box_bottom) / box_bottom


def _height_valid(box_top: float, box_bottom: float, cfg: DarvasBoxConfig) -> bool:
    pct = _box_height_pct(box_top, box_bottom)
    return cfg.min_box_height_pct <= pct <= cfg.max_box_height_pct


def _compute_hybrid_box(
    bars: pd.DataFrame,
    reversal_high: float,
    reversal_idx: int,
    cfg: DarvasBoxConfig,
) -> tuple[float, float] | None:
    """Hybrid Darvas 3-day reversal + ATR bands."""
    rev_days = cfg.darvas_reversal_days
    if reversal_idx < rev_days - 1:
        return None
    window = bars.iloc[reversal_idx - rev_days + 1 : reversal_idx + 1]
    if len(window) < rev_days:
        return None
    darvas_top = float(window["high"].max())
    darvas_bottom = float(window.iloc[-1]["low"])

    atr_slice = bars.iloc[: reversal_idx + 1]
    atr = compute_atr(atr_slice, cfg.atr_period)
    atr_top = reversal_high + cfg.atr_multiplier * atr
    atr_bottom = reversal_high - cfg.atr_multiplier * atr

    box_top = min(darvas_top, atr_top)
    box_bottom = max(darvas_bottom, atr_bottom)
    if box_top <= box_bottom:
        return None
    if not _height_valid(box_top, box_bottom, cfg):
        return None
    return box_top, box_bottom


def fixed_new_high_lookback_sessions(cfg: AppConfig) -> int:
    """Trading sessions for new-high gate when adaptive mode is off."""
    uf = cfg.universe_filters
    if uf.new_high_lookback_weeks > 0:
        return int(uf.new_high_lookback_weeks * 252 / 52)
    return int(uf.lookback_years_for_52wk_high * 252)


def _new_52wk_high(bars: pd.DataFrame, lookback_sessions: int) -> bool:
    if len(bars) < lookback_sessions + 1:
        return False
    today_high = bars.iloc[-1]["high"]
    prior = bars.iloc[-(lookback_sessions + 1) : -1]["high"].max()
    return today_high > prior


def _inside_box(close: float, box_top: float, box_bottom: float) -> bool:
    return box_bottom <= close <= box_top


def _clear_box_for_rescan(state: BoxState) -> None:
    state.box_state = BoxStateEnum.SCANNING
    state.box_top = None
    state.box_bottom = None
    state.box_start_date = None
    state.box_end_date = None
    state.reversal_high = None
    state.days_in_box = 0
    state.breakout_date = None


def _log_gate_reject(
    debug: ActionDebugLogger | None,
    cfg: AppConfig,
    symbol: str,
    target_date: date,
    reason_code: str,
    message: str,
    **details: object,
) -> None:
    if debug is None or not cfg.backtest.debug_log.include_gate_rejections:
        return
    debug.reject(
        target_date,
        symbol,
        "GATE",
        reason_code,
        message,
        **details,
    )


def _log_box(
    debug: ActionDebugLogger | None,
    symbol: str,
    target_date: date,
    prev: BoxStateEnum,
    new: BoxStateEnum,
    message: str,
    cfg: AppConfig,
    **details: object,
) -> None:
    if debug is None or prev == new:
        return
    if not cfg.backtest.debug_log.include_per_symbol_scanning and new == BoxStateEnum.SCANNING:
        return
    debug.box_transition(
        target_date,
        symbol,
        prev.value,
        new.value,
        message,
        **details,
    )


def update_box_state(
    state: BoxState,
    bars: pd.DataFrame,
    cfg: AppConfig,
    trend_ok: bool,
    has_open_position: bool,
    target_date: date,
    debug: ActionDebugLogger | None = None,
    *,
    new_high_lookback_sessions: int | None = None,
) -> BoxState:
    """Advance Darvas state machine for one symbol on target_date."""
    dcfg = cfg.darvas_box
    min_hist = dcfg.required_price_history_days
    if new_high_lookback_sessions is not None:
        lookback = new_high_lookback_sessions
    else:
        lookback = fixed_new_high_lookback_sessions(cfg)
    prev_state = state.box_state
    symbol = state.symbol

    if len(bars) < min_hist:
        if debug and cfg.backtest.debug_log.include_per_symbol_scanning:
            debug.log(
                target_date,
                "BOX",
                "SKIP",
                f"{symbol}: insufficient price history ({len(bars)} bars, need {min_hist})",
                symbol=symbol,
                details={"bars": len(bars), "required": min_hist},
            )
        return state

    close = float(bars.iloc[-1]["close"])
    vol = int(bars.iloc[-1]["volume"])
    vol_period = dcfg.breakout_volume_sma_period
    vol_sma = volume_sma(bars, vol_period)
    state.volume_sma_20 = vol_sma
    state.last_close = close

    if state.box_state == BoxStateEnum.SCANNING:
        if not trend_ok:
            _log_gate_reject(
                debug,
                cfg,
                symbol,
                target_date,
                "TREND_FAIL",
                f"{symbol}: trend filter failed — cannot start box scan",
            )
            return state
        if cfg.universe_filters.require_new_52wk_high and not _new_52wk_high(bars, lookback):
            prior_max = None
            pct_of_high = None
            if len(bars) >= lookback + 1:
                today_high = float(bars.iloc[-1]["high"])
                prior_max = float(bars.iloc[-(lookback + 1) : -1]["high"].max())
                if prior_max > 0:
                    pct_of_high = round(100.0 * today_high / prior_max, 2)
            _log_gate_reject(
                debug,
                cfg,
                symbol,
                target_date,
                "NO_52WK_HIGH",
                f"{symbol}: no new 52-week high"
                + (f" (high {today_high:.2f} vs prior max {prior_max:.2f}, {pct_of_high}%)" if prior_max else ""),
                today_high=float(bars.iloc[-1]["high"]),
                prior_52w_high=prior_max,
                pct_of_52wh=pct_of_high,
                lookback_sessions=lookback,
            )
            return state
        state.box_state = BoxStateEnum.FORMING
        state.reversal_high = float(bars.iloc[-1]["high"])
        state.box_start_date = target_date
        state.days_in_box = 0
        rev_idx = len(bars) - 1
        bounds = _compute_hybrid_box(bars, state.reversal_high, rev_idx, dcfg)
        if bounds:
            state.box_top, state.box_bottom = bounds
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.FORMING,
                "new 52-week high with valid hybrid box bounds",
                cfg,
                box_top=state.box_top,
                box_bottom=state.box_bottom,
            )
        else:
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.FORMING,
                f"new 52-week high but box bounds invalid "
                f"(need {dcfg.darvas_reversal_days} reversal bars and valid height)",
                cfg,
                reversal_days=dcfg.darvas_reversal_days,
            )
        return state

    if state.box_state == BoxStateEnum.FORMING:
        if state.box_top is None or state.box_bottom is None:
            state.box_state = BoxStateEnum.SCANNING
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                "box top/bottom missing — reset to scanning",
                cfg,
            )
            return state
        rev_idx = len(bars) - 1
        bounds = _compute_hybrid_box(bars, state.reversal_high or close, rev_idx, dcfg)
        if bounds:
            state.box_top, state.box_bottom = bounds
        elif rev_idx < dcfg.darvas_reversal_days - 1:
            if debug:
                debug.log(
                    target_date,
                    "BOX",
                    "REJECT",
                    f"{symbol}: did not satisfy box criteria — "
                    f"need {dcfg.darvas_reversal_days} reversal bars to form box, have {rev_idx + 1}",
                    symbol=symbol,
                    details={"reversal_days_required": dcfg.darvas_reversal_days, "bars_available": rev_idx + 1},
                )
        if not _inside_box(close, state.box_top, state.box_bottom):
            state.box_state = BoxStateEnum.SCANNING
            state.days_in_box = 0
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                f"close {close:.2f} moved outside box [{state.box_bottom:.2f}, {state.box_top:.2f}]",
                cfg,
            )
            return state
        state.days_in_box += 1
        if state.days_in_box > dcfg.max_box_duration_days:
            state.box_state = BoxStateEnum.SCANNING
            state.days_in_box = 0
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                f"box exceeded max duration ({dcfg.max_box_duration_days} days)",
                cfg,
            )
            return state
        if not trend_ok:
            return state
        if state.days_in_box >= dcfg.min_box_duration_days:
            state.box_state = BoxStateEnum.VALIDATED
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.VALIDATED,
                f"box validated after {state.days_in_box} days "
                f"(minimum {dcfg.min_box_duration_days} required)",
                cfg,
                days_in_box=state.days_in_box,
            )
        return state

    if state.box_state == BoxStateEnum.VALIDATED:
        if state.box_top is None or state.box_bottom is None:
            state.box_state = BoxStateEnum.SCANNING
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                "validated state but box bounds missing",
                cfg,
            )
            return state
        rev_idx = len(bars) - 1
        bounds = _compute_hybrid_box(bars, state.reversal_high or close, rev_idx, dcfg)
        if bounds:
            state.box_top, state.box_bottom = bounds
        vol_ok = vol >= vol_sma * dcfg.breakout_volume_multiplier
        price_ok = close > state.box_top
        breakout = price_ok and vol_ok
        if breakout and trend_ok:
            state.box_state = BoxStateEnum.BREAKOUT
            state.breakout_date = target_date
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.BREAKOUT,
                f"breakout: close {close:.2f} > box top {state.box_top:.2f}, "
                f"volume {vol} >= {dcfg.breakout_volume_multiplier}x SMA ({vol_sma:.0f})",
                cfg,
            )
            return state
        if not _inside_box(close, state.box_top, state.box_bottom):
            state.box_state = BoxStateEnum.SCANNING
            state.days_in_box = 0
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                f"price left box before breakout (close={close:.2f})",
                cfg,
            )
            return state
        if price_ok and not vol_ok:
            threshold = vol_sma * dcfg.breakout_volume_multiplier
            _log_gate_reject(
                debug,
                cfg,
                symbol,
                target_date,
                "BREAKOUT_VOLUME_LOW",
                f"{symbol}: close {close:.2f} > box top {state.box_top:.2f} but volume {vol} "
                f"< {dcfg.breakout_volume_multiplier}x SMA ({threshold:.0f})",
                close=close,
                box_top=state.box_top,
                volume=vol,
                vol_sma20=round(vol_sma, 0),
                required_volume=round(threshold, 0),
                vol_ratio=round(vol / vol_sma, 4) if vol_sma else None,
                breakout_volume_multiplier=dcfg.breakout_volume_multiplier,
            )
        return state

    if state.box_state == BoxStateEnum.BREAKOUT:
        if state.box_top is None or state.box_bottom is None:
            _clear_box_for_rescan(state)
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                "breakout state missing box bounds — reset to scanning",
                cfg,
            )
            return state

        rev_idx = len(bars) - 1
        if state.reversal_high:
            bounds = _compute_hybrid_box(bars, state.reversal_high, rev_idx, dcfg)
            if bounds:
                state.box_top, state.box_bottom = bounds

        stale_pct = dcfg.breakout_reset_above_top_pct
        top, bottom = state.box_top, state.box_bottom
        assert top is not None and bottom is not None

        if close < bottom:
            _clear_box_for_rescan(state)
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                f"failed breakout — close {close:.2f} below box bottom {bottom:.2f}",
                cfg,
            )
            return state

        if close > top * (1 + stale_pct / 100):
            _clear_box_for_rescan(state)
            _log_box(
                debug,
                symbol,
                target_date,
                prev_state,
                BoxStateEnum.SCANNING,
                f"stale breakout — close {close:.2f} > {stale_pct:.1f}% above box top {top:.2f}",
                cfg,
            )
            return state

        return state

    return state
