"""GTT fill simulation â€” REQUIREMENTS v1.2 Section 8."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from src.models import (
    ActionType,
    ExitReason,
    OpenPosition,
    PendingBuyOrder,
    PlannedGTTAction,
    TradeLedgerRow,
    make_idempotency_key,
)


@dataclass
class VirtualPortfolio:
    settled_cash: float
    unsettled_proceeds: list[tuple[date, float]] = field(default_factory=list)
    pending_buys: dict[str, PendingBuyOrder] = field(default_factory=dict)
    positions: dict[str, OpenPosition] = field(default_factory=dict)
    closed_trades: list[dict] = field(default_factory=list)


class VirtualBroker:
    def __init__(self, slippage_pct: float) -> None:
        self.slippage = slippage_pct / 100.0
        self.portfolio = VirtualPortfolio(settled_cash=0.0)

    def set_initial_cash(self, amount: float) -> None:
        self.portfolio.settled_cash = amount

    def apply_actions(self, session_date: date, actions: list[PlannedGTTAction]) -> None:
        for action in actions:
            if action.action_type == ActionType.PLACE_BUY_GTT:
                self.portfolio.pending_buys[action.symbol] = PendingBuyOrder(
                    symbol=action.symbol,
                    trigger_price=action.trigger_price,
                    stop_loss_price=action.stop_loss_price,
                    target_price=action.target_price,
                    quantity=action.quantity,
                    placed_date=session_date,
                    entry_box_top=action.entry_box_top,
                    entry_box_bottom=action.entry_box_bottom,
                )
            elif action.action_type == ActionType.CANCEL_BUY_GTT:
                self.portfolio.pending_buys.pop(action.symbol, None)
            elif action.action_type == ActionType.TRAIL_OCO:
                pos = self.portfolio.positions.get(action.symbol)
                if pos:
                    pos.current_stop_loss = action.stop_loss_price
                    if action.target_price > 0:
                        pos.current_target = action.target_price

    def expire_stale_pending_buys(
        self,
        session_date: date,
        max_sessions: int,
        trading_days: list[date],
    ) -> list[PlannedGTTAction]:
        """Cancel buy GTTs that have been pending longer than max_sessions trading days."""
        cancels: list[PlannedGTTAction] = []
        for symbol, order in list(self.portfolio.pending_buys.items()):
            sessions_waiting = count_sessions_waiting(order.placed_date, session_date, trading_days)
            if sessions_waiting > max_sessions:
                cancels.append(
                    PlannedGTTAction(
                        symbol=symbol,
                        action_type=ActionType.CANCEL_BUY_GTT,
                        idempotency_key=make_idempotency_key(
                            symbol, session_date, ActionType.CANCEL_BUY_GTT.value
                        ),
                    )
                )
        return cancels

    def settle_cash(self, session_date: date) -> None:
        still_pending: list[tuple[date, float]] = []
        for avail_date, amount in self.portfolio.unsettled_proceeds:
            if avail_date <= session_date:
                self.portfolio.settled_cash += amount
            else:
                still_pending.append((avail_date, amount))
        self.portfolio.unsettled_proceeds = still_pending

    def process_session(
        self,
        session_date: date,
        bars: dict[str, pd.Series],
    ) -> list[TradeLedgerRow]:
        """Process fills and exits for session using daily H/L."""
        events: list[TradeLedgerRow] = []
        self.settle_cash(session_date)

        for symbol, order in list(self.portfolio.pending_buys.items()):
            bar = bars.get(symbol)
            if bar is None:
                continue
            if float(bar["high"]) >= order.trigger_price:
                fill_price = order.trigger_price * (1 + self.slippage)
                cost = fill_price * order.quantity
                if cost > self.portfolio.settled_cash:
                    continue
                self.portfolio.settled_cash -= cost
                trade_id = make_idempotency_key(symbol, session_date, "BUY_FILL")
                self.portfolio.positions[symbol] = OpenPosition(
                    symbol=symbol,
                    quantity=order.quantity,
                    entry_price=fill_price,
                    current_stop_loss=order.stop_loss_price,
                    current_target=order.target_price,
                    initial_stop_loss=order.stop_loss_price,
                    trade_id=trade_id,
                    entry_date=session_date,
                    entry_box_top=order.entry_box_top,
                    entry_box_bottom=order.entry_box_bottom,
                    hold_anchor_date=session_date,
                )
                events.append(
                    TradeLedgerRow(
                        trade_id=trade_id,
                        symbol=symbol,
                        direction="BUY",
                        price=fill_price,
                        quantity=order.quantity,
                        current_stop_loss=order.stop_loss_price,
                        current_target=order.target_price,
                        is_active=True,
                        entry_date=session_date,
                    )
                )
                del self.portfolio.pending_buys[symbol]

        for symbol, pos in list(self.portfolio.positions.items()):
            bar = bars.get(symbol)
            if bar is None:
                continue
            low = float(bar["low"])
            high = float(bar["high"])
            exit_price = None
            exit_reason = None
            if low <= pos.current_stop_loss:
                exit_price = pos.current_stop_loss * (1 - self.slippage)
                exit_reason = ExitReason.STOP_LOSS_HIT
            elif high >= pos.current_target:
                exit_price = pos.current_target * (1 - self.slippage)
                exit_reason = ExitReason.TARGET_HIT

            if exit_price is not None and exit_reason is not None:
                proceeds = exit_price * pos.quantity
                avail = session_date + timedelta(days=2)
                self.portfolio.unsettled_proceeds.append((avail, proceeds))
                events.append(
                    TradeLedgerRow(
                        trade_id=pos.trade_id,
                        symbol=symbol,
                        direction="SELL",
                        price=exit_price,
                        quantity=pos.quantity,
                        is_active=False,
                        exit_reason=exit_reason,
                        entry_date=None,
                        exit_date=session_date,
                    )
                )
                self.portfolio.closed_trades.append(
                    {
                        "trade_id": pos.trade_id,
                        "symbol": symbol,
                        "entry_date": pos.entry_date or session_date,
                        "exit_date": session_date,
                        "entry_price": pos.entry_price,
                        "exit_price": exit_price,
                        "quantity": pos.quantity,
                        "exit_reason": exit_reason.value,
                        "pnl": (exit_price - pos.entry_price) * pos.quantity,
                    }
                )
                del self.portfolio.positions[symbol]

        return events

    def mark_to_market(self, last_closes: dict[str, float]) -> float:
        equity = self.portfolio.settled_cash
        for sym, pos in self.portfolio.positions.items():
            px = last_closes.get(sym, pos.entry_price)
            equity += pos.quantity * px
        for _, amount in self.portfolio.unsettled_proceeds:
            equity += amount
        return equity

    def get_open_positions(self) -> list[OpenPosition]:
        return list(self.portfolio.positions.values())

    def pending_symbols(self) -> set[str]:
        return set(self.portfolio.pending_buys.keys())


def count_sessions_waiting(placed_date: date, session_date: date, trading_days: list[date]) -> int:
    return sum(1 for d in trading_days if placed_date <= d <= session_date)
