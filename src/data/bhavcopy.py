"""NSE Bhavcopy download and ingest."""

from __future__ import annotations

import io
import sqlite3
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from src.repository.sqlite import init_data_lake

ARCHIVE_URL = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/"
    "cm{dd}{mon}{year}bhav.csv.zip"
)
FULL_BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dd}{mm}{yyyy}.csv"
# NSE historical EQUITIES ZIP ends ~2024-07-05; sec_bhavdata_full covers later dates.
LEGACY_BHAV_END = date(2024, 7, 5)
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
})


def _archive_url(d: date) -> str:
    mon = MONTHS[d.month - 1]
    return ARCHIVE_URL.format(year=d.year, mon=mon, dd=f"{d.day:02d}")


def _ensure_session() -> None:
    if not SESSION.cookies:
        SESSION.get("https://www.nseindia.com", timeout=30)


def _parse_bhavcopy_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize legacy ZIP, UDiff, and sec_bhavdata_full column names."""
    df = df.rename(columns={c: c.strip() for c in df.columns})
    colmap = {
        "SYMBOL": "symbol",
        "Symbol": "symbol",
        "SERIES": "series",
        "Series": "series",
        "OPEN": "open",
        "Open": "open",
        "OPEN_PRICE": "open",
        "HIGH": "high",
        "High": "high",
        "HIGH_PRICE": "high",
        "LOW": "low",
        "Low": "low",
        "LOW_PRICE": "low",
        "CLOSE": "close",
        "Close": "close",
        "TOTTRDQTY": "volume",
        "TotTrdQty": "volume",
        "TTL_TRD_QNTY": "volume",
        "TOTTRDVAL": "turnover_inr",
        "Turnover": "turnover_inr",
        "TURNOVER_LACS": "turnover_lacs",
        "DATE1": "date",
        "Date": "date",
        "TIMESTAMP": "date",
    }
    df = df.rename(columns={k: v for k, v in colmap.items() if k in df.columns})
    if "close" not in df.columns:
        for src in ("CLOSE_PRICE", "LAST_PRICE"):
            if src in df.columns:
                df = df.rename(columns={src: "close"})
                break
    if "turnover_lacs" in df.columns and "turnover_inr" not in df.columns:
        df["turnover_inr"] = pd.to_numeric(df["turnover_lacs"], errors="coerce") * 100_000.0
    if "symbol" in df.columns:
        df["symbol"] = df["symbol"].astype(str).str.strip()
    if "series" in df.columns:
        df["series"] = df["series"].astype(str).str.strip()
        df = df[df["series"].str.upper() == "EQ"]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce").dt.date
    elif "date" not in df.columns and len(df):
        pass
    for col in ("open", "high", "low", "close", "volume", "turnover_inr"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["symbol", "close"])


def _read_bhavcopy_file(path: Path, fallback_date: date | None = None) -> pd.DataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    else:
        # Skip HTML error pages saved with .csv extension
        head = path.read_bytes()[:20]
        if head.startswith(b"<!DOCTYPE") or head.startswith(b"<html"):
            raise ValueError("HTML error page, not CSV")
        df = pd.read_csv(path)
    df = _parse_bhavcopy_df(df)
    if "date" not in df.columns or df["date"].isna().all():
        if fallback_date:
            df["date"] = fallback_date
    return df


def _full_bhav_filename(d: date) -> str:
    return f"sec_bhavdata_full_{d.day:02d}{d.month:02d}{d.year}.csv"


def download_bhavcopy_day(d: date, raw_dir: Path) -> Path | None:
    """Download one day Bhavcopy from NSE archives (legacy ZIP or sec_bhavdata_full CSV)."""
    _ensure_session()
    mon = MONTHS[d.month - 1]
    legacy_out = raw_dir / f"cm{d.day:02d}{mon}{d.year}bhav.csv.zip"
    full_out = raw_dir / _full_bhav_filename(d)
    for out in (legacy_out, full_out):
        if out.exists() and out.stat().st_size > 1000:
            return out

    if d <= LEGACY_BHAV_END:
        resp = SESSION.get(_archive_url(d), timeout=90)
        if resp.status_code == 200 and resp.content[:2] == b"PK":
            legacy_out.write_bytes(resp.content)
            return legacy_out

    url = FULL_BHAV_URL.format(dd=f"{d.day:02d}", mm=f"{d.month:02d}", yyyy=d.year)
    resp = SESSION.get(url, timeout=90)
    if resp.status_code != 200:
        if d <= LEGACY_BHAV_END:
            return None
        return None
    text = resp.text.lstrip()
    if text.startswith("<!DOCTYPE") or text.startswith("<html"):
        return None
    full_out.write_text(text, encoding="utf-8")
    return full_out


def download_bhavcopy_range(
    start: date,
    end: date,
    raw_dir: Path,
    *,
    pause_sec: float = 0.4,
    skip_existing: bool = True,
) -> tuple[int, int]:
    """Download daily CM Bhavcopy ZIP files from nsearchives.nseindia.com."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    failed = 0
    d = start
    while d <= end:
        if d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        mon = MONTHS[d.month - 1]
        legacy_out = raw_dir / f"cm{d.day:02d}{mon}{d.year}bhav.csv.zip"
        full_out = raw_dir / _full_bhav_filename(d)
        if skip_existing and (
            (legacy_out.exists() and legacy_out.stat().st_size > 1000)
            or (full_out.exists() and full_out.stat().st_size > 1000)
        ):
            skipped += 1
            d += timedelta(days=1)
            continue
        result = download_bhavcopy_day(d, raw_dir)
        if result:
            downloaded += 1
            if downloaded % 50 == 0:
                print(f"  downloaded {downloaded} (latest {d.isoformat()})")
        else:
            failed += 1
            print(f"  FAIL {d.isoformat()}")
        time.sleep(pause_sec)
        d += timedelta(days=1)
    if failed:
        print(f"  failed days: {failed}")
    return downloaded, skipped


def ingest_bhavcopy_dir(
    raw_dir: Path,
    db_path: Path,
    *,
    symbols: set[str] | None = None,
    batch_size: int = 5000,
) -> int:
    """Load all Bhavcopy ZIP/CSV files in raw_dir into daily_bars. Returns row count."""
    init_data_lake(db_path)
    conn = sqlite3.connect(db_path)
    total = 0
    files = sorted(list(raw_dir.glob("*.zip")) + list(raw_dir.glob("*.csv")))
    if not files:
        raise FileNotFoundError(f"No bhavcopy files in {raw_dir}")

    for fpath in files:
        try:
            fallback = None
            name = fpath.stem.replace(".csv", "")
            if name.startswith("cm") and len(name) >= 11:
                dd = int(name[2:4])
                mon = name[4:7]
                yr = int(name[7:11])
                mi = MONTHS.index(mon) + 1
                fallback = date(yr, mi, dd)
            elif name.startswith("sec_bhavdata_full_"):
                suffix = name[len("sec_bhavdata_full_") :]
                if len(suffix) == 8 and suffix.isdigit():
                    dd = int(suffix[0:2])
                    mm = int(suffix[2:4])
                    yr = int(suffix[4:8])
                    fallback = date(yr, mm, dd)
            df = _read_bhavcopy_file(fpath, fallback_date=fallback)
        except Exception as exc:
            print(f"  skip {fpath.name}: {exc}")
            continue
        if symbols:
            df = df[df["symbol"].isin(symbols)]
        if df.empty:
            continue
        rows = [
            (
                r.symbol,
                r.date.isoformat(),
                float(r.open),
                float(r.high),
                float(r.low),
                float(r.close),
                int(r.volume) if pd.notna(r.volume) else 0,
                float(r.turnover_inr) if pd.notna(r.turnover_inr) else 0.0,
            )
            for r in df.itertuples(index=False)
        ]
        for i in range(0, len(rows), batch_size):
            conn.executemany(
                """
                INSERT OR REPLACE INTO daily_bars
                (symbol, date, open, high, low, close, volume, turnover_inr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows[i : i + batch_size],
            )
        conn.commit()
        total += len(rows)
        print(f"  ingested {fpath.name}: {len(rows)} rows")
    conn.close()
    return total


def build_trading_calendar_from_bars(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR IGNORE INTO trading_calendar (date, is_trading_day)
        SELECT DISTINCT date, 1 FROM daily_bars WHERE symbol != 'NIFTY 50'
        """
    )
    count = conn.execute("SELECT COUNT(*) FROM trading_calendar").fetchone()[0]
    conn.commit()
    conn.close()
    return count
