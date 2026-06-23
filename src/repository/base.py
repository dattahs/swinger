"""Abstract repository — REQUIREMENTS v1.2 Section 5."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from src.models import BoxState, OpenPosition, TradeLedgerRow


class Repository(ABC):
    @abstractmethod
    def get_state_registry(self) -> dict[str, BoxState]: ...

    @abstractmethod
    def upsert_state_registry(self, registry: dict[str, BoxState]) -> None: ...

    @abstractmethod
    def get_open_positions(self) -> list[OpenPosition]: ...

    @abstractmethod
    def record_trade(self, trade: TradeLedgerRow) -> None: ...

    @abstractmethod
    def update_trade(self, trade_id: str, **fields: object) -> None: ...

    @abstractmethod
    def get_system_state(self, key: str) -> dict: ...

    @abstractmethod
    def set_system_state(self, key: str, value: dict) -> None: ...

    @abstractmethod
    def get_fundamentals_pit(self, symbol: str, as_of: date) -> dict[str, float]: ...

    @abstractmethod
    def get_daily_bars(self, symbol: str, end: date, days: int) -> pd.DataFrame: ...

    @abstractmethod
    def append_decision_log(self, rows: list) -> None: ...
