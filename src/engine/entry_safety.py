"""Entry safety gates for breakout GTT candidates."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import AppConfig
from src.engine.darvas import volume_sma


def _bar_date(raw: object) -> date:
    if isinstance(raw, date):
        return raw
    if hasattr(raw, "date") and callable(raw.date):
        return raw.date()  # type: ignore[no-any-return]
    return date.fromisoformat(str(raw)[:10])


def _close_sma(bars: pd.DataFrame, period: int) -> float:
    closes = bars["close"].astype(float)
    return float(closes.tail(period).mean())


def check_entry_safety(
    bars: pd.DataFrame,
    cfg: AppConfig,
    *,
    breakout_date: date | None = None,
) -> tuple[bool, str | None]:
    """Entry gates for breakout GTT placement.

    1. Close above SMA on the latest bar (when enabled).
    2. Post-breakout distribution antipattern: >= N consecutive sessions after
       breakout_date where close < open and volume > volume SMA.

    Breakout volume qualification is handled in the Darvas state machine.
    """
    if bars.empty:
        return True, None

    rm = cfg.risk_management
    period = rm.entry_sma_period

    if rm.entry_require_close_above_sma:
        if len(bars) < period:
            return False, "ENTRY_SMA_HISTORY"
        close = float(bars.iloc[-1]["close"])
        sma = _close_sma(bars, period)
        if close <= sma:
            return False, "PRICE_BELOW_SMA"

    if breakout_date is None:
        return True, None

    need = rm.entry_post_breakout_consecutive_red_high_vol
    if need < 1 or len(bars) < period:
        return True, None

    consecutive = 0
    for i in range(len(bars)):
        row = bars.iloc[i]
        session = _bar_date(row["date"])
        if session <= breakout_date:
            continue
        vol_sma = volume_sma(bars.iloc[: i + 1], period)
        red = float(row["close"]) < float(row["open"])
        high_vol = int(row["volume"]) > vol_sma
        if red and high_vol:
            consecutive += 1
            if consecutive >= need:
                return False, "DISTRIBUTION_ANTIPATTERN"
        else:
            consecutive = 0

    return True, None
