"""Find historical India VIX windows similar to a reference period."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VixWindowMatch:
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


def _zscore(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    sd = float(x.std())
    if sd < 1e-12:
        return x - float(x.mean())
    return (x - float(x.mean())) / sd


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) != len(b) or len(a) < 3:
        return float("nan")
    a = _zscore(a)
    b = _zscore(b)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1e-12:
        return float("nan")
    return float(np.dot(a, b) / denom)


def _dtw_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Classic DTW on equal-length series (O(n^2))."""
    n = len(a)
    if n != len(b) or n == 0:
        return float("inf")
    d = np.full((n + 1, n + 1), np.inf)
    d[0, 0] = 0.0
    for i in range(1, n + 1):
        ai = a[i - 1]
        for j in range(1, n + 1):
            cost = (ai - b[j - 1]) ** 2
            d[i, j] = cost + min(d[i - 1, j], d[i, j - 1], d[i - 1, j - 1])
    return float(np.sqrt(d[n, n] / n))


def _window_features(df: pd.DataFrame) -> dict[str, np.ndarray]:
    close = df["close"].astype(float).to_numpy()
    open_ = df["open"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    low = df["low"].astype(float).to_numpy()
    safe = np.maximum(close, 1e-6)
    returns = np.diff(close, prepend=close[0]) / safe
    rng = (high - low) / safe
    body = (close - open_) / safe
    return {
        "close": close,
        "returns": returns,
        "range": rng,
        "body": body,
    }


def _composite_score(ref: dict[str, np.ndarray], cand: dict[str, np.ndarray]) -> tuple[float, dict[str, float]]:
    corr_close = _pearson(ref["close"], cand["close"])
    corr_returns = _pearson(ref["returns"], cand["returns"])
    corr_range = _pearson(ref["range"], cand["range"])
    corr_body = _pearson(ref["body"], cand["body"])
    dtw = _dtw_distance(_zscore(ref["close"]), _zscore(cand["close"]))
    dtw_sim = 1.0 / (1.0 + dtw)

    parts = {
        "corr_close": corr_close,
        "corr_returns": corr_returns,
        "corr_range": corr_range,
        "corr_body": corr_body,
        "dtw_similarity": dtw_sim,
    }
    weights = {
        "corr_close": 0.35,
        "corr_returns": 0.20,
        "corr_range": 0.15,
        "corr_body": 0.10,
        "dtw_similarity": 0.20,
    }
    score = 0.0
    weight_sum = 0.0
    for key, w in weights.items():
        val = parts[key]
        if np.isfinite(val):
            score += w * val
            weight_sum += w
    if weight_sum <= 0:
        return float("nan"), parts
    return score / weight_sum, parts


def _calendar_shift_one_year(start: date, end: date) -> tuple[date, date]:
    """Shift [start, end] forward by one calendar year (Jun Y -> Jun Y+1)."""
    return date(start.year + 1, start.month, start.day), date(end.year + 1, end.month, end.day)


def _windows_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return not (a_end < b_start or b_end < a_start)


def find_vix_analogs(
    vix: pd.DataFrame,
    *,
    reference_start: date,
    reference_end: date,
    window_sessions: int | None = 252,
    top_k: int = 3,
    search_start: date | None = None,
    min_gap_sessions: int = 63,
) -> list[VixWindowMatch]:
    """
    Rank historical VIX windows by shape similarity to reference period.

    Uses z-scored Pearson correlation on close/returns/range/body plus DTW on closes.
    Returns top_k non-overlapping analog windows and their subsequent-year backtest ranges.
    """
    vix = vix.sort_values("date").reset_index(drop=True)
    vix["date"] = pd.to_datetime(vix["date"]).dt.date

    ref_mask = (vix["date"] >= reference_start) & (vix["date"] <= reference_end)
    ref_df = vix.loc[ref_mask].copy()
    if ref_df.empty:
        raise ValueError(f"No VIX data in reference window {reference_start} -> {reference_end}")
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

    search = vix.copy()
    if search_start is not None:
        search = search[search["date"] >= search_start]
    # Candidates must end strictly before reference starts (no lookahead).
    search = search[search["date"] < ref_start].reset_index(drop=True)
    if len(search) < window_sessions:
        raise ValueError("Not enough history before reference period for analog search")

    candidates: list[tuple[float, dict[str, float], date, date]] = []
    dates = search["date"].tolist()
    for i in range(0, len(search) - window_sessions + 1):
        chunk = search.iloc[i : i + window_sessions]
        a_start = chunk["date"].iloc[0]
        a_end = chunk["date"].iloc[-1]
        score, parts = _composite_score(ref_feat, _window_features(chunk))
        if np.isfinite(score):
            candidates.append((score, parts, a_start, a_end))

    candidates.sort(key=lambda x: x[0], reverse=True)

    selected: list[VixWindowMatch] = []
    for score, parts, a_start, a_end in candidates:
        if _windows_overlap(a_start, a_end, ref_start, ref_end):
            continue
        if any(_windows_overlap(a_start, a_end, m.analog_start, m.analog_end) for m in selected):
            continue
        # Require gap from already-selected windows.
        if selected and min_gap_sessions > 0:
            too_close = False
            for m in selected:
                gap = min(abs((a_start - m.analog_end).days), abs((m.analog_start - a_end).days))
                if gap < min_gap_sessions * 7 // 5:
                    too_close = True
                    break
            if too_close:
                continue

        bt_start, bt_end = _calendar_shift_one_year(a_start, a_end)
        selected.append(
            VixWindowMatch(
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
