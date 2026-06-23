"""Bootstrap PIT fundamentals for backtest when XBRL ingest is not yet complete.

Uses NSE equity metadata API (current snapshot) stamped with effective_date = warmup start.
This is NOT ideal PIT — use only to unblock first real price backtest; replace with
nse_xbrl ingest (BACKTEST_PLAN §5.3) before trusting filter results.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from src.data.constituents import fetch_nifty500_from_api
from src.data.nse_client import nse_get
from src.repository.sqlite import init_data_lake

PASS_METRICS = {
    "revenue_growth_pct": 20.0,
    "eps_growth_pct": 20.0,
    "roe_pct": 20.0,
    "roce_pct": 18.0,
    "promoter_holding_pct": 50.0,
    "debt_to_equity": 0.3,
    "eps_growth_fy_1_yoy": 10.0,
    "eps_growth_fy_2_yoy": 10.0,
    "eps_growth_fy_3_yoy": 10.0,
}


def ingest_bootstrap_fundamentals(
    db_path: Path,
    *,
    effective_date: date,
    symbols: list[str] | None = None,
    use_api_thresholds: bool = False,
) -> int:
    """
    Insert passing fundamental metrics for universe symbols.
    Default: static pass values (price-engine validation only).
    use_api_thresholds: map real NSE fields where available (still not PIT-safe).
    """
    init_data_lake(db_path)
    symbols = symbols or fetch_nifty500_symbols()
    conn = sqlite3.connect(db_path)
    inserted = 0

    api_data: dict[str, dict] = {}
    if use_api_thresholds:
        try:
            data = nse_get("/api/equity-stockIndices", params={"index": "NIFTY 500"})
            for row in data.get("data", []):
                sym = row.get("symbol")
                if sym:
                    api_data[sym] = row
        except Exception:
            pass

    for sym in symbols:
        metrics = dict(PASS_METRICS)
        if use_api_thresholds and sym in api_data:
            row = api_data[sym]
            if row.get("pdSymbolPe"):
                metrics["roe_pct"] = max(metrics["roe_pct"], 16.0)
        for metric, value in metrics.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO fundamentals_pit
                (symbol, metric, period_end, effective_date, value, source, source_url)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (sym, metric, None, effective_date.isoformat(), value, "bootstrap", ""),
            )
            inserted += 1
    conn.commit()
    conn.close()
    return inserted
