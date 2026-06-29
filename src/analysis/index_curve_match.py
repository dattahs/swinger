"""Find historical NIFTY index windows similar to a reference period."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.vix_curve_match import (
    _composite_score,
    _windows_overlap,
    _window_features,
)

INDEX_SYMBOL = "NIFTY 50"


@dataclass(frozen=True)
class IndexWindowMatch:
    rank: int
    analog_start: date
    analog_end: date
    backtest_start: date
    backtest_end: date
    score: float
    corr_close: float
    corr_returns: float
    dtw_similarity: float
    corr_range: float


def _add_months(d: date, months: int) -> date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, 28)
    return date(y, m, day)


def _calendar_shift_months(start: date, end: date, months: int) -> tuple[date, date]:
    """Shift [start, end] forward by calendar months."""
    return _add_months(start, months), _add_months(end, months)


def load_index_daily_bars(
    db_path: str | Path,
    *,
    symbol: str = INDEX_SYMBOL,
    start: date | None = None,
    end: date | None = None,
) -> pd.DataFrame:
    """Load daily OHLC for an index symbol from the backtest data lake."""
    conn = sqlite3.connect(str(db_path))
    try:
        clauses = ["symbol = ?"]
        params: list[object] = [symbol]
        if start is not None:
            clauses.append("date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("date <= ?")
            params.append(end.isoformat())
        where = " AND ".join(clauses)
        df = pd.read_sql_query(
            f"""
            SELECT date, open, high, low, close, volume
            FROM daily_bars
            WHERE {where}
            ORDER BY date
            """,
            conn,
            params=params,
        )
    finally:
        conn.close()
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def find_index_analogs(
    index_bars: pd.DataFrame,
    *,
    reference_start: date,
    reference_end: date,
    window_sessions: int | None = None,
    top_k: int = 3,
    search_start: date | None = None,
    min_gap_sessions: int = 63,
    backtest_shift_months: int = 18,
) -> list[IndexWindowMatch]:
    """
    Rank historical index windows by shape similarity to reference period.

    Uses z-scored Pearson on close/returns/range/body plus DTW on closes.
    Returns top_k non-overlapping analog windows and backtest ranges shifted
  forward by ``backtest_shift_months`` (default 18).
    """
    bars = index_bars.sort_values("date").reset_index(drop=True)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date

    ref_mask = (bars["date"] >= reference_start) & (bars["date"] <= reference_end)
    ref_df = bars.loc[ref_mask].copy()
    if ref_df.empty:
        raise ValueError(f"No index data in reference window {reference_start} -> {reference_end}")
    actual_sessions = len(ref_df)
    if window_sessions is None or actual_sessions < window_sessions:
        window_sessions = actual_sessions
    if window_sessions < 200:
        raise ValueError(
            f"Reference window has {actual_sessions} sessions; need at least 200 for stable matching"
        )
    ref_df = ref_df.tail(window_sessions).reset_index(drop=True)
    ref_feat = _window_features(ref_df)
    ref_start = ref_df["date"].iloc[0]
    ref_end = ref_df["date"].iloc[-1]

    search = bars.copy()
    if search_start is not None:
        search = search[search["date"] >= search_start]
    search = search[search["date"] < ref_start].reset_index(drop=True)
    if len(search) < window_sessions:
        raise ValueError("Not enough history before reference period for analog search")

    candidates: list[tuple[float, dict[str, float], date, date]] = []
    for i in range(0, len(search) - window_sessions + 1):
        chunk = search.iloc[i : i + window_sessions]
        a_start = chunk["date"].iloc[0]
        a_end = chunk["date"].iloc[-1]
        score, parts = _composite_score(ref_feat, _window_features(chunk))
        if np.isfinite(score):
            candidates.append((score, parts, a_start, a_end))

    candidates.sort(key=lambda x: x[0], reverse=True)

    selected: list[IndexWindowMatch] = []
    for score, parts, a_start, a_end in candidates:
        if _windows_overlap(a_start, a_end, ref_start, ref_end):
            continue
        if any(_windows_overlap(a_start, a_end, m.analog_start, m.analog_end) for m in selected):
            continue
        if selected and min_gap_sessions > 0:
            too_close = False
            for m in selected:
                gap = min(abs((a_start - m.analog_end).days), abs((m.analog_start - a_end).days))
                if gap < min_gap_sessions * 7 // 5:
                    too_close = True
                    break
            if too_close:
                continue

        bt_start, bt_end = _calendar_shift_months(a_start, a_end, backtest_shift_months)
        selected.append(
            IndexWindowMatch(
                rank=len(selected) + 1,
                analog_start=a_start,
                analog_end=a_end,
                backtest_start=bt_start,
                backtest_end=bt_end,
                score=score,
                corr_close=parts["corr_close"],
                corr_returns=parts["corr_returns"],
                dtw_similarity=parts["dtw_similarity"],
                corr_range=parts["corr_range"],
            )
        )
        if len(selected) >= top_k:
            break

    return selected
