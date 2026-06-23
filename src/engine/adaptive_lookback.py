"""Adaptive new-high lookback from broad-market position vs SMA."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from src.config import AdaptiveNewHighLookbackConfig, AppConfig


def _bar_date(raw: object) -> date:
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw)[:10])


@dataclass
class LookbackCadenceState:
    frozen_sessions: int | None = None
    frozen_meta: dict[str, float | str] | None = None
    last_spread_pct: float | None = None
    last_above_sma: bool | None = None
    sessions_since_refresh: int = 0
    static_done: bool = False


_cadence_state: LookbackCadenceState | None = None


def reset_lookback_cadence_state() -> None:
    """Reset per-backtest cadence freeze state."""
    global _cadence_state
    _cadence_state = LookbackCadenceState()


def index_spread_pct_series(index_bars: pd.DataFrame, sma_period: int) -> pd.Series:
    """(close - SMA) / SMA * 100 for each bar."""
    if index_bars.empty or len(index_bars) < sma_period:
        return pd.Series(dtype=float)
    closes = index_bars["close"].astype(float)
    dates = index_bars["date"].map(_bar_date)
    spreads: list[float] = []
    for i in range(len(index_bars)):
        if i + 1 < sma_period:
            spreads.append(np.nan)
            continue
        sma = float(closes.iloc[i + 1 - sma_period : i + 1].mean())
        close = float(closes.iloc[i])
        spreads.append(100.0 * (close - sma) / sma if sma > 0 else 0.0)
    return pd.Series(spreads, index=dates)


def calibrate_spread_percentiles(
    spread_series: pd.Series,
    as_of: date,
    *,
    calibration_sessions: int,
    low_percentile: float,
    high_percentile: float,
) -> tuple[float, float, float]:
    """Return (spread_today, p_low, p_high) using only history on or before as_of."""
    valid = spread_series.dropna()
    if valid.empty:
        return 0.0, -5.0, 5.0
    hist = valid[valid.index <= as_of]
    if len(hist) < 50:
        today = float(hist.iloc[-1]) if len(hist) else 0.0
        return today, -5.0, 5.0
    window = hist.tail(calibration_sessions)
    today = float(hist.loc[hist.index <= as_of].iloc[-1])
    p_low = float(np.percentile(window, low_percentile))
    p_high = float(np.percentile(window, high_percentile))
    if p_high <= p_low:
        p_high = p_low + 1.0
    return today, p_low, p_high


def normalized_bull_factor(spread_pct: float, p_low: float, p_high: float) -> float:
    """0 = bearish (strict long lookback), 1 = bullish (relaxed short lookback)."""
    if p_high <= p_low:
        return 0.5
    return float(np.clip((spread_pct - p_low) / (p_high - p_low), 0.0, 1.0))


def lookback_weeks_from_factor(factor: float, cfg: AdaptiveNewHighLookbackConfig) -> float:
    """Interpolate weeks between max (bear) and min (bull)."""
    return cfg.max_lookback_weeks - factor * (cfg.max_lookback_weeks - cfg.min_lookback_weeks)


def weeks_to_sessions(weeks: float) -> int:
    return max(1, int(round(weeks * 252 / 52)))


def _close_above_sma(index_bars: pd.DataFrame, sma_period: int, as_of: date) -> bool:
    hist = index_bars[index_bars["date"].map(_bar_date) <= as_of]
    if len(hist) < sma_period:
        return True
    closes = hist["close"].astype(float)
    sma = float(closes.iloc[-sma_period:].mean())
    close = float(closes.iloc[-1])
    return close >= sma


def _compute_adaptive_sessions(
    index_bars: pd.DataFrame,
    cfg: AppConfig,
    as_of: date,
) -> tuple[int, dict[str, float | str]]:
    uf = cfg.universe_filters
    acfg = uf.adaptive_new_high_lookback
    cal_sessions = int(acfg.calibration_years * 252)
    spreads = index_spread_pct_series(index_bars, acfg.sma_period)
    spread_today, p_low, p_high = calibrate_spread_percentiles(
        spreads,
        as_of,
        calibration_sessions=cal_sessions,
        low_percentile=acfg.low_percentile,
        high_percentile=acfg.high_percentile,
    )
    factor = normalized_bull_factor(spread_today, p_low, p_high)
    weeks = lookback_weeks_from_factor(factor, acfg)
    sessions = weeks_to_sessions(weeks)
    return sessions, {
        "mode": "adaptive",
        "spread_pct": round(spread_today, 3),
        "p_low": round(p_low, 3),
        "p_high": round(p_high, 3),
        "bull_factor": round(factor, 4),
        "lookback_weeks": round(weeks, 2),
        "lookback_sessions": sessions,
    }


def _apply_cadence(
    fresh_sessions: int,
    fresh_meta: dict[str, float | str],
    index_bars: pd.DataFrame,
    cfg: AppConfig,
    as_of: date,
) -> tuple[int, dict[str, float | str]]:
    acfg = cfg.universe_filters.adaptive_new_high_lookback
    cadence = acfg.recalibration_cadence or "daily"
    if cadence == "daily":
        return fresh_sessions, fresh_meta

    global _cadence_state
    if _cadence_state is None:
        _cadence_state = LookbackCadenceState()
    state = _cadence_state

    if cadence == "static":
        if not state.static_done:
            state.frozen_sessions = fresh_sessions
            state.frozen_meta = dict(fresh_meta)
            state.static_done = True
        meta = dict(state.frozen_meta or fresh_meta)
        meta["cadence"] = "static"
        return int(state.frozen_sessions or fresh_sessions), meta

    should_refresh = state.frozen_sessions is None
    if not should_refresh and cadence == "weekly":
        should_refresh = state.sessions_since_refresh >= 5
    elif not should_refresh and cadence == "monthly":
        should_refresh = state.sessions_since_refresh >= 21
    elif not should_refresh and cadence == "event_nifty_sma_cross":
        above = _close_above_sma(index_bars, acfg.sma_period, as_of)
        if state.last_above_sma is not None and above != state.last_above_sma:
            should_refresh = True
        state.last_above_sma = above
    elif not should_refresh and cadence == "event_spread_jump":
        spread = float(fresh_meta.get("spread_pct", 0.0))
        if (
            state.last_spread_pct is not None
            and abs(spread - state.last_spread_pct) >= acfg.spread_jump_threshold_pct
        ):
            should_refresh = True
        state.last_spread_pct = spread

    if should_refresh:
        state.frozen_sessions = fresh_sessions
        state.frozen_meta = dict(fresh_meta)
        state.sessions_since_refresh = 0

    state.sessions_since_refresh += 1
    meta = dict(state.frozen_meta or fresh_meta)
    meta["cadence"] = cadence
    meta["cadence_frozen"] = not should_refresh
    return int(state.frozen_sessions or fresh_sessions), meta


def resolve_new_high_lookback_sessions(
    index_bars: pd.DataFrame,
    cfg: AppConfig,
    as_of: date,
) -> tuple[int, dict[str, float | str]]:
    """Sessions for new-high gate; metadata for logging."""
    uf = cfg.universe_filters
    acfg = uf.adaptive_new_high_lookback
    if not uf.require_new_52wk_high:
        return 0, {"mode": "disabled"}
    if not acfg.enabled:
        sessions = (
            weeks_to_sessions(uf.new_high_lookback_weeks)
            if uf.new_high_lookback_weeks > 0
            else int(uf.lookback_years_for_52wk_high * 252)
        )
        return sessions, {"mode": "fixed", "weeks": uf.new_high_lookback_weeks}

    fresh_sessions, fresh_meta = _compute_adaptive_sessions(index_bars, cfg, as_of)
    return _apply_cadence(fresh_sessions, fresh_meta, index_bars, cfg, as_of)
