"""Reconcile broker snapshot with local portfolio ledger."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime

from src.broker.types import BrokerGTT, GTTStatus, ReconciliationDrift, ReconciliationResult
from src.broker.base import GTTBrokerClient
from src.models import OpenPosition, TradeLedgerRow
from src.repository.base import Repository

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_gtts"
_BROKER_SYNC_KEY = "broker_sync"


def _load_pending(repo: Repository) -> dict[str, dict]:
    return repo.get_system_state(_PENDING_KEY) or {}


def _save_pending(repo: Repository, pending: dict[str, dict]) -> None:
    repo.set_system_state(_PENDING_KEY, pending)


def _tracked_gtt_ids(pending: dict[str, dict], repo: Repository) -> list[str]:
    ids: list[str] = []
    for row in pending.values():
        gid = row.get("gtt_order_id")
        if gid:
            ids.append(str(gid))
    for pos in repo.get_open_positions():
        trade_rows = _active_trades_for_symbol(repo, pos.symbol)
        for t in trade_rows:
            if t.gtt_buy_trigger_id:
                ids.append(t.gtt_buy_trigger_id)
            if t.gtt_position_oco_id:
                ids.append(t.gtt_position_oco_id)
    return sorted(set(ids))


def _active_trades_for_symbol(repo: Repository, symbol: str) -> list[TradeLedgerRow]:
    """Best-effort: read active BUY rows from ledger via open positions."""
    positions = repo.get_open_positions()
    sym_positions = [p for p in positions if p.symbol == symbol]
    if not sym_positions:
        return []
    out: list[TradeLedgerRow] = []
    for p in sym_positions:
        if not p.trade_id:
            continue
        # Repository has no get_trade_by_id — reconstruct minimal row from position
        out.append(
            TradeLedgerRow(
                trade_id=p.trade_id,
                symbol=p.symbol,
                direction="BUY",
                price=p.entry_price,
                quantity=p.quantity,
                current_stop_loss=p.current_stop_loss,
                current_target=p.current_target,
                is_active=True,
            )
        )
    return out


def reconcile_broker_state(
    session_date: date,
    broker: GTTBrokerClient,
    repo: Repository,
    *,
    adopt_broker_truth: bool = True,
    price_map: dict[str, float] | None = None,
) -> ReconciliationResult:
    """Sync funds, positions, pending GTTs, and today's fills with the repository."""
    pending = _load_pending(repo)
    tracked_ids = _tracked_gtt_ids(pending, repo)
    snapshot = broker.fetch_snapshot(
        session_date,
        tracked_gtt_ids=tracked_ids,
        symbols=list(pending.keys()),
    )
    drifts: list[ReconciliationDrift] = []
    price_map = price_map or {}

    broker_by_symbol = {p.symbol: p for p in snapshot.positions if p.symbol}
    ledger_positions = {p.symbol: p for p in repo.get_open_positions()}

    for sym, bpos in broker_by_symbol.items():
        lpos = ledger_positions.get(sym)
        if lpos is None:
            drifts.append(
                ReconciliationDrift(
                    kind="position_missing_in_ledger",
                    symbol=sym,
                    message=f"Broker has {bpos.quantity} shares; ledger has no open position",
                    broker_value=str(bpos.quantity),
                    ledger_value="0",
                )
            )
            if adopt_broker_truth:
                _establish_position_from_broker(repo, session_date, bpos)
        elif lpos.quantity != bpos.quantity:
            drifts.append(
                ReconciliationDrift(
                    kind="position_qty_mismatch",
                    symbol=sym,
                    message=f"Qty mismatch broker={bpos.quantity} ledger={lpos.quantity}",
                    broker_value=str(bpos.quantity),
                    ledger_value=str(lpos.quantity),
                )
            )
            if adopt_broker_truth:
                repo.update_trade(lpos.trade_id, quantity=bpos.quantity, price=bpos.average_price)

    for sym, lpos in ledger_positions.items():
        if sym not in broker_by_symbol:
            drifts.append(
                ReconciliationDrift(
                    kind="position_missing_at_broker",
                    symbol=sym,
                    message=f"Ledger shows open {sym} but broker has no position",
                    broker_value="0",
                    ledger_value=str(lpos.quantity),
                )
            )
            if adopt_broker_truth:
                repo.update_trade(lpos.trade_id, is_active=0)

    active_gtts = {
        g.gtt_order_id: g
        for g in snapshot.gtt_orders
        if g.status in (GTTStatus.ACTIVE, GTTStatus.UNKNOWN)
    }
    for sym, prow in list(pending.items()):
        gid = str(prow.get("gtt_order_id", ""))
        if gid and gid not in active_gtts:
            drifts.append(
                ReconciliationDrift(
                    kind="pending_gtt_missing_at_broker",
                    symbol=sym,
                    message=f"Tracked buy GTT {gid} not active at broker",
                    broker_value="absent",
                    ledger_value=gid,
                )
            )
            pending.pop(sym, None)

    broker_buy_gtts: dict[str, BrokerGTT] = {}
    for g in snapshot.gtt_orders:
        if g.transaction_type == "BUY" and g.status in (GTTStatus.ACTIVE, GTTStatus.UNKNOWN):
            if g.symbol:
                broker_buy_gtts[g.symbol] = g

    for sym, gtt in broker_buy_gtts.items():
        if sym not in pending:
            drifts.append(
                ReconciliationDrift(
                    kind="untracked_broker_gtt",
                    symbol=sym,
                    message=f"Broker has buy GTT {gtt.gtt_order_id} not in pending_gtts",
                    broker_value=gtt.gtt_order_id,
                    ledger_value="",
                )
            )
            pending[sym] = {
                "gtt_order_id": gtt.gtt_order_id,
                "trigger_price": gtt.trigger_price,
                "stop_loss_price": gtt.stop_loss_price or 0.0,
                "target_price": gtt.target_price or 0.0,
                "quantity": gtt.quantity,
                "placed_date": session_date.isoformat(),
                "status": "ACTIVE",
            }

    _apply_fills(session_date, snapshot.fills_today, repo, pending, drifts, adopt_broker_truth)

    settled_cash = snapshot.funds.available_cash_inr
    repo.set_system_state(
        _BROKER_SYNC_KEY,
        {
            "last_sync_date": session_date.isoformat(),
            "settled_cash_inr": settled_cash,
            "drift_count": len(drifts),
            "snapshot_errors": snapshot.errors,
        },
    )
    _save_pending(repo, pending)

    if drifts:
        for d in drifts:
            logger.warning("Reconcile drift [%s] %s: %s", d.kind, d.symbol, d.message)
    else:
        logger.info("Reconcile OK for %s — cash ₹%.2f", session_date, settled_cash)

    open_count = len(repo.get_open_positions())
    return ReconciliationResult(
        session_date=session_date,
        snapshot=snapshot,
        drifts=drifts,
        adopted_broker_truth=adopt_broker_truth and bool(drifts),
        pending_symbols=set(pending.keys()),
        settled_cash_inr=settled_cash,
        open_positions_synced=open_count,
    )


def _establish_position_from_broker(repo: Repository, session_date: date, bpos) -> None:
    trade_id = f"LIVE-{bpos.symbol}-{session_date.isoformat()}-{uuid.uuid4().hex[:8]}"
    repo.record_trade(
        TradeLedgerRow(
            trade_id=trade_id,
            timestamp=datetime.utcnow(),
            symbol=bpos.symbol,
            direction="BUY",
            price=bpos.average_price,
            quantity=bpos.quantity,
            current_stop_loss=0.0,
            current_target=0.0,
            is_active=True,
            entry_date=session_date,
        )
    )


def _apply_fills(
    session_date: date,
    fills: list,
    repo: Repository,
    pending: dict[str, dict],
    drifts: list[ReconciliationDrift],
    adopt_broker_truth: bool,
) -> None:
    for fill in fills:
        if fill.transaction_type != "BUY":
            _handle_sell_fill(session_date, fill, repo, adopt_broker_truth)
            continue
        sym = fill.symbol
        prow = pending.get(sym)
        if prow:
            pending.pop(sym, None)
        drifts.append(
            ReconciliationDrift(
                kind="buy_fill_detected",
                symbol=sym,
                message=f"Buy fill {fill.quantity} @ {fill.price}",
                broker_value=str(fill.price),
                ledger_value="pending",
            )
        )
        if adopt_broker_truth:
            existing = [p for p in repo.get_open_positions() if p.symbol == sym]
            stop = float(prow.get("stop_loss_price", 0.0)) if prow else 0.0
            target = float(prow.get("target_price", 0.0)) if prow else 0.0
            if existing:
                repo.update_trade(
                    existing[0].trade_id,
                    quantity=fill.quantity,
                    price=fill.price,
                    current_stop_loss=stop,
                    current_target=target,
                    entry_date=session_date.isoformat(),
                )
            else:
                _establish_position_from_broker(
                    repo,
                    session_date,
                    type("BP", (), {"symbol": sym, "quantity": fill.quantity, "average_price": fill.price})(),
                )


def _handle_sell_fill(session_date: date, fill, repo: Repository, adopt_broker_truth: bool) -> None:
    if not adopt_broker_truth:
        return
    positions = [p for p in repo.get_open_positions() if p.symbol == fill.symbol]
    if not positions:
        return
    pos = positions[0]
    repo.update_trade(
        pos.trade_id,
        is_active=0,
        exit_date=session_date.isoformat(),
    )


def compute_equity(
    settled_cash_inr: float,
    open_positions: list[OpenPosition],
    price_map: dict[str, float],
) -> float:
    equity = settled_cash_inr
    for pos in open_positions:
        mark = price_map.get(pos.symbol, pos.entry_price)
        equity += mark * pos.quantity
    return equity
