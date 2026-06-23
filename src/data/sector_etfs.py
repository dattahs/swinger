"""Sector ETF universe and NSE sector-index reference symbols for backtests."""

from __future__ import annotations

# Liquid NSE sector ETFs (Nippon / ICICI / etc.) — included in daily scan universe.
SECTOR_ETF_SYMBOLS: tuple[str, ...] = (
    "AUTOBEES",
    "BANKBEES",
    "CONSUMBEES",
    "INFRABEES",
    "ITBEES",
    "PHARMABEES",
    "PSUBNKBEES",
)

# NSE index names as stored in daily_bars (from ind_close_all archives).
SECTOR_INDEX_SYMBOLS: dict[str, str] = {
    "AUTO": "NIFTY AUTO",
    "BANK": "NIFTY BANK",
    "CONSUMPTION": "NIFTY CONSUMPTION",
    "ENERGY": "NIFTY ENERGY",
    "FINANCIAL_SERVICES": "NIFTY FINANCIAL SERVICES",
    "FMCG": "NIFTY FMCG",
    "HEALTHCARE": "NIFTY HEALTHCARE",
    "INFRA": "NIFTY INFRA",
    "IT": "NIFTY IT",
    "MEDIA": "NIFTY MEDIA",
    "METAL": "NIFTY METAL",
    "PHARMA": "NIFTY PHARMA",
    "PRIVATE_BANK": "NIFTY PRIVATE BANK",
    "PSU_BANK": "NIFTY PSU BANK",
    "REALTY": "NIFTY REALTY",
}

# Map NSE industry / sector strings (lowercased substring) → canonical sector key.
_INDUSTRY_TO_SECTOR_KEY: tuple[tuple[str, str], ...] = (
    ("pharma", "PHARMA"),
    ("health", "HEALTHCARE"),
    ("hospital", "HEALTHCARE"),
    ("software", "IT"),
    ("information tech", "IT"),
    ("it ", "IT"),
    ("private bank", "PRIVATE_BANK"),
    ("psu bank", "PSU_BANK"),
    ("public sector bank", "PSU_BANK"),
    ("bank", "BANK"),
    ("financial services", "FINANCIAL_SERVICES"),
    ("financial", "FINANCIAL_SERVICES"),
    ("insurance", "FINANCIAL_SERVICES"),
    ("nbfc", "FINANCIAL_SERVICES"),
    ("auto", "AUTO"),
    ("automobile", "AUTO"),
    ("fmcg", "FMCG"),
    ("consumer", "CONSUMPTION"),
    ("consumption", "CONSUMPTION"),
    ("metal", "METAL"),
    ("steel", "METAL"),
    ("mining", "METAL"),
    ("realty", "REALTY"),
    ("real estate", "REALTY"),
    ("construction", "REALTY"),
    ("infra", "INFRA"),
    ("infrastructure", "INFRA"),
    ("capital goods", "INFRA"),
    ("aerospace", "INFRA"),
    ("defence", "INFRA"),
    ("telecom", "IT"),
    ("retail", "CONSUMPTION"),
    ("textiles", "CONSUMPTION"),
    ("oil & gas", "ENERGY"),
    ("chemicals", "ENERGY"),
    ("metals", "METAL"),
    ("power", "ENERGY"),
    ("oil", "ENERGY"),
    ("gas", "ENERGY"),
    ("energy", "ENERGY"),
    ("media", "MEDIA"),
    ("entertainment", "MEDIA"),
)

# Sector ETF used for per-sector trend override (price series in daily_bars).
_SECTOR_KEY_TO_ETF: dict[str, str] = {
    "AUTO": "AUTOBEES",
    "BANK": "BANKBEES",
    "CONSUMPTION": "CONSUMBEES",
    "INFRA": "INFRABEES",
    "IT": "ITBEES",
    "PHARMA": "PHARMABEES",
    "PSU_BANK": "PSUBNKBEES",
    "PRIVATE_BANK": "BANKBEES",
    "FINANCIAL_SERVICES": "BANKBEES",
    "HEALTHCARE": "PHARMABEES",
    "FMCG": "CONSUMBEES",
    "ENERGY": "INFRABEES",
    "METAL": "INFRABEES",
    "REALTY": "INFRABEES",
    "MEDIA": "CONSUMBEES",
}


def is_sector_etf(symbol: str) -> bool:
    return symbol.upper() in SECTOR_ETF_SYMBOLS


def sector_key_from_label(sector_label: str) -> str | None:
    """Resolve NSE industry/sector text to a canonical sector key."""
    text = sector_label.strip().lower()
    if not text or text == "unknown":
        return None
    for needle, key in _INDUSTRY_TO_SECTOR_KEY:
        if needle in text:
            return key
    return None


def sector_index_symbol(sector_label: str) -> str | None:
    key = sector_key_from_label(sector_label)
    if key is None:
        return None
    return SECTOR_INDEX_SYMBOLS.get(key)


def sector_etf_for_label(sector_label: str) -> str | None:
    key = sector_key_from_label(sector_label)
    if key is None:
        return None
    return _SECTOR_KEY_TO_ETF.get(key)


def scan_universe_extras() -> list[str]:
    """Symbols appended to NIFTY 500 for Darvas scanning."""
    return list(SECTOR_ETF_SYMBOLS)
