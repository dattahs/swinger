"""In-memory warm cache for backtest sessions — bars, sectors, fundamentals."""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.data.sector_etfs import SECTOR_INDEX_SYMBOLS, scan_universe_extras

if TYPE_CHECKING:
    from src.config import AppConfig
    from src.repository.sqlite import SqliteDataLake


def enrich_bar_indicators(
    df: pd.DataFrame,
    *,
    atr_period: int = 20,
    vol_period: int = 20,
) -> pd.DataFrame:
    """Add rolling ATR and volume SMA columns (computed once per symbol)."""
    if df.empty:
        return df
    out = df.sort_values("date").reset_index(drop=True).copy()
    high = out["high"].to_numpy(dtype=float)
    low = out["low"].to_numpy(dtype=float)
    close = out["close"].to_numpy(dtype=float)
    prev_close = np.empty_like(close)
    prev_close[0] = close[0]
    prev_close[1:] = close[:-1]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr_col = f"atr_{atr_period}"
    out[atr_col] = pd.Series(tr).rolling(atr_period, min_periods=1).mean()
    out["vol_sma_20"] = out["volume"].rolling(vol_period, min_periods=1).mean()
    return out


class BacktestDataCache:
    """Warm-loaded reference data and OHLCV slices for a backtest date range."""

    def __init__(self, lake: SqliteDataLake, config: AppConfig) -> None:
        self._lake = lake
        self._config = config
        self._bars_by_symbol: dict[str, pd.DataFrame] = {}
        self._symbols_on_date: dict[date, set[str]] = {}
        self._sector_map: dict[str, str] = {}
        self._fundamentals: dict[str, dict[str, list[tuple[date, float]]]] = {}
        self._asm_gsm: set[tuple[str, date]] = set()
        self._earnings: dict[str, list[date]] = {}
        self._membership: list[str] = []
        self._extras: list[str] = list(scan_universe_extras())
        self._history_days: int = config.darvas_box.required_price_history_days + 50
        self._warmed = False

    def warm(self, start: date, end: date) -> None:
        """Bulk-load bars and reference data for [warmup_start, end]."""
        warmup = min(self._config.backtest.price_warmup_start_date, start)
        bar_start = warmup - timedelta(days=self._history_days + 30)
        index_sym = self._config.darvas_box.market_trend_filter.index

        self._membership = self._lake.get_universe(end)
        symbols = sorted(
            set(self._membership)
            | set(self._extras)
            | set(SECTOR_INDEX_SYMBOLS.values())
            | {index_sym}
        )

        atr_period = self._config.darvas_box.atr_period
        with self._lake._connect() as conn:
            placeholders = ",".join("?" * len(symbols))
            bars_df = pd.read_sql_query(
                f"""
                SELECT symbol, date, open, high, low, close, volume, turnover_inr
                FROM daily_bars
                WHERE symbol IN ({placeholders})
                  AND date >= ? AND date <= ?
                ORDER BY symbol, date
                """,
                conn,
                params=[*symbols, bar_start.isoformat(), end.isoformat()],
            )
            sector_rows = conn.execute("SELECT symbol, sector FROM sector_map").fetchall()
            if symbols:
                fund_df = pd.read_sql_query(
                    f"""
                    SELECT symbol, metric, effective_date, value
                    FROM fundamentals_pit
                    WHERE symbol IN ({placeholders})
                    """,
                    conn,
                    params=symbols,
                )
                asm_rows = conn.execute(
                    f"""
                    SELECT symbol, date FROM asm_gsm_exclusions
                    WHERE symbol IN ({placeholders})
                    """,
                    symbols,
                ).fetchall()
                earn_rows = conn.execute(
                    f"""
                    SELECT symbol, event_date FROM earnings_calendar
                    WHERE symbol IN ({placeholders})
                    """,
                    symbols,
                ).fetchall()

        if not bars_df.empty:
            bars_df["date"] = pd.to_datetime(bars_df["date"]).dt.date
            for sym, group in bars_df.groupby("symbol"):
                enriched = enrich_bar_indicators(
                    group.reset_index(drop=True),
                    atr_period=atr_period,
                )
                self._bars_by_symbol[str(sym)] = enriched
                for session_date in enriched["date"].unique():
                    self._symbols_on_date.setdefault(session_date, set()).add(str(sym))

        self._sector_map = {r["symbol"]: r["sector"] for r in sector_rows}
        self._load_fundamentals(fund_df if symbols else pd.DataFrame())
        self._asm_gsm = {
            (r["symbol"], date.fromisoformat(r["date"])) for r in asm_rows
        }
        self._earnings = {}
        for r in earn_rows:
            self._earnings.setdefault(r["symbol"], []).append(date.fromisoformat(r["event_date"]))
        for sym in self._earnings:
            self._earnings[sym].sort()

        self._warmed = True

    def _load_fundamentals(self, fund_df: pd.DataFrame) -> None:
        self._fundamentals = {}
        if fund_df.empty:
            return
        fund_df = fund_df.copy()
        fund_df["effective_date"] = pd.to_datetime(fund_df["effective_date"]).dt.date
        for (sym, metric), group in fund_df.groupby(["symbol", "metric"]):
            entries = sorted(
                (row.effective_date, float(row.value)) for row in group.itertuples()
            )
            self._fundamentals.setdefault(str(sym), {})[str(metric)] = entries

    def slice_bars(self, symbol: str, end: date, days: int | None = None) -> pd.DataFrame:
        days = days or self._history_days
        df = self._bars_by_symbol.get(symbol)
        if df is None or df.empty:
            return pd.DataFrame()
        subset = df[df["date"] <= end]
        if subset.empty:
            return pd.DataFrame()
        return subset.tail(days).reset_index(drop=True)

    def get_scan_universe(self, as_of: date) -> list[str]:
        candidates = sorted(set(self._membership) | set(self._extras))
        trading = self._symbols_on_date.get(as_of, set())
        return [s for s in candidates if s in trading]

    def filter_symbols_with_bar_on(self, symbols: list[str], as_of: date) -> list[str]:
        trading = self._symbols_on_date.get(as_of, set())
        return [s for s in symbols if s in trading]

    def get_sector(self, symbol: str) -> str:
        return self._sector_map.get(symbol, "UNKNOWN")

    def get_fundamentals_pit(self, symbol: str, as_of: date) -> dict[str, float]:
        by_metric = self._fundamentals.get(symbol, {})
        out: dict[str, float] = {}
        for metric, entries in by_metric.items():
            dates = [e[0] for e in entries]
            idx = bisect_right(dates, as_of) - 1
            if idx >= 0:
                out[metric] = entries[idx][1]
        return out

    def is_asm_gsm(self, symbol: str, as_of: date) -> bool:
        return (symbol, as_of) in self._asm_gsm

    def has_upcoming_earnings(self, symbol: str, as_of: date, days: int) -> bool:
        events = self._earnings.get(symbol, [])
        if not events:
            return False
        limit = as_of.toordinal() + days
        for ev in events:
            ord_ev = ev.toordinal()
            if as_of.toordinal() < ord_ev <= limit:
                return True
        return False
