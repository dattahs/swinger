"""Sector regime council — 10 NSE segments, taxonomy labels, Darvas parameter hints."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data.sector_etfs import sector_key_from_label

LOOKBACK_6M_SESSIONS = 126
MIN_HISTORY_SESSIONS = 130
NIFTY50_SYMBOL = "NIFTY 50"
MIDCAP_DB_SYMBOL = "NIFTY MIDCAP 100"

REGIME_LABELS = frozenset(
    {
        "STRONG_TREND_UP",
        "WEAK_TREND_UP",
        "RANGING",
        "WEAK_TREND_DOWN",
        "STRONG_TREND_DOWN",
        "HIGH_VOLATILITY",
    }
)

SECTOR_DEFINITIONS: tuple[tuple[str, str, str, str | None, str], ...] = (
    ("Banking", "BANKNIFTY", "NIFTY BANK", "BANKBEES", "BANK"),
    ("IT", "CNXIT", "NIFTY IT", "ITBEES", "IT"),
    ("Pharma", "CNXPHARMA", "NIFTY PHARMA", "PHARMABEES", "PHARMA"),
    ("Auto", "CNXAUTO", "NIFTY AUTO", "AUTOBEES", "AUTO"),
    ("FMCG", "CNXFMCG", "NIFTY FMCG", "CONSUMBEES", "FMCG"),
    ("Metal", "CNXMETAL", "NIFTY METAL", "INFRABEES", "METAL"),
    ("Realty", "CNXREALTY", "NIFTY REALTY", "INFRABEES", "REALTY"),
    ("Energy", "CNXENERGY", "NIFTY ENERGY", "INFRABEES", "ENERGY"),
    ("Infra", "CNXINFRA", "NIFTY INFRA", "INFRABEES", "INFRA"),
    ("Midcap", "NIFMDCP100", MIDCAP_DB_SYMBOL, None, "MIDCAP"),
)


@dataclass(frozen=True)
class CouncilRequest:
    """Inputs for a single council run."""

    as_of: date | None = None
    window_months: int = 6
    db_path: Path = Path("data/processed/swinger_data.db")
    vix_csv_path: Path = Path("data/processed/india_vix_daily.csv")
    fii_net_flow_30d_cr: float | None = None
    skip_breadth: bool = False


@dataclass
class SectorMetrics:
    sector: str
    index_symbol: str
    price: float
    ma20: float
    ma50: float
    ma200: float
    rsi14: float
    atr_current: float
    atr_6m_avg: float
    pct_change_6m: float
    breadth_pct_above_50dma: float
    volume_20d_avg: int
    volume_6m_avg: int
    rs_vs_nifty50_6m: float
    atr_ratio: float
    vol_ratio: float
    raw_regime: str
    confidence: int
    regime: str


def window_start(as_of: date, window_months: int) -> date:
    """Calendar-month window start (matches council prompt convention)."""
    month = as_of.month - window_months
    year = as_of.year
    while month <= 0:
        month += 12
        year -= 1
    day = min(as_of.day, 28)
    return date(year, month, day)


def resolve_as_of(conn: sqlite3.Connection, as_of: date | None) -> date:
    if as_of is not None:
        return as_of
    row = conn.execute(
        "SELECT MAX(date) FROM daily_bars WHERE symbol = ?",
        (NIFTY50_SYMBOL,),
    ).fetchone()
    if not row or not row[0]:
        raise ValueError("No NIFTY 50 bars in data lake — cannot resolve as-of date")
    return date.fromisoformat(str(row[0]))


def load_bars(conn: sqlite3.Connection, symbol: str, end: date) -> pd.DataFrame:
    df = pd.read_sql_query(
        """
        SELECT date, open, high, low, close, volume
        FROM daily_bars WHERE symbol = ? AND date <= ?
        ORDER BY date
        """,
        conn,
        params=(symbol, end.isoformat()),
    )
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    return df.dropna(subset=["close"])


def compute_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    val = 100 - (100 / (1 + rs))
    v = val.iloc[-1]
    return float(v) if pd.notna(v) else 50.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def pct_change_over(df: pd.DataFrame, days: int) -> float:
    if len(df) < days + 1:
        return 0.0
    start = float(df.iloc[-days - 1]["close"])
    end = float(df.iloc[-1]["close"])
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def fetch_midcap_symbols() -> list[str]:
    try:
        import requests

        url = "https://nsearchives.nseindia.com/content/indices/ind_niftymidcap100list.csv"
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseindia.com/"},
            timeout=60,
        )
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text))
        sym_col = next(c for c in df.columns if str(c).lower() == "symbol")
        return sorted(str(s).strip() for s in df[sym_col].dropna().tolist())
    except Exception:
        return []


def sector_constituent_symbols(
    conn: sqlite3.Connection,
    sector_key: str,
    *,
    industry_map: dict[str, str] | None = None,
    midcap_symbols: list[str] | None = None,
) -> list[str]:
    if sector_key == "MIDCAP":
        syms = midcap_symbols if midcap_symbols is not None else fetch_midcap_symbols()
        if syms:
            return syms
    if industry_map is None:
        try:
            from src.data.constituents import fetch_nifty500_industry_map

            industry_map = fetch_nifty500_industry_map()
        except Exception:
            industry_map = {}
    rows = conn.execute("SELECT DISTINCT symbol FROM daily_bars").fetchall()
    all_syms = {r[0] for r in rows if r[0] and not str(r[0]).startswith("NIFTY")}
    return [
        sym
        for sym in all_syms
        if sector_key_from_label(industry_map.get(sym, "")) == sector_key
    ]


def compute_breadth_pct_above_50dma(
    conn: sqlite3.Connection,
    sector_key: str,
    as_of: date,
    *,
    symbols: list[str] | None = None,
) -> float:
    """Percent of sector names trading above their 50-day MA on as_of."""
    if symbols is None:
        symbols = sector_constituent_symbols(conn, sector_key)
    if not symbols:
        return 50.0

    above = 0
    total = 0
    for sym in symbols:
        df = load_bars(conn, sym, as_of)
        if len(df) < 55:
            continue
        ma50 = df["close"].rolling(50).mean().iloc[-1]
        close = float(df.iloc[-1]["close"])
        if pd.isna(ma50):
            continue
        total += 1
        if close > ma50:
            above += 1
    if total == 0:
        return 50.0
    return round(100.0 * above / total, 1)


def load_midcap_index_bars(conn: sqlite3.Connection, end: date) -> pd.DataFrame:
    df = load_bars(conn, MIDCAP_DB_SYMBOL, end)
    if not df.empty:
        return df
    symbols = fetch_midcap_symbols()
    if not symbols:
        return pd.DataFrame()
    frames: list[pd.Series] = []
    for sym in symbols:
        s = load_bars(conn, sym, end)
        if len(s) < MIN_HISTORY_SESSIONS:
            continue
        norm = s.set_index("date")["close"] / float(s.iloc[0]["close"])
        frames.append(norm)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, axis=1).mean(axis=1).reset_index()
    combined.columns = ["date", "close"]
    base = float(combined.iloc[0]["close"])
    combined["open"] = combined["close"]
    combined["high"] = combined["close"]
    combined["low"] = combined["close"]
    combined["close"] = combined["close"] * base
    combined["volume"] = 0
    return combined


def classify_regime(
    price: float,
    ma20: float,
    ma50: float,
    ma200: float,
    rsi14: float,
    breadth: float,
    atr_cur: float,
    atr_avg: float,
) -> tuple[str, int]:
    """Return (regime_label, confidence 0-100) from indicator snapshot."""
    atr_ratio = atr_cur / atr_avg if atr_avg > 0 else 1.0
    dist50 = abs(price - ma50) / ma50 * 100 if ma50 > 0 else 0.0

    if atr_ratio > 1.8:
        return "HIGH_VOLATILITY", 55
    if price < ma20 < ma50 < ma200 and rsi14 < 40 and breadth < 30:
        return "STRONG_TREND_DOWN", 78
    if price > ma20 > ma50 > ma200 and 55 <= rsi14 <= 75 and breadth > 65:
        return "STRONG_TREND_UP", 82
    if price < ma50 and price > ma200 and rsi14 < 50 and breadth < 45:
        return "WEAK_TREND_DOWN", 68
    if price > ma50 and (rsi14 < 55 or breadth < 65) and not (price > ma20 > ma50 > ma200):
        return "WEAK_TREND_UP", 65
    if dist50 <= 3 and 40 <= rsi14 <= 60:
        return "RANGING", 62
    if price > ma50:
        return "WEAK_TREND_UP", 58
    if price < ma50:
        return "WEAK_TREND_DOWN", 58
    return "RANGING", 55


def apply_confidence_override(regime: str, confidence: int) -> str:
    """Below 60 confidence → RANGING per council rules."""
    return "RANGING" if confidence < 60 else regime


def base_regime_multiplier(regime: str) -> float:
    return {
        "STRONG_TREND_UP": 1.0,
        "WEAK_TREND_UP": 0.6,
        "RANGING": 0.0,
        "WEAK_TREND_DOWN": 0.0,
        "STRONG_TREND_DOWN": 0.0,
        "HIGH_VOLATILITY": 0.25,
    }[regime]


def darvas_parameters(regime: str, confidence: int, atr_ratio: float) -> dict[str, Any]:
    skip = confidence < 60 or regime not in ("STRONG_TREND_UP", "WEAK_TREND_UP")
    if regime == "STRONG_TREND_UP":
        lookback, band, trail, min_days, vol = 20, 1.5, 8.0, 5, True
    elif regime == "WEAK_TREND_UP":
        lookback, band, trail, min_days, vol = 15, 1.2, 6.0, 7, True
    elif regime == "HIGH_VOLATILITY":
        lookback, band, trail, min_days, vol = 10, 2.0, 5.0, 10, True
    elif regime in ("WEAK_TREND_DOWN", "STRONG_TREND_DOWN"):
        lookback, band, trail, min_days, vol = 10, 1.0, 4.0, 10, False
    else:
        lookback, band, trail, min_days, vol = 15, 1.3, 5.0, 8, False
    if atr_ratio > 1.5:
        band += 0.3
        trail -= 1.0
    if confidence < 60:
        skip = True
    return {
        "skip_new_entries": skip,
        "box_lookback_days": lookback,
        "atr_period": 14,
        "atr_band_multiplier": round(band, 2),
        "trailing_stop_pct": round(trail, 2),
        "min_box_formation_days": min_days,
        "volume_confirmation_required": vol,
        "parameter_rationale": (
            f"Regime {regime} at confidence {confidence}; ATR ratio {atr_ratio:.2f}x "
            f"sets band {band} and trail {trail}%."
        ),
    }


def judge_ruling(regime: str, confidence: int) -> str:
    if confidence < 55:
        return "TRANSITION_LIKELY"
    if regime in ("STRONG_TREND_DOWN", "HIGH_VOLATILITY") or confidence < 65:
        return "TRANSITION_IMMINENT" if confidence < 60 else "TRANSITION_LIKELY"
    if regime in ("STRONG_TREND_UP", "WEAK_TREND_UP") and confidence >= 70:
        return "KEEP"
    return "TRANSITION_LIKELY"


def estimated_weeks_in_regime(regime: str, confidence: int) -> int:
    base = {
        "STRONG_TREND_UP": 8,
        "WEAK_TREND_UP": 5,
        "RANGING": 4,
        "WEAK_TREND_DOWN": 3,
        "STRONG_TREND_DOWN": 2,
        "HIGH_VOLATILITY": 2,
    }[regime]
    return max(1, int(base * confidence / 75))


def build_agent_reports(sector: str, regime: str, m: SectorMetrics) -> tuple[dict, dict, dict]:
    p, m20, m50, m200 = m.price, m.ma20, m.ma50, m.ma200
    rsi_v, br, rs = m.rsi14, m.breadth_pct_above_50dma, m.rs_vs_nifty50_6m
    ch6, vol_r, atr_r = m.pct_change_6m, m.vol_ratio, m.atr_ratio
    conf = m.confidence
    final_regime = m.regime
    mult = round(base_regime_multiplier(final_regime) * (conf / 100), 2)

    if regime in ("STRONG_TREND_UP", "WEAK_TREND_UP"):
        blue = {
            "thesis": (
                f"{sector} maintains constructive trend structure with price {p:.0f} above 50DMA "
                f"({m50:.0f}) and 6m RS ({rs:+.1f}pp vs Nifty50). Momentum and breadth support "
                f"continuation over the next 4-8 weeks."
            ),
            "top_signals": [
                f"Stacked MAs: price {'>' if p > m20 else '<'} 20MA {'>' if m20 > m50 else '<'} 50MA {'>' if m50 > m200 else '<'} 200MA",
                f"RSI-14 at {rsi_v:.1f} with breadth {br:.0f}% above 50DMA",
                f"6m return {ch6:+.1f}% with RS vs Nifty50 {rs:+.1f}pp",
            ],
        }
        red = {
            "thesis": (
                f"{sector} shows early fatigue: RSI {rsi_v:.1f}, breadth {br:.0f}%, "
                f"volume ratio {vol_r:.2f}x."
            ),
            "top_risks": [
                "Price-breadth divergence if breadth falls while index holds above 50DMA",
                f"RS vs Nifty50 at {rs:+.1f}pp — leadership may fade",
                "Rising India VIX or FII outflows would hit high-beta sectors first",
            ],
            "transition_trigger": f"Two consecutive closes below 50DMA ({m50:.0f}) with breadth below 45%",
        }
    elif regime == "RANGING":
        blue = {
            "thesis": (
                f"{sector} consolidating near 50DMA ({m50:.0f}) with RSI {rsi_v:.1f}; "
                f"range regimes often persist 4-6 weeks when ATR compresses."
            ),
            "top_signals": [
                f"Price within ±3% of 50DMA at {m50:.0f}",
                f"Breadth {br:.0f}% — mixed participation",
                f"ATR ratio {atr_r:.2f}x — volatility contraction",
            ],
        }
        red = {
            "thesis": f"Range resolution likely within 4 weeks; {sector} lacks volume confirmation for breakout.",
            "top_risks": [
                "Breakdown below range low with expanding ATR",
                "Breadth deterioration below 40%",
                "Relative weakness vs Nifty50 accelerating",
            ],
            "transition_trigger": f"Daily close >3% from 50DMA ({m50:.0f}) on volume >1.3x 20d average",
        }
    else:
        blue = {
            "thesis": (
                f"{sector} downtrend with resistance at 20/50DMA; bear phases on NSE "
                f"often extend 4-8 weeks once structure breaks down."
            ),
            "top_signals": [
                f"Price {p:.0f} below 20MA {m20:.0f} and 50MA {m50:.0f}",
                f"RSI {rsi_v:.1f} — room for further drift",
                f"Breadth {br:.0f}% — weak participation",
            ],
        }
        red = {
            "thesis": (
                f"Counter-evidence for deeper decline limited; oversold bounce risk near RSI 30 "
                f"but 200DMA at {m200:.0f} caps recovery."
            ),
            "top_risks": [
                "Counter-evidence genuinely weak — no bullish breadth divergence yet",
                f"Relief-rally risk: RSI mean-reversion from {rsi_v:.1f}",
                "Sector RS still negative — no rotation inflow",
            ],
            "transition_trigger": f"Sustained reclaim of 50DMA ({m50:.0f}) with breadth >55% for 5 sessions",
        }

    judge = {
        "ruling": judge_ruling(final_regime, conf),
        "rationale": (
            f"Blue case for {regime} weighed against Red transition risks. "
            f"Confidence {conf}/100 from MA stack, RSI {rsi_v:.1f}, breadth {br:.0f}%, ATR {atr_r:.2f}x."
        ),
        "estimated_weeks_in_regime": estimated_weeks_in_regime(final_regime, conf),
        "position_size_multiplier": mult,
    }
    return blue, red, judge


def experiment_suggestions(regime: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    lb = int(params["box_lookback_days"])
    band = float(params["atr_band_multiplier"])
    trail = float(params["trailing_stop_pct"])
    return [
        {
            "parameter": "box_lookback_days",
            "current_value": lb,
            "test_values": [max(5, lb - 5), lb, lb + 5],
            "metric_to_watch": "win_rate",
            "hypothesis": f"Lookback sweep for {regime} breakout capture vs noise.",
        },
        {
            "parameter": "atr_band_multiplier",
            "current_value": band,
            "test_values": [round(band - 0.2, 2), band, round(band + 0.2, 2)],
            "metric_to_watch": "max_drawdown_pct",
            "hypothesis": "Tighter bands reduce false breakouts in current volatility envelope.",
        },
        {
            "parameter": "trailing_stop_pct",
            "current_value": trail,
            "test_values": [round(trail - 1.0, 2), trail, round(trail + 1.0, 2)],
            "metric_to_watch": "cagr",
            "hypothesis": f"Trail width optimized for {regime} persistence vs whipsaw.",
        },
    ]


def _sector_index_bars(
    conn: sqlite3.Connection,
    sector_name: str,
    db_symbol: str,
    as_of: date,
) -> pd.DataFrame:
    if sector_name == "Midcap":
        return load_midcap_index_bars(conn, as_of)
    return load_bars(conn, db_symbol, as_of)


def _sector_volumes(
    conn: sqlite3.Connection,
    sector_name: str,
    etf_symbol: str | None,
    index_df: pd.DataFrame,
    as_of: date,
    midcap_symbols: list[str],
) -> tuple[int, int]:
    if etf_symbol:
        etf = load_bars(conn, etf_symbol, as_of)
        vol20 = int(etf.tail(20)["volume"].mean()) if not etf.empty else 0
        vol6m = int(etf.tail(LOOKBACK_6M_SESSIONS)["volume"].mean()) if len(etf) >= 20 else vol20
        return vol20, vol6m
    if sector_name == "Midcap":
        vol20_vals: list[float] = []
        vol6m_vals: list[float] = []
        for sym in midcap_symbols[:100]:
            s = load_bars(conn, sym, as_of)
            if len(s) >= 20:
                vol20_vals.append(float(s.tail(20)["volume"].mean()))
            if len(s) >= LOOKBACK_6M_SESSIONS:
                vol6m_vals.append(float(s.tail(LOOKBACK_6M_SESSIONS)["volume"].mean()))
        vol20 = int(np.mean(vol20_vals)) if vol20_vals else 0
        vol6m = int(np.mean(vol6m_vals)) if vol6m_vals else vol20
        return vol20, vol6m
    vol20 = int(index_df.tail(20)["volume"].mean())
    vol6m = int(index_df.tail(LOOKBACK_6M_SESSIONS)["volume"].mean())
    return vol20, vol6m


def compute_sector_metrics(
    conn: sqlite3.Connection,
    sector_name: str,
    index_symbol: str,
    db_symbol: str,
    etf_symbol: str | None,
    sector_key: str,
    as_of: date,
    nifty_6m_pct: float,
    *,
    skip_breadth: bool = False,
    midcap_symbols: list[str] | None = None,
    industry_map: dict[str, str] | None = None,
) -> SectorMetrics | None:
    df = _sector_index_bars(conn, sector_name, db_symbol, as_of)
    if df.empty or len(df) < MIN_HISTORY_SESSIONS:
        return None

    price = float(df.iloc[-1]["close"])
    ma20 = float(df["close"].rolling(20).mean().iloc[-1])
    ma50 = float(df["close"].rolling(50).mean().iloc[-1])
    ma200 = float(df["close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else ma50
    rsi14 = compute_rsi(df["close"])
    atr_series = compute_atr(df)
    atr_cur = float(atr_series.iloc[-1])
    atr_6m = float(atr_series.tail(LOOKBACK_6M_SESSIONS).mean())
    pct_6m = pct_change_over(df, LOOKBACK_6M_SESSIONS)
    rs_vs = pct_6m - nifty_6m_pct

    if skip_breadth:
        breadth = 50.0
    else:
        syms = None
        if sector_key == "MIDCAP" and midcap_symbols:
            syms = midcap_symbols
        breadth = compute_breadth_pct_above_50dma(conn, sector_key, as_of, symbols=syms)

    mc_syms = midcap_symbols if midcap_symbols is not None else fetch_midcap_symbols()
    vol20, vol6m = _sector_volumes(conn, sector_name, etf_symbol, df, as_of, mc_syms)
    atr_ratio = atr_cur / atr_6m if atr_6m > 0 else 1.0
    vol_ratio = vol20 / vol6m if vol6m > 0 else 1.0
    raw_regime, confidence = classify_regime(
        price, ma20, ma50, ma200, rsi14, breadth, atr_cur, atr_6m
    )
    regime = apply_confidence_override(raw_regime, confidence)

    return SectorMetrics(
        sector=sector_name,
        index_symbol=index_symbol,
        price=price,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        rsi14=rsi14,
        atr_current=atr_cur,
        atr_6m_avg=atr_6m,
        pct_change_6m=pct_6m,
        breadth_pct_above_50dma=breadth,
        volume_20d_avg=vol20,
        volume_6m_avg=vol6m,
        rs_vs_nifty50_6m=rs_vs,
        atr_ratio=atr_ratio,
        vol_ratio=vol_ratio,
        raw_regime=raw_regime,
        confidence=confidence,
        regime=regime,
    )


def build_council_summary(
    sector_outputs: list[dict[str, Any]],
    *,
    vix_current: float | None,
    vix_6m_avg: float | None,
    fii_net_flow_30d_cr: float | None,
) -> dict[str, Any]:
    regimes = [s["regime"] for s in sector_outputs]
    if not regimes:
        return {
            "dominant_regime": "RANGING",
            "regime_dispersion": "HIGH",
            "regime_dispersion_reason": "No sector data available for council run.",
            "systemic_risk_flag": False,
            "systemic_risk_reason": "Insufficient data.",
            "recommended_overall_exposure": 0.0,
            "capital_deployment_priority": [],
            "next_review_trigger": "Weekly on Friday close or India VIX +15% single-week spike",
        }

    unique = set(regimes)
    if len(unique) <= 2:
        dispersion, disp_reason = "LOW", f"Only {len(unique)} distinct regime labels — broad alignment."
    elif len(unique) <= 4:
        dispersion, disp_reason = "MEDIUM", "Mixed cyclicals vs defensives producing 3-4 regime buckets."
    else:
        dispersion, disp_reason = "HIGH", "Sector dispersion elevated with 5+ distinct regime classifications."

    dominant = max(set(regimes), key=regimes.count)
    up_count = sum(1 for r in regimes if r in ("STRONG_TREND_UP", "WEAK_TREND_UP"))
    exposure = round(min(1.0, up_count / len(regimes) * 0.85), 2)

    priority = sorted(
        sector_outputs,
        key=lambda s: (
            s["judge_verdict"]["position_size_multiplier"],
            1 if s["regime"] == "STRONG_TREND_UP" else 0,
            s["confidence"],
        ),
        reverse=True,
    )
    cap_priority = [
        p["sector"] for p in priority if p["judge_verdict"]["position_size_multiplier"] > 0
    ][:3]
    if len(cap_priority) < 3:
        cap_priority.extend(
            [
                p["sector"]
                for p in priority
                if p["sector"] not in cap_priority
                and p["regime"] not in ("STRONG_TREND_DOWN", "WEAK_TREND_DOWN")
            ][: 3 - len(cap_priority)]
        )

    fii = fii_net_flow_30d_cr if fii_net_flow_30d_cr is not None else 0.0
    systemic = (
        vix_current is not None
        and vix_6m_avg is not None
        and vix_6m_avg > 0
        and vix_current > vix_6m_avg * 1.15
        and fii < 0
    )
    if systemic and vix_current is not None and vix_6m_avg is not None:
        sys_reason = (
            f"India VIX {vix_current:.1f} vs 6m avg {vix_6m_avg:.1f}; "
            f"FII net 30d {fii:.0f} Cr."
        )
    else:
        sys_reason = "No synchronized macro stress across VIX and flows."

    return {
        "dominant_regime": dominant,
        "regime_dispersion": dispersion,
        "regime_dispersion_reason": disp_reason,
        "systemic_risk_flag": systemic,
        "systemic_risk_reason": sys_reason,
        "recommended_overall_exposure": exposure,
        "capital_deployment_priority": cap_priority,
        "next_review_trigger": "Weekly on Friday close or India VIX +15% single-week spike",
    }


def _load_vix(vix_path: Path, as_of: date) -> tuple[float | None, float | None]:
    if not vix_path.is_file():
        return None, None
    vix = pd.read_csv(vix_path, parse_dates=["date"])
    vix = vix[vix["date"] <= pd.Timestamp(as_of)]
    if vix.empty:
        return None, None
    vix_cur = float(vix.iloc[-1]["close"])
    tail = vix.tail(LOOKBACK_6M_SESSIONS)
    vix_6m = float(tail["close"].mean()) if not tail.empty else vix_cur
    return vix_cur, vix_6m


def run_sector_regime_council(request: CouncilRequest) -> dict[str, Any]:
    """Run full Blue/Red/Judge council for all configured sectors."""
    conn = sqlite3.connect(request.db_path, timeout=60)
    try:
        as_of = resolve_as_of(conn, request.as_of)
        win_start = window_start(as_of, request.window_months)

        nifty = load_bars(conn, NIFTY50_SYMBOL, as_of)
        if nifty.empty:
            raise ValueError(f"No NIFTY 50 bars on or before {as_of}")
        nifty_6m = pct_change_over(nifty, LOOKBACK_6M_SESSIONS)
        vix_cur, vix_6m = _load_vix(request.vix_csv_path, as_of)

        midcap_symbols = fetch_midcap_symbols()
        industry_map: dict[str, str] | None = None
        if not request.skip_breadth:
            try:
                from src.data.constituents import fetch_nifty500_industry_map

                industry_map = fetch_nifty500_industry_map()
            except Exception:
                industry_map = {}

        sector_outputs: list[dict[str, Any]] = []
        for sector_name, idx_sym, db_sym, etf_sym, sector_key in SECTOR_DEFINITIONS:
            metrics = compute_sector_metrics(
                conn,
                sector_name,
                idx_sym,
                db_sym,
                etf_sym,
                sector_key,
                as_of,
                nifty_6m,
                skip_breadth=request.skip_breadth,
                midcap_symbols=midcap_symbols,
                industry_map=industry_map,
            )
            if metrics is None:
                continue

            blue, red, judge = build_agent_reports(sector_name, metrics.regime, metrics)
            params = darvas_parameters(metrics.regime, metrics.confidence, metrics.atr_ratio)
            sector_outputs.append(
                {
                    "sector": sector_name,
                    "index_symbol": idx_sym,
                    "regime": metrics.regime,
                    "confidence": metrics.confidence,
                    "blue_agent": blue,
                    "red_agent": red,
                    "judge_verdict": judge,
                    "darvas_parameters": params,
                    "experiment_suggestions": experiment_suggestions(metrics.regime, params),
                }
            )

        summary = build_council_summary(
            sector_outputs,
            vix_current=vix_cur,
            vix_6m_avg=vix_6m,
            fii_net_flow_30d_cr=request.fii_net_flow_30d_cr,
        )

        return {
            "analysis_date": as_of.isoformat(),
            "window": f"{win_start.isoformat()} to {as_of.isoformat()}",
            "sectors": sector_outputs,
            "council_summary": summary,
        }
    finally:
        conn.close()
