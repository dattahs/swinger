"""India VIX daily bars from NSE index archives."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.data.bhavcopy import SESSION, _ensure_session
from src.data.index_data import INDEX_URL, _parse_index_value, _row_from_archive
from src.repository.sqlite import init_data_lake

VIX_SYMBOL = "INDIA VIX"
VIX_ARCHIVE_NAMES = {"India VIX", "INDIA VIX", "India Vix"}


def _download_vix_day(d: date) -> pd.Series | None:
    _ensure_session()
    ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year}"
    url = INDEX_URL.format(ddmmyyyy=ddmmyyyy)
    resp = SESSION.get(url, timeout=60)
    if resp.status_code != 200 or resp.text.startswith("<!DOCTYPE"):
        return None
    df = pd.read_csv(StringIO(resp.text))
    name_col = "Index Name"
    close_col = "Closing Index Value"
    if name_col not in df.columns:
        return None
    row = df[df[name_col].isin(VIX_ARCHIVE_NAMES)]
    if row.empty:
        row = df[df[name_col].str.contains("India VIX", case=False, na=False)]
    if row.empty:
        return None
    return _row_from_archive(row.iloc[0], d, close_col)


def download_india_vix_yahoo(start: date, end: date) -> pd.DataFrame:
    """Bulk download India VIX OHLC via Yahoo Finance (^INDIAVIX)."""
    try:
        import yfinance as yf
    except ImportError as exc:
        raise ImportError("yfinance required for bulk VIX download: pip install yfinance") from exc

    end_exclusive = end + timedelta(days=1)
    hist = yf.Ticker("^INDIAVIX").history(
        start=start.isoformat(),
        end=end_exclusive.isoformat(),
        auto_adjust=False,
    )
    if hist.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])

    rows: list[dict] = []
    for ts, row in hist.iterrows():
        d = ts.date() if hasattr(ts, "date") else pd.Timestamp(ts).date()
        if d < start or d > end:
            continue
        rows.append(
            {
                "date": d,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
            }
        )
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def download_india_vix(
    start: date,
    end: date,
    *,
    pause_sec: float = 0.35,
) -> pd.DataFrame:
    """Download India VIX OHLC for each weekday in [start, end]."""
    rows: list[dict] = []
    d = start
    while d <= end:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        parsed = _download_vix_day(d)
        if parsed is not None:
            rows.append(
                {
                    "date": d,
                    "open": float(parsed["open"]),
                    "high": float(parsed["high"]),
                    "low": float(parsed["low"]),
                    "close": float(parsed["close"]),
                }
            )
        time.sleep(pause_sec)
        d += timedelta(days=1)
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True)


def vix_cache_path(repo_root: Path) -> Path:
    return repo_root / "data" / "processed" / "india_vix_daily.csv"


def load_or_download_vix(
    repo_root: Path,
    start: date,
    end: date,
    *,
    refresh: bool = False,
) -> pd.DataFrame:
    """Load cached CSV or download missing India VIX history."""
    cache = vix_cache_path(repo_root)
    cache.parent.mkdir(parents=True, exist_ok=True)

    existing = pd.DataFrame(columns=["date", "open", "high", "low", "close"])
    if cache.is_file() and not refresh:
        existing = pd.read_csv(cache, parse_dates=["date"])
        existing["date"] = pd.to_datetime(existing["date"]).dt.date

    if existing.empty:
        try:
            merged = download_india_vix_yahoo(start, end)
        except ImportError:
            merged = download_india_vix(start, end, pause_sec=0.35)
    else:
        frames = [existing]
        if start < existing["date"].min():
            try:
                frames.append(download_india_vix_yahoo(start, existing["date"].min() - timedelta(days=1)))
            except ImportError:
                frames.append(
                    download_india_vix(start, existing["date"].min() - timedelta(days=1), pause_sec=0.35)
                )
        if end > existing["date"].max():
            try:
                frames.append(download_india_vix_yahoo(existing["date"].max() + timedelta(days=1), end))
            except ImportError:
                frames.append(
                    download_india_vix(existing["date"].max() + timedelta(days=1), end, pause_sec=0.35)
                )
        merged = pd.concat(frames, ignore_index=True)
        merged = merged.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)

    merged.to_csv(cache, index=False)
    mask = (merged["date"] >= start) & (merged["date"] <= end)
    return merged.loc[mask].reset_index(drop=True)


def ingest_vix_to_db(db_path: Path, df: pd.DataFrame) -> int:
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path)
    count = 0
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_bars
            (symbol, date, open, high, low, close, volume, turnover_inr)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                VIX_SYMBOL,
                pd.Timestamp(row["date"]).date().isoformat(),
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                0,
                0.0,
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count
