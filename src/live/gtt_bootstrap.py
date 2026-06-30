"""Bootstrap day-1 pending GTTs before a follow-on live session."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from src.models import TradeLedgerRow
from src.repository.base import Repository
from src.repository.sqlite import SqliteDataLake

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_gtts"
_ASSUMED_KEY = "assumed_gtt_fill_symbols"


@dataclass
class GTTBootstrapReport:
    session_date: date
    assumed_fills: list[str]
    still_pending: list[str]
    skipped_already_open: list[str]


def bootstrap_pending_gtts_from_bars(
    session_date: date,
    repo: Repository,
    data_lake: SqliteDataLake,
    *,
    assume_price_fills: bool = True,
) -> GTTBootstrapReport:
    """Treat pending buy GTTs as filled when EOD high touched trigger since placement.

    Symbols still below trigger remain in pending_gtts (reactivated at broker on next
    PLACE_BUY_GTT if the GTT expired). Assumed fills are recorded in the ledger and
    tagged in system_state for reconcile.
    """
    pending: dict[str, dict] = dict(repo.get_system_state(_PENDING_KEY) or {})
    if not pending:
        return GTTBootstrapReport(session_date, [], [], [])

    assumed: list[str] = []
    skipped: list[str] = []
    still: dict[str, dict] = {}

    open_symbols = {p.symbol for p in repo.get_open_positions() if p.is_active}

    for sym, row in pending.items():
        trigger = float(row.get("trigger_price") or 0.0)
        if trigger <= 0:
            still[sym] = row
            continue

        placed_raw = row.get("placed_date") or session_date.isoformat()
        placed = date.fromisoformat(str(placed_raw)[:10])

        if sym in open_symbols:
            skipped.append(sym)
            still[sym] = row
            continue

        if not assume_price_fills:
            still[sym] = row
            continue

        bars = data_lake.get_daily_bars(sym, session_date, 400)
        if bars.empty:
            still[sym] = row
            continue

        bars = bars[(bars["date"] >= placed) & (bars["date"] <= session_date)]
        if bars.empty:
            still[sym] = row
            continue

        max_high = float(bars["high"].max())
        if max_high < trigger:
            still[sym] = row
            logger.info(
                "%s: pending GTT trigger %.2f not touched (max high %.2f) — keep/reactivate",
                sym,
                trigger,
                max_high,
            )
            continue

        qty = int(row.get("quantity") or 0)
        if qty < 1:
            still[sym] = row
            continue

        fill_price = trigger
        stop = float(row.get("stop_loss_price") or 0.0)
        target = float(row.get("target_price") or 0.0)
        trade_id = f"LIVE-{sym}-{session_date.isoformat()}-assumed-{uuid.uuid4().hex[:8]}"

        repo.record_trade(
            TradeLedgerRow(
                trade_id=trade_id,
                timestamp=datetime.utcnow(),
                symbol=sym,
                direction="BUY",
                price=fill_price,
                quantity=qty,
                current_stop_loss=stop,
                current_target=target,
                gtt_buy_trigger_id=str(row.get("gtt_order_id") or ""),
                is_active=True,
                entry_date=placed,
                entry_box_top=row.get("entry_box_top"),
                entry_box_bottom=row.get("entry_box_bottom"),
            )
        )
        assumed.append(sym)
        logger.info(
            "Assumed GTT fill %s: %d @ %.2f (trigger %.2f, max high %.2f since %s)",
            sym,
            qty,
            fill_price,
            trigger,
            max_high,
            placed,
        )

    repo.set_system_state(_PENDING_KEY, still)
    prev_assumed = set(repo.get_system_state(_ASSUMED_KEY) or [])
    repo.set_system_state(_ASSUMED_KEY, sorted(prev_assumed | set(assumed)))

    return GTTBootstrapReport(
        session_date=session_date,
        assumed_fills=assumed,
        still_pending=sorted(still.keys()),
        skipped_already_open=skipped,
    )


def assumed_gtt_fill_symbols(repo: Repository) -> set[str]:
    return set(repo.get_system_state(_ASSUMED_KEY) or [])
