"""Broker snapshot and reconciliation types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class GTTStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TRIGGERED = "TRIGGERED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


@dataclass
class BrokerFunds:
    available_cash_inr: float
    used_margin_inr: float = 0.0
    unsettled_proceeds_inr: float = 0.0
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerPosition:
    symbol: str
    quantity: int
    average_price: float
    product: str = "D"
    instrument_token: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerGTT:
    gtt_order_id: str
    symbol: str
    instrument_token: str
    transaction_type: str
    quantity: int
    trigger_price: float
    status: GTTStatus
    gtt_type: str = "SINGLE"
    stop_loss_price: float | None = None
    target_price: float | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerFill:
    symbol: str
    order_id: str
    trade_id: str
    transaction_type: str
    quantity: int
    price: float
    traded_at: datetime | None = None
    product: str = "D"
    raw: dict = field(default_factory=dict)


@dataclass
class BrokerSnapshot:
    as_of: datetime
    funds: BrokerFunds
    positions: list[BrokerPosition]
    gtt_orders: list[BrokerGTT]
    fills_today: list[BrokerFill]
    errors: list[str] = field(default_factory=list)


@dataclass
class ReconciliationDrift:
    kind: str
    symbol: str
    message: str
    broker_value: str = ""
    ledger_value: str = ""


@dataclass
class ReconciliationResult:
    session_date: date
    snapshot: BrokerSnapshot
    drifts: list[ReconciliationDrift] = field(default_factory=list)
    adopted_broker_truth: bool = False
    pending_symbols: set[str] = field(default_factory=set)
    settled_cash_inr: float = 0.0
    unsettled_proceeds_inr: float = 0.0
    open_positions_synced: int = 0

    @property
    def is_synced(self) -> bool:
        return not self.drifts or self.adopted_broker_truth
