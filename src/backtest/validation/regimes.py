"""Market regime classification from index 252-day return."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.repository.sqlite import SqliteDataLake

BULL_THRESHOLD = 0.05
BEAR_THRESHOLD = -0.05
LOOKBACK_SESSIONS = 252


def classify_regime(return_252d: float | None) -> str | None:
    if return_252d is None:
        return None
    if return_252d > BULL_THRESHOLD:
        return "BULL"
    if return_252d < BEAR_THRESHOLD:
        return "BEAR"
    return "SIDEWAYS"


def build_regime_map(
    data_lake: SqliteDataLake,
    index_symbol: str,
    start: date,
    end: date,
    *,
    lookback_sessions: int = LOOKBACK_SESSIONS,
) -> dict[date, str]:
    """Map each trading day to BULL / BEAR / SIDEWAYS from trailing index return."""
    warmup_start = data_lake.get_trading_days(
        date(start.year - 2, start.month, start.day), start
    )
    load_from = warmup_start[0] if warmup_start else start
    bars = data_lake.get_daily_bars(index_symbol, end, lookback_sessions + len(warmup_start) + 600)
    if bars.empty:
        return {}

    bars = bars.sort_values("date").reset_index(drop=True)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    bars["close"] = bars["close"].astype(float)
    bars["ret_252"] = bars["close"].pct_change(lookback_sessions)

    trading_days = data_lake.get_trading_days(start, end)
    by_date = {row["date"]: row["ret_252"] for _, row in bars.iterrows()}
    regime_map: dict[date, str] = {}
    for d in trading_days:
        ret = by_date.get(d)
        label = classify_regime(ret)
        if label is not None:
            regime_map[d] = label
    return regime_map


def regime_day_counts(regime_map: dict[date, str]) -> dict[str, int]:
    counts: dict[str, int] = {"BULL": 0, "BEAR": 0, "SIDEWAYS": 0}
    for label in regime_map.values():
        counts[label] = counts.get(label, 0) + 1
    return counts
