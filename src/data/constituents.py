"""NIFTY 500 membership and sector reference data."""

from __future__ import annotations

import sqlite3
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from src.data.nse_client import nse_get
from src.data.sector_etfs import SECTOR_ETF_SYMBOLS
from src.repository.sqlite import init_data_lake

NIFTY500_CSV_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"


def fetch_nifty500_symbols() -> list[str]:
    """Download current NIFTY 500 constituent symbols."""
    return sorted(fetch_nifty500_industry_map().keys())


def fetch_nifty500_industry_map() -> dict[str, str]:
    """Symbol → NSE Industry from official NIFTY 500 CSV."""
    resp = requests.get(
        NIFTY500_CSV_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.nseindia.com/",
        },
        timeout=60,
    )
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    sym_col = next(c for c in df.columns if c.lower() in ("symbol", "symbols"))
    ind_col = next(c for c in df.columns if c.lower() == "industry")
    out: dict[str, str] = {}
    for _, row in df.iterrows():
        sym = str(row[sym_col]).strip()
        industry = str(row[ind_col]).strip()
        if sym and industry and industry.lower() != "nan":
            out[sym] = industry
    return out


def fetch_nifty500_from_api() -> list[str]:
    """Fallback: NSE equity-stockIndices API."""
    data = nse_get("/api/equity-stockIndices", params={"index": "NIFTY 500"})
    rows = data.get("data", [])
    return sorted({r["symbol"] for r in rows if r.get("symbol")})


def ingest_nifty500_membership(
    db_path: Path,
    *,
    effective_date: date | None = None,
    symbols: list[str] | None = None,
) -> int:
    """
    Load NIFTY 500 membership. Uses current list with effective_date default 2016-09-01.
    Note: survivorship-biased until monthly historical archives are parsed (BACKTEST_PLAN §5.2).
    """
    init_data_lake(db_path)
    effective_date = effective_date or date(2016, 9, 1)
    if symbols is None:
        try:
            symbols = fetch_nifty500_symbols()
        except Exception:
            symbols = fetch_nifty500_from_api()
    conn = sqlite3.connect(db_path)
    for sym in symbols:
        conn.execute(
            "INSERT OR IGNORE INTO nifty500_membership (symbol, effective_date) VALUES (?, ?)",
            (sym, effective_date.isoformat()),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM nifty500_membership").fetchone()[0]
    conn.close()
    return count


def ingest_sector_map(db_path: Path, symbols: list[str] | None = None) -> int:
    """Map symbols to NSE industry; API first, then official NIFTY 500 CSV."""
    init_data_lake(db_path)
    industry_map: dict[str, str] = {}
    if symbols is None:
        try:
            symbols = fetch_nifty500_symbols()
        except Exception:
            symbols = []
    try:
        industry_map = fetch_nifty500_industry_map()
        if symbols is None or not symbols:
            symbols = sorted(industry_map.keys())
    except Exception as exc:
        print(f"  sector CSV unavailable ({exc})")

    conn = sqlite3.connect(db_path)
    api_ok = False
    try:
        data = nse_get("/api/equity-stockIndices", params={"index": "NIFTY 500"})
        for row in data.get("data", []):
            sym = row.get("symbol")
            sector = row.get("industry") or row.get("sector")
            if sym and sector:
                conn.execute(
                    "INSERT OR REPLACE INTO sector_map (symbol, sector) VALUES (?, ?)",
                    (sym, sector),
                )
        api_ok = True
    except Exception as exc:
        print(f"  sector API unavailable ({exc}), using NIFTY 500 CSV industries")

    if not api_ok:
        for sym in symbols:
            sector = industry_map.get(sym, "UNKNOWN")
            conn.execute(
                "INSERT OR REPLACE INTO sector_map (symbol, sector) VALUES (?, ?)",
                (sym, sector),
            )
    etf_sector_labels = {
        "AUTOBEES": "Automobile",
        "BANKBEES": "Banks",
        "CONSUMBEES": "Consumption",
        "INFRABEES": "Infrastructure",
        "ITBEES": "IT",
        "PHARMABEES": "Pharmaceuticals",
        "PSUBNKBEES": "PSU Banks",
    }
    for etf in SECTOR_ETF_SYMBOLS:
        conn.execute(
            "INSERT OR REPLACE INTO sector_map (symbol, sector) VALUES (?, ?)",
            (etf, etf_sector_labels.get(etf, etf)),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM sector_map").fetchone()[0]
    conn.close()
    return count
