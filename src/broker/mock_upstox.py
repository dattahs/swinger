"""Bhavcopy-backed mock Upstox broker for laptop mock-live trials."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.broker.base import GTTBrokerClient
from src.broker.instruments import InstrumentResolver
from src.broker.types import (
    BrokerFill,
    BrokerFunds,
    BrokerGTT,
    BrokerPosition,
    BrokerSnapshot,
    GTTStatus,
)
from src.models import PlannedGTTAction
from src.repository.base import Repository
from src.repository.sqlite import SqliteDataLake

logger = logging.getLogger(__name__)

_BOOK_KEY = "mock_broker_book"
_PAPER_COUNTER_KEY = "mock_broker_paper_counter"


def _empty_book(initial_cash: float) -> dict[str, Any]:
    return {
        "settled_cash_inr": initial_cash,
        "unsettled_proceeds": [],
        "pending_buys": {},
        "positions": {},
        "oco_gtts": {},
        "last_processed_date": None,
    }


class MockUpstoxGTTClient(GTTBrokerClient):
    """Simulate Upstox GTT lifecycle using NSE EOD high/low from the data lake."""

    def __init__(
        self,
        repo: Repository,
        data_lake: SqliteDataLake,
        instruments: InstrumentResolver,
        *,
        slippage_pct: float = 0.05,
        initial_capital_inr: float = 500_000.0,
    ) -> None:
        self.repo = repo
        self.data_lake = data_lake
        self.instruments = instruments
        self.slippage = slippage_pct / 100.0
        self.initial_capital_inr = initial_capital_inr
        self._book = self._load_book()
        self._current_session_date: date | None = None

    def _load_book(self) -> dict[str, Any]:
        stored = self.repo.get_system_state(_BOOK_KEY)
        if stored:
            return stored
        sync = self.repo.get_system_state("broker_sync") or {}
        cash = float(sync.get("settled_cash_inr") or 0.0)
        if cash <= 0:
            cash = self.initial_capital_inr
        return _empty_book(cash)

    def _save_book(self) -> None:
        self.repo.set_system_state(_BOOK_KEY, self._book)

    def _next_paper_id(self, prefix: str) -> str:
        counter = int(self.repo.get_system_state(_PAPER_COUNTER_KEY) or 0)
        counter += 1
        self.repo.set_system_state(_PAPER_COUNTER_KEY, counter)
        return f"PAPER-{prefix}-{counter:06d}"

    def _bar_for(self, symbol: str, session_date: date) -> dict[str, float] | None:
        df = self.data_lake.get_daily_bars(symbol, session_date, 5)
        if df.empty or "date" not in df.columns:
            return None
        match = df[df["date"] == session_date]
        if match.empty:
            return None
        row = match.iloc[-1]
        return {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }

    def _settle_cash(self, session_date: date) -> None:
        still: list[dict[str, Any]] = []
        for entry in self._book.get("unsettled_proceeds", []):
            avail = date.fromisoformat(str(entry["avail_date"])[:10])
            if avail <= session_date:
                self._book["settled_cash_inr"] = float(self._book.get("settled_cash_inr", 0.0)) + float(
                    entry["amount"]
                )
            else:
                still.append(entry)
        self._book["unsettled_proceeds"] = still

    def _process_session_fills(self, session_date: date) -> list[BrokerFill]:
        """Apply EOD bar logic for pending buys and OCO exits (GTTs placed before today)."""
        fills: list[BrokerFill] = []
        self._settle_cash(session_date)

        pending = self._book.get("pending_buys", {})
        for symbol, order in list(pending.items()):
            placed_raw = order.get("placed_date", "")
            if not placed_raw:
                continue
            placed = date.fromisoformat(str(placed_raw)[:10])
            if placed >= session_date:
                continue
            bar = self._bar_for(symbol, session_date)
            if bar is None:
                continue
            trigger = float(order["trigger_price"])
            if bar["high"] < trigger:
                continue
            fill_price = trigger * (1 + self.slippage)
            qty = int(order["quantity"])
            cost = fill_price * qty
            cash = float(self._book.get("settled_cash_inr", 0.0))
            if cost > cash:
                logger.warning("Mock fill skipped %s — insufficient cash %.0f < %.0f", symbol, cash, cost)
                continue
            self._book["settled_cash_inr"] = cash - cost
            trade_id = order.get("trade_id") or f"MOCK-{symbol}-{session_date.isoformat()}"
            self._book.setdefault("positions", {})[symbol] = {
                "quantity": qty,
                "average_price": fill_price,
                "current_stop_loss": float(order.get("stop_loss_price", 0.0)),
                "current_target": float(order.get("target_price", 0.0)),
                "trade_id": trade_id,
                "entry_date": session_date.isoformat(),
            }
            pending.pop(symbol, None)
            fills.append(
                BrokerFill(
                    symbol=symbol,
                    order_id=str(order.get("gtt_order_id", "")),
                    trade_id=trade_id,
                    transaction_type="BUY",
                    quantity=qty,
                    price=fill_price,
                )
            )
            logger.info("Mock BUY fill %s qty=%d @ %.2f on %s", symbol, qty, fill_price, session_date)

        positions = self._book.get("positions", {})
        oco_gtts = self._book.get("oco_gtts", {})
        for symbol, pos in list(positions.items()):
            oco_meta = oco_gtts.get(symbol) or {}
            placed_raw = oco_meta.get("placed_date", pos.get("entry_date", ""))
            if placed_raw:
                placed = date.fromisoformat(str(placed_raw)[:10])
                if placed >= session_date:
                    continue
            bar = self._bar_for(symbol, session_date)
            if bar is None:
                continue
            stop = float(pos.get("current_stop_loss", 0.0))
            target = float(pos.get("current_target", 0.0))
            qty = int(pos["quantity"])
            exit_price = None
            side = None
            if stop > 0 and bar["low"] <= stop:
                exit_price = stop * (1 - self.slippage)
                side = "SELL"
            elif target > 0 and bar["high"] >= target:
                exit_price = target * (1 - self.slippage)
                side = "SELL"
            if exit_price is None or side is None:
                continue
            proceeds = exit_price * qty
            avail = session_date + timedelta(days=2)
            self._book.setdefault("unsettled_proceeds", []).append(
                {"avail_date": avail.isoformat(), "amount": proceeds}
            )
            positions.pop(symbol, None)
            oco_gtts.pop(symbol, None)
            fills.append(
                BrokerFill(
                    symbol=symbol,
                    order_id=str(oco_meta.get("gtt_order_id", "")),
                    trade_id=f"MOCK-SELL-{symbol}-{session_date.isoformat()}",
                    transaction_type="SELL",
                    quantity=qty,
                    price=exit_price,
                )
            )
            logger.info("Mock SELL fill %s qty=%d @ %.2f on %s", symbol, qty, exit_price, session_date)

        self._book["pending_buys"] = pending
        self._book["positions"] = positions
        self._book["oco_gtts"] = oco_gtts
        return fills

    def fetch_snapshot(
        self,
        session_date: date,
        *,
        tracked_gtt_ids: list[str],
        symbols: list[str] | None = None,
    ) -> BrokerSnapshot:
        self._current_session_date = session_date
        last = self._book.get("last_processed_date")
        fills: list[BrokerFill] = []
        if last != session_date.isoformat():
            fills = self._process_session_fills(session_date)
            self._book["last_processed_date"] = session_date.isoformat()
            self._save_book()

        gtt_orders: list[BrokerGTT] = []
        for sym, row in self._book.get("pending_buys", {}).items():
            gtt_orders.append(
                BrokerGTT(
                    gtt_order_id=str(row.get("gtt_order_id", "")),
                    symbol=sym,
                    instrument_token=self.instruments.resolve(sym),
                    transaction_type="BUY",
                    quantity=int(row.get("quantity", 0)),
                    trigger_price=float(row.get("trigger_price", 0.0)),
                    status=GTTStatus.ACTIVE,
                    gtt_type="SINGLE",
                    stop_loss_price=float(row.get("stop_loss_price", 0.0)),
                    target_price=float(row.get("target_price", 0.0)),
                )
            )
        for sym, row in self._book.get("oco_gtts", {}).items():
            pos = self._book.get("positions", {}).get(sym, {})
            gtt_orders.append(
                BrokerGTT(
                    gtt_order_id=str(row.get("gtt_order_id", "")),
                    symbol=sym,
                    instrument_token=self.instruments.resolve(sym),
                    transaction_type="SELL",
                    quantity=int(pos.get("quantity", 0)),
                    trigger_price=float(pos.get("current_stop_loss", 0.0)),
                    status=GTTStatus.ACTIVE,
                    gtt_type="MULTIPLE",
                    stop_loss_price=float(pos.get("current_stop_loss", 0.0)),
                    target_price=float(pos.get("current_target", 0.0)),
                )
            )

        positions = [
            BrokerPosition(
                symbol=sym,
                quantity=int(row["quantity"]),
                average_price=float(row["average_price"]),
            )
            for sym, row in self._book.get("positions", {}).items()
        ]

        unsettled = sum(
            float(entry.get("amount", 0.0))
            for entry in self._book.get("unsettled_proceeds", [])
        )

        return BrokerSnapshot(
            as_of=datetime.now(timezone.utc),
            funds=BrokerFunds(
                available_cash_inr=float(self._book.get("settled_cash_inr", 0.0)),
                unsettled_proceeds_inr=unsettled,
            ),
            positions=positions,
            gtt_orders=gtt_orders,
            fills_today=fills,
            errors=[],
        )

    def place_buy_gtt(self, action: PlannedGTTAction, instrument_token: str) -> str:
        gtt_id = self._next_paper_id("BUY")
        placed = (self._current_session_date or date.today()).isoformat()
        self._book.setdefault("pending_buys", {})[action.symbol] = {
            "gtt_order_id": gtt_id,
            "trigger_price": action.trigger_price,
            "stop_loss_price": action.stop_loss_price,
            "target_price": action.target_price,
            "quantity": action.quantity,
            "placed_date": placed,
            "entry_box_top": action.entry_box_top,
            "entry_box_bottom": action.entry_box_bottom,
            "trade_id": f"MOCK-{action.symbol}-{uuid.uuid4().hex[:8]}",
        }
        self._save_book()
        logger.info("Mock place_buy_gtt %s trigger=%.2f id=%s", action.symbol, action.trigger_price, gtt_id)
        return gtt_id

    def cancel_gtt(self, gtt_order_id: str) -> None:
        pending = self._book.get("pending_buys", {})
        for sym, row in list(pending.items()):
            if str(row.get("gtt_order_id")) == gtt_order_id:
                pending.pop(sym, None)
                logger.info("Mock cancel_gtt %s (%s)", gtt_order_id, sym)
                break
        oco = self._book.get("oco_gtts", {})
        for sym, row in list(oco.items()):
            if str(row.get("gtt_order_id")) == gtt_order_id:
                oco.pop(sym, None)
                logger.info("Mock cancel OCO %s (%s)", gtt_order_id, sym)
                break
        self._save_book()

    def place_oco_sell(
        self,
        symbol: str,
        instrument_token: str,
        quantity: int,
        stop_loss_price: float,
        target_price: float,
        idempotency_key: str,
    ) -> str:
        gtt_id = self._next_paper_id("OCO")
        placed = (self._current_session_date or date.today()).isoformat()
        pos = self._book.setdefault("positions", {}).get(symbol)
        if pos:
            pos["current_stop_loss"] = stop_loss_price
            pos["current_target"] = target_price
            pos["quantity"] = quantity
        self._book.setdefault("oco_gtts", {})[symbol] = {
            "gtt_order_id": gtt_id,
            "placed_date": placed,
            "idempotency_key": idempotency_key,
        }
        self._save_book()
        logger.info("Mock place_oco_sell %s stop=%.2f target=%.2f id=%s", symbol, stop_loss_price, target_price, gtt_id)
        return gtt_id

    def modify_oco_sell(
        self,
        gtt_order_id: str,
        stop_loss_price: float,
        target_price: float,
        quantity: int,
    ) -> None:
        oco = self._book.get("oco_gtts", {})
        for sym, row in oco.items():
            if str(row.get("gtt_order_id")) == gtt_order_id:
                pos = self._book.get("positions", {}).get(sym)
                if pos:
                    pos["current_stop_loss"] = stop_loss_price
                    pos["current_target"] = target_price
                    pos["quantity"] = quantity
                self._save_book()
                logger.info("Mock modify_oco_sell %s stop=%.2f target=%.2f", sym, stop_loss_price, target_price)
                return
