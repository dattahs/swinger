"""Greedy candidate selection — REQUIREMENTS v1.2 Section 7."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.config import AppConfig
from src.debug_log import humanize_skip_reason
from src.models import ActionType, BreakoutCandidate, MarketContext, OpenPosition, PlannedGTTAction, SkipReason, make_idempotency_key

if TYPE_CHECKING:
    from src.debug_log import ActionDebugLogger


def _sector_exposure(
    positions: list[OpenPosition],
    sector: str,
    last_closes: dict[str, float],
    equity: float,
) -> float:
    if equity <= 0:
        return 0.0
    total = 0.0
    for p in positions:
        if p.sector == sector:
            px = last_closes.get(p.symbol, p.entry_price)
            total += p.quantity * px
    return total / equity


def select_candidates(
    candidates: list[BreakoutCandidate],
    context: MarketContext,
    cfg: AppConfig,
    last_closes: dict[str, float],
    pending_symbols: set[str],
    debug: ActionDebugLogger | None = None,
    *,
    breakout_reentry_blocked: set[str] | None = None,
) -> tuple[list[PlannedGTTAction], dict[str, SkipReason]]:
    rm = cfg.risk_management
    breakout_reentry_blocked = breakout_reentry_blocked or set()
    skip_map: dict[str, SkipReason] = {}
    actions: list[PlannedGTTAction] = []

    if context.kill_switch_active:
        for c in candidates:
            skip_map[c.symbol] = SkipReason.KILL_SWITCH
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.KILL_SWITCH.value,
                    humanize_skip_reason(SkipReason.KILL_SWITCH.value),
                )
        return actions, skip_map

    if context.sector_regime_gate_active:
        for c in candidates:
            skip_map[c.symbol] = SkipReason.SECTOR_REGIME_GATE
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.SECTOR_REGIME_GATE.value,
                    humanize_skip_reason(SkipReason.SECTOR_REGIME_GATE.value),
                )
        return actions, skip_map

    open_positions = [p for p in context.open_positions if p.is_active]
    open_count = len(open_positions)
    if open_count >= rm.max_concurrent_positions:
        for c in candidates:
            skip_map[c.symbol] = SkipReason.MAX_POSITIONS
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.MAX_POSITIONS.value,
                    humanize_skip_reason(SkipReason.MAX_POSITIONS.value),
                )
        return actions, skip_map

    sized = [c for c in candidates if c.quantity >= 1]
    for c in candidates:
        if c.quantity < 1 and c.symbol not in skip_map:
            skip_map[c.symbol] = SkipReason.INSUFFICIENT_CASH
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.INSUFFICIENT_CASH.value,
                    humanize_skip_reason(SkipReason.INSUFFICIENT_CASH.value),
                )

    sized.sort(
        key=lambda c: (
            -c.structural_rr,
            -c.sector_rs_percentile,
            -c.breakout_volume_ratio,
        )
    )

    selected_count = 0
    overcommit = max(1.0, rm.gtt_capital_overcommit_factor)
    remaining_cash = context.settled_cash_inr * overcommit
    working_positions = list(open_positions)

    for rank, c in enumerate(sized, start=1):
        if open_count + selected_count >= rm.max_concurrent_positions:
            skip_map[c.symbol] = SkipReason.MAX_POSITIONS
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.MAX_POSITIONS.value,
                    humanize_skip_reason(SkipReason.MAX_POSITIONS.value),
                )
            continue
        if c.symbol in pending_symbols:
            skip_map[c.symbol] = SkipReason.RANKED_OUT
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.RANKED_OUT.value,
                    f"{c.symbol} already has a pending GTT buy order",
                )
            continue
        if rm.require_box_reset_for_reentry and c.symbol in breakout_reentry_blocked:
            skip_map[c.symbol] = SkipReason.BOX_RESET_REQUIRED
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.BOX_RESET_REQUIRED.value,
                    humanize_skip_reason(SkipReason.BOX_RESET_REQUIRED.value),
                )
            continue
        cost = c.quantity * c.entry_price
        if cost > remaining_cash:
            skip_map[c.symbol] = SkipReason.INSUFFICIENT_CASH
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.INSUFFICIENT_CASH.value,
                    f"Trade cost {cost:,.0f} exceeds remaining cash {remaining_cash:,.0f}",
                )
            continue
        sector_mtm = _sector_exposure(working_positions, c.sector, last_closes, context.account_equity)
        if (sector_mtm * context.account_equity + cost) / context.account_equity > rm.max_sector_exposure_pct / 100:
            skip_map[c.symbol] = SkipReason.SECTOR_CAP
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.SECTOR_CAP.value,
                    humanize_skip_reason(SkipReason.SECTOR_CAP.value),
                    sector=c.sector,
                )
            continue

        actions.append(
            PlannedGTTAction(
                symbol=c.symbol,
                action_type=ActionType.PLACE_BUY_GTT,
                trigger_price=c.trigger_price,
                stop_loss_price=c.stop_loss_price,
                target_price=c.target_price,
                quantity=c.quantity,
                idempotency_key=make_idempotency_key(
                    c.symbol, context.target_date, ActionType.PLACE_BUY_GTT.value
                ),
                entry_box_top=c.box_top,
                entry_box_bottom=c.box_bottom,
            )
        )
        if debug:
            debug.select(context.target_date, c.symbol, rank, c.structural_rr, c.quantity)
        remaining_cash -= cost
        selected_count += 1
        working_positions.append(
            OpenPosition(
                symbol=c.symbol,
                quantity=c.quantity,
                entry_price=c.entry_price,
                current_stop_loss=c.stop_loss_price,
                current_target=c.target_price,
                sector=c.sector,
            )
        )

    selected_syms = {a.symbol for a in actions}
    for c in sized:
        if c.symbol not in selected_syms and c.symbol not in skip_map:
            skip_map[c.symbol] = SkipReason.RANKED_OUT
            if debug:
                debug.reject(
                    context.target_date,
                    c.symbol,
                    "RANK",
                    SkipReason.RANKED_OUT.value,
                    humanize_skip_reason(SkipReason.RANKED_OUT.value),
                )

    return actions, skip_map
