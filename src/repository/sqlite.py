"""SQLite repository for backtest data lake and run state."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.models import BoxState, BoxStateEnum, DecisionLogRow, OpenPosition, TradeLedgerRow
from src.repository.base import Repository

DATA_LAKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    turnover_inr REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_daily_bars_date ON daily_bars(date);

CREATE TABLE IF NOT EXISTS fundamentals_pit (
    symbol TEXT NOT NULL,
    metric TEXT NOT NULL,
    period_end TEXT,
    effective_date TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    PRIMARY KEY (symbol, metric, effective_date)
);

CREATE TABLE IF NOT EXISTS nifty500_membership (
    symbol TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    PRIMARY KEY (symbol, effective_date)
);

CREATE TABLE IF NOT EXISTS trading_calendar (
    date TEXT PRIMARY KEY,
    is_trading_day INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sector_map (
    symbol TEXT PRIMARY KEY,
    sector TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asm_gsm_exclusions (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    list_type TEXT NOT NULL,
    PRIMARY KEY (symbol, date, list_type)
);

CREATE TABLE IF NOT EXISTS earnings_calendar (
    symbol TEXT NOT NULL,
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL,
    PRIMARY KEY (symbol, event_date)
);
"""

RUN_STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS active_state_registry (
    symbol TEXT PRIMARY KEY,
    box_state TEXT NOT NULL,
    box_top REAL,
    box_bottom REAL,
    box_start_date TEXT,
    box_end_date TEXT,
    volume_sma_20 REAL,
    days_in_box INTEGER DEFAULT 0,
    reversal_high REAL,
    last_close REAL,
    breakout_date TEXT
);

CREATE TABLE IF NOT EXISTS trade_ledger (
    trade_id TEXT PRIMARY KEY,
    timestamp TEXT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    current_stop_loss REAL,
    current_target REAL,
    structural_rr_at_entry REAL,
    gtt_buy_trigger_id TEXT,
    gtt_position_oco_id TEXT,
    oco_pending_review INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    exit_reason TEXT,
    entry_date TEXT,
    exit_date TEXT
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    last_updated_timestamp TEXT
);

CREATE TABLE IF NOT EXISTS decision_log (
    date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    box_state TEXT,
    box_top REAL,
    box_bottom REAL,
    filter_pass INTEGER,
    filter_fail_reason TEXT,
    structural_rr REAL,
    rank INTEGER,
    selected INTEGER,
    action_type TEXT,
    skip_reason TEXT,
    trigger_price REAL,
    stop_loss_price REAL,
    target_price REAL,
    quantity INTEGER
);
"""


def init_data_lake(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(DATA_LAKE_SCHEMA)


def init_run_state(conn: sqlite3.Connection) -> None:
    conn.executescript(RUN_STATE_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(active_state_registry)")}
    if "breakout_date" not in cols:
        conn.execute("ALTER TABLE active_state_registry ADD COLUMN breakout_date TEXT")


class SqliteDataLake:
    """Read-only access to historical bars, PIT fundamentals, reference data."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            init_data_lake(self.db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

    def _connect(self) -> sqlite3.Connection:
        return self._conn

    def get_trading_days(self, start: date, end: date) -> list[date]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT date FROM trading_calendar
            WHERE is_trading_day = 1 AND date >= ? AND date <= ?
            ORDER BY date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if rows:
            return [date.fromisoformat(r["date"]) for r in rows]
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT DISTINCT date FROM daily_bars
            WHERE date >= ? AND date <= ?
            ORDER BY date
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        return [date.fromisoformat(r["date"]) for r in rows]

    def get_latest_trading_day(self, on_or_before: date | None = None) -> date | None:
        end = on_or_before or date.today()
        days = self.get_trading_days(date(2000, 1, 1), end)
        return days[-1] if days else None

    def get_universe(self, as_of: date) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT symbol FROM nifty500_membership
            WHERE effective_date = (
                SELECT MAX(effective_date) FROM nifty500_membership WHERE effective_date <= ?
            )
            """,
            (as_of.isoformat(),),
        ).fetchall()
        return [r["symbol"] for r in rows if not r["symbol"].startswith("DEMO")]

    def filter_symbols_with_bar_on(self, symbols: list[str], as_of: date) -> list[str]:
        """Keep only symbols that have an OHLCV bar on the given session date."""
        if not symbols:
            return []
        conn = self._connect()
        placeholders = ",".join("?" * len(symbols))
        rows = conn.execute(
            f"""
            SELECT symbol FROM daily_bars
            WHERE date = ? AND symbol IN ({placeholders})
            """,
            (as_of.isoformat(), *symbols),
        ).fetchall()
        have = {r["symbol"] for r in rows}
        return [s for s in symbols if s in have]

    def get_scan_universe(self, as_of: date) -> list[str]:
        """NIFTY 500 membership plus sector ETFs, limited to symbols trading on as_of."""
        from src.data.sector_etfs import scan_universe_extras

        base = self.get_universe(as_of)
        candidates = sorted(set(base) | set(scan_universe_extras()))
        return self.filter_symbols_with_bar_on(candidates, as_of)

    def get_daily_bars(self, symbol: str, end: date, days: int) -> pd.DataFrame:
        conn = self._connect()
        df = pd.read_sql_query(
            """
            SELECT symbol, date, open, high, low, close, volume, turnover_inr
            FROM daily_bars
            WHERE symbol = ? AND date <= ?
            ORDER BY date DESC
            LIMIT ?
            """,
            conn,
            params=(symbol, end.isoformat(), days),
        )
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df.sort_values("date").reset_index(drop=True)

    def get_fundamentals_pit(self, symbol: str, as_of: date) -> dict[str, float]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT metric, value FROM fundamentals_pit f
            WHERE symbol = ? AND effective_date = (
                SELECT MAX(effective_date) FROM fundamentals_pit
                WHERE symbol = f.symbol AND metric = f.metric AND effective_date <= ?
            )
            """,
            (symbol, as_of.isoformat()),
        ).fetchall()
        return {r["metric"]: r["value"] for r in rows}

    def get_sector(self, symbol: str) -> str:
        conn = self._connect()
        row = conn.execute(
            "SELECT sector FROM sector_map WHERE symbol = ?", (symbol,)
        ).fetchone()
        return row["sector"] if row else "UNKNOWN"

    def is_asm_gsm(self, symbol: str, as_of: date) -> bool:
        conn = self._connect()
        row = conn.execute(
            "SELECT 1 FROM asm_gsm_exclusions WHERE symbol = ? AND date = ?",
            (symbol, as_of.isoformat()),
        ).fetchone()
        return row is not None

    def has_upcoming_earnings(self, symbol: str, as_of: date, days: int) -> bool:
        conn = self._connect()
        row = conn.execute(
            """
            SELECT 1 FROM earnings_calendar
            WHERE symbol = ? AND event_date > ? AND julianday(event_date) <= julianday(?) + ?
            """,
            (symbol, as_of.isoformat(), as_of.isoformat(), days),
        ).fetchone()
        return row is not None


class SqliteBacktestRepository(Repository):
    """Mutable run state for a single backtest."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
        else:
            self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        init_run_state(self._conn)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_state_registry(self) -> dict[str, BoxState]:
        rows = self._conn.execute("SELECT * FROM active_state_registry").fetchall()
        out: dict[str, BoxState] = {}
        for r in rows:
            out[r["symbol"]] = BoxState(
                symbol=r["symbol"],
                box_state=BoxStateEnum(r["box_state"]),
                box_top=r["box_top"],
                box_bottom=r["box_bottom"],
                box_start_date=date.fromisoformat(r["box_start_date"]) if r["box_start_date"] else None,
                box_end_date=date.fromisoformat(r["box_end_date"]) if r["box_end_date"] else None,
                volume_sma_20=r["volume_sma_20"],
                days_in_box=r["days_in_box"] or 0,
                reversal_high=r["reversal_high"],
                last_close=r["last_close"],
                breakout_date=(
                    date.fromisoformat(r["breakout_date"])
                    if "breakout_date" in r.keys() and r["breakout_date"]
                    else None
                ),
            )
        return out

    def upsert_state_registry(self, registry: dict[str, BoxState]) -> None:
        if not registry:
            return
        rows = [
            (
                sym,
                st.box_state.value,
                st.box_top,
                st.box_bottom,
                st.box_start_date.isoformat() if st.box_start_date else None,
                st.box_end_date.isoformat() if st.box_end_date else None,
                st.volume_sma_20,
                st.days_in_box,
                st.reversal_high,
                st.last_close,
                st.breakout_date.isoformat() if st.breakout_date else None,
            )
            for sym, st in registry.items()
        ]
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO active_state_registry
            (symbol, box_state, box_top, box_bottom, box_start_date, box_end_date,
             volume_sma_20, days_in_box, reversal_high, last_close, breakout_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()

    def get_open_positions(self) -> list[OpenPosition]:
        rows = self._conn.execute(
            "SELECT * FROM trade_ledger WHERE is_active = 1 AND direction = 'BUY'"
        ).fetchall()
        return [
            OpenPosition(
                symbol=r["symbol"],
                quantity=r["quantity"],
                entry_price=r["price"],
                current_stop_loss=r["current_stop_loss"] or 0.0,
                current_target=r["current_target"] or 0.0,
                trade_id=r["trade_id"],
            )
            for r in rows
        ]

    def record_trade(self, trade: TradeLedgerRow) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO trade_ledger
            (trade_id, timestamp, symbol, direction, price, quantity,
             current_stop_loss, current_target, structural_rr_at_entry,
             gtt_buy_trigger_id, gtt_position_oco_id, oco_pending_review,
             is_active, exit_reason, entry_date, exit_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade.trade_id,
                (trade.timestamp or datetime.utcnow()).isoformat(),
                trade.symbol,
                trade.direction,
                trade.price,
                trade.quantity,
                trade.current_stop_loss,
                trade.current_target,
                trade.structural_rr_at_entry,
                trade.gtt_buy_trigger_id,
                trade.gtt_position_oco_id,
                int(trade.oco_pending_review),
                int(trade.is_active),
                trade.exit_reason.value if trade.exit_reason else None,
                trade.entry_date.isoformat() if trade.entry_date else None,
                trade.exit_date.isoformat() if trade.exit_date else None,
            ),
        )
        self._conn.commit()

    def update_trade(self, trade_id: str, **fields: object) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [trade_id]
        self._conn.execute(f"UPDATE trade_ledger SET {cols} WHERE trade_id = ?", vals)
        self._conn.commit()

    def get_system_state(self, key: str) -> dict:
        row = self._conn.execute(
            "SELECT value_json FROM system_state WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["value_json"]) if row else {}

    def set_system_state(self, key: str, value: dict) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO system_state (key, value_json, last_updated_timestamp)
            VALUES (?, ?, ?)
            """,
            (key, json.dumps(value), datetime.utcnow().isoformat()),
        )
        self._conn.commit()

    def get_fundamentals_pit(self, symbol: str, as_of: date) -> dict[str, float]:
        raise NotImplementedError("Use SqliteDataLake for PIT reads")

    def get_daily_bars(self, symbol: str, end: date, days: int) -> pd.DataFrame:
        raise NotImplementedError("Use SqliteDataLake for bar reads")

    def append_decision_log(self, rows: list[DecisionLogRow]) -> None:
        if not rows:
            return
        payload = [
            (
                row.date.isoformat(),
                row.symbol,
                row.box_state,
                row.box_top,
                row.box_bottom,
                int(row.filter_pass),
                row.filter_fail_reason,
                row.structural_rr,
                row.rank,
                int(row.selected),
                row.action_type,
                row.skip_reason,
                row.trigger_price,
                row.stop_loss_price,
                row.target_price,
                row.quantity,
            )
            for row in rows
        ]
        self._conn.executemany(
            """
            INSERT INTO decision_log
            (date, symbol, box_state, box_top, box_bottom, filter_pass,
             filter_fail_reason, structural_rr, rank, selected, action_type,
             skip_reason, trigger_price, stop_loss_price, target_price, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        self._conn.commit()

    def get_all_trades(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM trade_ledger ORDER BY entry_date").fetchall()
        return [dict(r) for r in rows]

    def get_decision_log_df(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM decision_log", self._conn)
