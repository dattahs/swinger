"""Abstract GTT broker client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from src.broker.types import BrokerSnapshot
from src.models import PlannedGTTAction


class GTTBrokerClient(ABC):
    """Pluggable broker API for live GTT execution."""

    @abstractmethod
    def fetch_snapshot(
        self,
        session_date: date,
        *,
        tracked_gtt_ids: list[str],
        symbols: list[str] | None = None,
    ) -> BrokerSnapshot: ...

    @abstractmethod
    def place_buy_gtt(
        self,
        action: PlannedGTTAction,
        instrument_token: str,
    ) -> str:
        """Place entry GTT; returns broker gtt_order_id."""

    @abstractmethod
    def cancel_gtt(self, gtt_order_id: str) -> None: ...

    @abstractmethod
    def place_oco_sell(
        self,
        symbol: str,
        instrument_token: str,
        quantity: int,
        stop_loss_price: float,
        target_price: float,
        idempotency_key: str,
    ) -> str:
        """Place or replace OCO exit GTT; returns gtt_order_id."""

    @abstractmethod
    def modify_oco_sell(
        self,
        gtt_order_id: str,
        stop_loss_price: float,
        target_price: float,
        quantity: int,
    ) -> None: ...
