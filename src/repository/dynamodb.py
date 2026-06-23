"""DynamoDB repository for production live runs — REQUIREMENTS v1.2 Section 5."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src.models import BoxState, DecisionLogRow, OpenPosition, TradeLedgerRow
from src.repository.base import Repository


class DynamoDBRepository(Repository):
    """Production persistence (Lambda). Implement when deploying to AWS."""

    def __init__(self, table_prefix: str = "swinger") -> None:
        self.table_prefix = table_prefix
        raise NotImplementedError(
            "DynamoDBRepository is not implemented yet. "
            "Use SqliteLiveRepository via live.local_db_path for local runs."
        )

    def get_state_registry(self) -> dict[str, BoxState]:
        raise NotImplementedError

    def upsert_state_registry(self, registry: dict[str, BoxState]) -> None:
        raise NotImplementedError

    def get_open_positions(self) -> list[OpenPosition]:
        raise NotImplementedError

    def record_trade(self, trade: TradeLedgerRow) -> None:
        raise NotImplementedError

    def update_trade(self, trade_id: str, **fields: object) -> None:
        raise NotImplementedError

    def get_system_state(self, key: str) -> dict:
        raise NotImplementedError

    def set_system_state(self, key: str, value: dict) -> None:
        raise NotImplementedError

    def get_fundamentals_pit(self, symbol: str, as_of: date) -> dict[str, float]:
        raise NotImplementedError

    def get_daily_bars(self, symbol: str, end: date, days: int) -> pd.DataFrame:
        raise NotImplementedError

    def append_decision_log(self, rows: list[DecisionLogRow]) -> None:
        raise NotImplementedError
