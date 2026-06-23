"""Execute planned GTT actions against the broker with idempotency."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from src.broker.base import GTTBrokerClient
from src.broker.instruments import InstrumentResolver
from src.broker.upstox import UpstoxAPIError
from src.models import ActionType, PlannedGTTAction
from src.repository.base import Repository

logger = logging.getLogger(__name__)

_PENDING_KEY = "pending_gtts"
_EXECUTED_KEY = "executed_idempotency"


@dataclass
class ExecutionResult:
    action: PlannedGTTAction
    success: bool
    broker_gtt_id: str | None = None
    error: str | None = None


@dataclass
class ExecutionReport:
    session_date: date
    results: list[ExecutionResult] = field(default_factory=list)

    @property
    def failures(self) -> list[ExecutionResult]:
        return [r for r in self.results if not r.success]


class GTTExecutor:
    def __init__(
        self,
        broker: GTTBrokerClient,
        repo: Repository,
        instruments: InstrumentResolver,
        *,
        paper_mode: bool = True,
    ) -> None:
        self.broker = broker
        self.repo = repo
        self.instruments = instruments
        self.paper_mode = paper_mode

    def apply_planned_actions(
        self,
        session_date: date,
        actions: list[PlannedGTTAction],
    ) -> ExecutionReport:
        executed = self.repo.get_system_state(_EXECUTED_KEY) or {}
        pending = self.repo.get_system_state(_PENDING_KEY) or {}
        report = ExecutionReport(session_date=session_date)

        for action in actions:
            if action.action_type == ActionType.NO_CHANGE:
                continue
            if action.idempotency_key and executed.get(action.idempotency_key):
                logger.info("Skip duplicate action %s", action.idempotency_key)
                report.results.append(ExecutionResult(action=action, success=True))
                continue
            try:
                gtt_id = self._execute_one(action, pending, session_date)
                if action.idempotency_key:
                    executed[action.idempotency_key] = {
                        "date": session_date.isoformat(),
                        "gtt_order_id": gtt_id,
                        "action": action.action_type.value,
                    }
                report.results.append(
                    ExecutionResult(action=action, success=True, broker_gtt_id=gtt_id)
                )
            except Exception as exc:  # noqa: BLE001 — per-action isolation
                logger.exception("Failed %s %s", action.action_type, action.symbol)
                report.results.append(
                    ExecutionResult(action=action, success=False, error=str(exc))
                )

        self.repo.set_system_state(_PENDING_KEY, pending)
        self.repo.set_system_state(_EXECUTED_KEY, executed)
        return report

    def _execute_one(
        self,
        action: PlannedGTTAction,
        pending: dict[str, dict],
        session_date: date,
    ) -> str | None:
        sym = action.symbol
        token = self.instruments.resolve(sym)

        if action.action_type == ActionType.PLACE_BUY_GTT:
            existing = pending.get(sym)
            if existing and existing.get("gtt_order_id"):
                self.broker.cancel_gtt(str(existing["gtt_order_id"]))
            gtt_id = self.broker.place_buy_gtt(action, token)
            pending[sym] = {
                "gtt_order_id": gtt_id,
                "trigger_price": action.trigger_price,
                "stop_loss_price": action.stop_loss_price,
                "target_price": action.target_price,
                "quantity": action.quantity,
                "placed_date": session_date.isoformat(),
                "entry_box_top": action.entry_box_top,
                "entry_box_bottom": action.entry_box_bottom,
                "status": "ACTIVE",
            }
            return gtt_id

        if action.action_type == ActionType.CANCEL_BUY_GTT:
            existing = pending.pop(sym, None)
            if existing and existing.get("gtt_order_id"):
                self.broker.cancel_gtt(str(existing["gtt_order_id"]))
            return None

        if action.action_type in (ActionType.ESTABLISH_OCO, ActionType.TRAIL_OCO):
            return self._handle_oco_action(action, token)

        raise UpstoxAPIError(f"Unsupported action {action.action_type}")

    def _handle_oco_action(self, action: PlannedGTTAction, token: str) -> str:
        sym = action.symbol
        positions = [p for p in self.repo.get_open_positions() if p.symbol == sym]
        if not positions:
            raise UpstoxAPIError(f"No open position for {action.action_type.value} {sym}")
        pos = positions[0]
        qty = action.quantity or pos.quantity
        oco_map = self.repo.get_system_state("oco_by_symbol") or {}
        oco_id = oco_map.get(sym)

        if action.action_type == ActionType.TRAIL_OCO and oco_id:
            self.broker.modify_oco_sell(
                oco_id,
                action.stop_loss_price,
                action.target_price,
                qty,
            )
            if pos.trade_id:
                self.repo.update_trade(
                    pos.trade_id,
                    current_stop_loss=action.stop_loss_price,
                    current_target=action.target_price,
                )
            return oco_id

        gtt_id = self.broker.place_oco_sell(
            sym,
            token,
            qty,
            action.stop_loss_price,
            action.target_price,
            action.idempotency_key,
        )
        oco_map[sym] = gtt_id
        self.repo.set_system_state("oco_by_symbol", oco_map)
        if pos.trade_id:
            self.repo.update_trade(
                pos.trade_id,
                gtt_position_oco_id=gtt_id,
                current_stop_loss=action.stop_loss_price,
                current_target=action.target_price,
            )
        return gtt_id
