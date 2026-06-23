"""Sector relative strength vs NIFTY 50 — REQUIREMENTS v1.2 / PRD v4 tiebreaker."""

from __future__ import annotations

import pandas as pd

from src.data.sector_etfs import sector_index_symbol


def trailing_return(bars: pd.DataFrame, lookback_days: int) -> float | None:
    if bars.empty or len(bars) < lookback_days + 1:
        return None
    start = float(bars.iloc[-lookback_days - 1]["close"])
    end = float(bars.iloc[-1]["close"])
    if start <= 0:
        return None
    return end / start - 1.0


def compute_sector_rs_percentiles(
    sector_labels: set[str],
    sector_index_bars: dict[str, pd.DataFrame],
    nifty_bars: pd.DataFrame,
    lookback_days: int,
) -> dict[str, float]:
    """
    Rank sectors by trailing return minus NIFTY 50 over lookback_days.
    Returns sector_label → percentile in [0, 100].
    """
    nifty_ret = trailing_return(nifty_bars, lookback_days)
    if nifty_ret is None:
        return {label: 0.0 for label in sector_labels}

    rs_by_label: dict[str, float] = {}
    for label in sector_labels:
        index_sym = sector_index_symbol(label)
        bars = sector_index_bars.get(index_sym or "", pd.DataFrame())
        if bars.empty and index_sym:
            bars = sector_index_bars.get(label, pd.DataFrame())
        sector_ret = trailing_return(bars, lookback_days)
        rs_by_label[label] = (sector_ret - nifty_ret) if sector_ret is not None else 0.0

    ordered = sorted(rs_by_label.items(), key=lambda item: item[1])
    n = len(ordered)
    if n == 0:
        return {}
    if n == 1:
        return {ordered[0][0]: 50.0}

    percentiles: dict[str, float] = {}
    for rank, (label, _) in enumerate(ordered):
        percentiles[label] = 100.0 * rank / (n - 1)
    return percentiles
