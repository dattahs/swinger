"""Universe and fundamental filters — REQUIREMENTS v1.2 Sections 6, 12."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.config import AppConfig
from src.data.sector_etfs import (
    is_sector_etf,
    sector_etf_for_label,
    sector_index_symbol,
)
from src.repository.sqlite import SqliteDataLake


def index_trend_ok(index_bars: pd.DataFrame, cfg: AppConfig) -> bool:
    mas = cfg.darvas_box.market_trend_filter.moving_averages
    if index_bars.empty or len(index_bars) < max(mas):
        return False
    close = float(index_bars.iloc[-1]["close"])
    for period in mas:
        ma = float(index_bars["close"].tail(period).mean())
        if close <= ma:
            return False
    return True


def _sector_index_bars_for_symbol(
    symbol: str,
    sector_label: str,
    sector_index_bars: dict[str, pd.DataFrame],
    sector_etf_bars: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Resolve sector index series; fall back to mapped sector ETF prices."""
    index_sym = sector_index_symbol(sector_label)
    if index_sym:
        bars = sector_index_bars.get(index_sym, pd.DataFrame())
        if not bars.empty:
            return bars
    etf = symbol if is_sector_etf(symbol) else sector_etf_for_label(sector_label)
    if etf:
        return sector_etf_bars.get(etf, pd.DataFrame())
    return pd.DataFrame()


def symbol_trend_ok(
    symbol: str,
    sector_label: str,
    index_bars: pd.DataFrame,
    sector_etf_bars: dict[str, pd.DataFrame],
    sector_index_bars: dict[str, pd.DataFrame],
    cfg: AppConfig,
) -> bool:
    mtf = cfg.darvas_box.market_trend_filter

    if mtf.mode == "sector_index":
        bars = _sector_index_bars_for_symbol(
            symbol, sector_label, sector_index_bars, sector_etf_bars
        )
        return index_trend_ok(bars, cfg)

    if index_trend_ok(index_bars, cfg):
        return True
    if not mtf.allow_sector_trend_override:
        return False
    etf = sector_etf_for_label(sector_label) if not is_sector_etf(symbol) else symbol
    if etf is None:
        return False
    etf_bars = sector_etf_bars.get(etf, pd.DataFrame())
    return index_trend_ok(etf_bars, cfg)


def check_universe_filters(
    symbol: str,
    bars: pd.DataFrame,
    as_of: date,
    data_lake: SqliteDataLake,
    cfg: AppConfig,
) -> tuple[bool, str | None]:
    uf = cfg.universe_filters
    if bars.empty:
        return False, "NO_BARS"
    last = bars.iloc[-1]
    min_price = 50.0 if is_sector_etf(symbol) else uf.min_stock_price_inr
    min_volume = 100_000 if is_sector_etf(symbol) else uf.min_daily_volume_shares
    min_turnover_cr = 1.0 if is_sector_etf(symbol) else uf.min_daily_turnover_inr_cr
    if last["close"] < min_price:
        return False, "PRICE_TOO_LOW"
    if last["volume"] < min_volume:
        return False, "VOLUME_TOO_LOW"
    turnover_cr = (last.get("turnover_inr") or 0) / 1e7
    if turnover_cr < min_turnover_cr:
        return False, "TURNOVER_TOO_LOW"
    if uf.exclude_asm_gsm and data_lake.is_asm_gsm(symbol, as_of):
        return False, "ASM_GSM"
    return True, None


def check_fundamental_filters(
    symbol: str,
    as_of: date,
    data_lake: SqliteDataLake,
    cfg: AppConfig,
) -> tuple[bool, str | None]:
    if is_sector_etf(symbol):
        return True, None
    ff = cfg.fundamental_filters
    metrics = data_lake.get_fundamentals_pit(symbol, as_of)
    if not metrics:
        return False, "NO_FUNDAMENTALS"

    checks = [
        ("revenue_growth_pct", ff.min_revenue_growth_pct, "REV_GROWTH"),
        ("eps_growth_pct", ff.min_eps_growth_pct, "EPS_GROWTH"),
        ("roe_pct", ff.min_roe_pct, "ROE"),
        ("roce_pct", ff.min_roce_pct, "ROCE"),
        ("promoter_holding_pct", ff.min_promoter_holding_pct, "PROMOTER"),
    ]
    for key, threshold, label in checks:
        val = metrics.get(key)
        if val is None or val < threshold:
            return False, f"{label}<{threshold}"

    de = metrics.get("debt_to_equity")
    if de is not None and de > ff.max_debt_to_equity:
        return False, "DE_TOO_HIGH"

    if data_lake.has_upcoming_earnings(symbol, as_of, ff.avoid_days_before_earnings):
        return False, "EARNINGS_BLACKOUT"

    if ff.enforce_long_term_growth_group:
        for i in range(1, 4):
            key = f"eps_growth_fy_{i}_yoy"
            if metrics.get(key, 0) <= 0:
                return False, "LTG_GROUP"

    return True, None
