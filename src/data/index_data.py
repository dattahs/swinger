"""NIFTY 50 index daily bars from NSE index archives."""

from __future__ import annotations

import sqlite3
import time
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.data.bhavcopy import MONTHS, SESSION, _ensure_session
from src.repository.sqlite import init_data_lake

INDEX_SYMBOL = "NIFTY 50"
INDEX_NAMES = {"NIFTY 50", "Nifty 50", "Nifty50"}
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{ddmmyyyy}.csv"

# NSE index archive name → daily_bars symbol key
SECTOR_INDEX_ARCHIVE_NAMES: dict[str, str] = {
    "Nifty Auto": "NIFTY AUTO",
    "Nifty Bank": "NIFTY BANK",
    "Nifty Consumption": "NIFTY CONSUMPTION",
    "Nifty Energy": "NIFTY ENERGY",
    "Nifty Financial Services": "NIFTY FINANCIAL SERVICES",
    "Nifty FMCG": "NIFTY FMCG",
    "Nifty Healthcare": "NIFTY HEALTHCARE",
    "Nifty Healthcare Index": "NIFTY HEALTHCARE",
    "Nifty Infra": "NIFTY INFRA",
    "Nifty Infrastructure": "NIFTY INFRA",
    "Nifty IT": "NIFTY IT",
    "Nifty Media": "NIFTY MEDIA",
    "Nifty Metal": "NIFTY METAL",
    "Nifty Pharma": "NIFTY PHARMA",
    "Nifty Private Bank": "NIFTY PRIVATE BANK",
    "Nifty PSU Bank": "NIFTY PSU BANK",
    "Nifty Realty": "NIFTY REALTY",
}


def _parse_index_value(raw: object) -> float | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    text = str(raw).strip().replace(",", "")
    if not text or text in {"-", "NA", "N/A", "nan"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _row_from_archive(r: pd.Series, d: date, close_col: str) -> pd.Series | None:
    close = _parse_index_value(r.get(close_col))
    if close is None:
        return None
    open_ = _parse_index_value(r.get("Open Index Value"))
    high = _parse_index_value(r.get("High Index Value"))
    low = _parse_index_value(r.get("Low Index Value"))
    return pd.Series({
        "date": d,
        "open": open_ if open_ is not None else close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
    })


def _download_index_day(d: date) -> pd.Series | None:
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
    row = df[df[name_col].isin(INDEX_NAMES)]
    if row.empty:
        row = df[df[name_col].str.contains("Nifty 50", case=False, na=False)]
    if row.empty:
        return None
    return _row_from_archive(row.iloc[0], d, close_col)


def _download_index_rows(d: date) -> list[tuple[str, pd.Series]]:
    """Return (symbol, ohlc row) for NIFTY 50 and configured sector indices."""
    _ensure_session()
    ddmmyyyy = f"{d.day:02d}{d.month:02d}{d.year}"
    url = INDEX_URL.format(ddmmyyyy=ddmmyyyy)
    resp = SESSION.get(url, timeout=60)
    if resp.status_code != 200 or resp.text.startswith("<!DOCTYPE"):
        nifty = _download_index_day(d)
        return [(INDEX_SYMBOL, nifty)] if nifty is not None else []
    df = pd.read_csv(StringIO(resp.text))
    name_col = "Index Name"
    close_col = "Closing Index Value"
    if name_col not in df.columns:
        nifty = _download_index_day(d)
        return [(INDEX_SYMBOL, nifty)] if nifty is not None else []

    targets = {**SECTOR_INDEX_ARCHIVE_NAMES, "Nifty 50": INDEX_SYMBOL}
    rows: list[tuple[str, pd.Series]] = []
    for archive_name, symbol in targets.items():
        match = df[df[name_col] == archive_name]
        if match.empty and archive_name == "Nifty 50":
            match = df[df[name_col].isin(INDEX_NAMES)]
        if match.empty:
            continue
        parsed = _row_from_archive(match.iloc[0], d, close_col)
        if parsed is None:
            continue
        rows.append((symbol, parsed))
    return rows


def download_and_ingest_nifty50(
    db_path: Path,
    start: date,
    end: date,
    *,
    pause_sec: float = 0.35,
    include_sector_indices: bool = True,
) -> int:
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path)
    count = 0
    d = start
    while d <= end:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        if include_sector_indices:
            day_rows = _download_index_rows(d)
        else:
            row = _download_index_day(d)
            day_rows = [(INDEX_SYMBOL, row)] if row is not None else []
        for symbol, row in day_rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, turnover_inr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    d.isoformat(),
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    0,
                    0.0,
                ),
            )
            count += 1
        time.sleep(pause_sec)
        d += timedelta(days=1)
    conn.commit()
    conn.close()
    return count
