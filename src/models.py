"""Core Pydantic models — REQUIREMENTS v1.2 Section 4."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class BoxStateEnum(str, Enum):
    SCANNING = "SCANNING"
    FORMING = "FORMING"
    VALIDATED = "VALIDATED"
    BREAKOUT = "BREAKOUT"


class ActionType(str, Enum):
    PLACE_BUY_GTT = "PLACE_BUY_GTT"
    CANCEL_BUY_GTT = "CANCEL_BUY_GTT"
    ESTABLISH_OCO = "ESTABLISH_OCO"
    TRAIL_OCO = "TRAIL_OCO"
    NO_CHANGE = "NO_CHANGE"


class ExitReason(str, Enum):
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    TARGET_HIT = "TARGET_HIT"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"


class SkipReason(str, Enum):
    MAX_POSITIONS = "MAX_POSITIONS"
    SECTOR_CAP = "SECTOR_CAP"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    RANKED_OUT = "RANKED_OUT"
    KILL_SWITCH = "KILL_SWITCH"
    STRUCTURAL_R_BELOW_MIN = "STRUCTURAL_R_BELOW_MIN"
    BOX_RESET_REQUIRED = "BOX_RESET_REQUIRED"
    STALE_BREAKOUT = "STALE_BREAKOUT"
    BREAKOUT_FAILED = "BREAKOUT_FAILED"


class OpenPosition(BaseModel):
    symbol: str
    quantity: int
    entry_price: float
    current_stop_loss: float
    current_target: float
    initial_stop_loss: float | None = None
    initial_target: float | None = None
    sector: str = ""
    is_active: bool = True
    trade_id: str = ""
    entry_date: date | None = None
    entry_box_top: float | None = None
    entry_box_bottom: float | None = None
    hold_anchor_date: date | None = None
    stale_escalation_active: bool = False


class MarketContext(BaseModel):
    target_date: date
    account_equity: float
    settled_cash_inr: float
    open_positions: list[OpenPosition] = Field(default_factory=list)
    kill_switch_active: bool = False
    symbols_with_oco: set[str] = Field(default_factory=set)


class BoxState(BaseModel):
    symbol: str
    box_state: BoxStateEnum = BoxStateEnum.SCANNING
    box_top: float | None = None
    box_bottom: float | None = None
    box_start_date: date | None = None
    box_end_date: date | None = None
    volume_sma_20: float | None = None
    days_in_box: int = 0
    reversal_high: float | None = None
    last_close: float | None = None
    breakout_date: date | None = None


class PointInTimeFundamentals(BaseModel):
    symbol: str
    effective_date: date
    metrics: dict[str, float] = Field(default_factory=dict)


class PlannedGTTAction(BaseModel):
    symbol: str
    action_type: ActionType
    trigger_price: float = 0.0
    stop_loss_price: float = 0.0
    target_price: float = 0.0
    quantity: int = 0
    idempotency_key: str = ""
    entry_box_top: float | None = None
    entry_box_bottom: float | None = None


class DecisionLogRow(BaseModel):
    date: date
    symbol: str
    box_state: str
    box_top: float | None = None
    box_bottom: float | None = None
    filter_pass: bool = False
    filter_fail_reason: str | None = None
    structural_rr: float | None = None
    rank: int | None = None
    selected: bool = False
    action_type: str = "NO_CHANGE"
    skip_reason: str | None = None
    trigger_price: float | None = None
    stop_loss_price: float | None = None
    target_price: float | None = None
    quantity: int | None = None


class TradeLedgerRow(BaseModel):
    trade_id: str
    timestamp: datetime | None = None
    symbol: str
    direction: Literal["BUY", "SELL"]
    price: float
    quantity: int
    current_stop_loss: float | None = None
    current_target: float | None = None
    structural_rr_at_entry: float | None = None
    gtt_buy_trigger_id: str | None = None
    gtt_position_oco_id: str | None = None
    oco_pending_review: bool = False
    is_active: bool = True
    exit_reason: ExitReason | None = None
    entry_date: date | None = None
    exit_date: date | None = None


class DailyBar(BaseModel):
    symbol: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover_inr: float = 0.0


class PendingBuyOrder(BaseModel):
    symbol: str
    trigger_price: float
    stop_loss_price: float
    target_price: float
    quantity: int
    placed_date: date
    entry_box_top: float | None = None
    entry_box_bottom: float | None = None


class BreakoutCandidate(BaseModel):
    symbol: str
    box_top: float
    box_bottom: float
    entry_price: float
    trigger_price: float
    stop_loss_price: float
    target_price: float
    structural_rr: float
    sector: str
    sector_rs_percentile: float = 0.0
    breakout_volume_ratio: float = 1.0
    quantity: int = 0


def make_idempotency_key(symbol: str, target_date: date, action_type: str) -> str:
    raw = f"{symbol}|{target_date.isoformat()}|{action_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
