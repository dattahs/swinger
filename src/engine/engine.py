"""Strategy orchestrator — REQUIREMENTS v1.2 Section 4."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

from src.config import AppConfig
from src.debug_log import humanize_filter_reason, humanize_skip_reason
from src.engine.adaptive_lookback import resolve_new_high_lookback_sessions
from src.engine.darvas import update_box_state
from src.engine.entry_safety import check_entry_safety
from src.engine.filters import (
    check_fundamental_filters,
    check_universe_filters,
    index_trend_ok,
    symbol_trend_ok,
)
from src.engine.ranking import select_candidates
from src.engine.sector_rs import compute_sector_rs_percentiles
from src.data.sector_etfs import SECTOR_ETF_SYMBOLS, SECTOR_INDEX_SYMBOLS
from src.engine.risk import (
    compute_dynamic_atr_target_action,
    compute_entry_prices,
    compute_structural_rr,
    compute_trail_action,
    count_hold_sessions,
    passes_structural_r_min,
    size_position,
)
from src.models import (
    ActionType,
    BoxState,
    BoxStateEnum,
    BreakoutCandidate,
    DecisionLogRow,
    MarketContext,
    PlannedGTTAction,
    SkipReason,
    make_idempotency_key,
)
from src.repository.sqlite import SqliteDataLake

if TYPE_CHECKING:
    from src.debug_log import ActionDebugLogger


class PriceDataMatrix:
    """OHLCV bars keyed by symbol through target_date."""

    def __init__(self, bars: dict[str, pd.DataFrame], index_bars: pd.DataFrame) -> None:
        self.bars = bars
        self.index_bars = index_bars

    def get(self, symbol: str) -> pd.DataFrame:
        return self.bars.get(symbol, pd.DataFrame())


def run_daily_strategy_iteration(
    context: MarketContext,
    price_data: PriceDataMatrix,
    data_lake: SqliteDataLake,
    state_registry: dict[str, BoxState],
    config: AppConfig,
    universe: list[str],
    pending_symbols: set[str] | None = None,
    debug: ActionDebugLogger | None = None,
    *,
    prev_box_states: dict[str, str] | None = None,
    breakout_reentry_blocked: set[str] | None = None,
    trading_days_to_date: list[date] | None = None,
) -> tuple[list[PlannedGTTAction], dict[str, BoxState], list[DecisionLogRow]]:
    pending_symbols = pending_symbols or set()
    prev_box_states = prev_box_states or {}
    breakout_reentry_blocked = breakout_reentry_blocked if breakout_reentry_blocked is not None else set()
    trading_days_to_date = trading_days_to_date or []
    actions: list[PlannedGTTAction] = []
    decision_rows: list[DecisionLogRow] = []
    trend_ok = index_trend_ok(price_data.index_bars, config)
    sector_etf_bars = {etf: price_data.get(etf) for etf in SECTOR_ETF_SYMBOLS}
    sector_index_bars = {
        index_sym: price_data.get(index_sym) for index_sym in SECTOR_INDEX_SYMBOLS.values()
    }
    sector_labels = {data_lake.get_sector(s) for s in universe}
    sector_rs_map = compute_sector_rs_percentiles(
        sector_labels,
        sector_index_bars,
        price_data.index_bars,
        config.candidate_ranking.sector_rs_lookback_days,
    )

    index_close = None
    if not price_data.index_bars.empty:
        index_close = float(price_data.index_bars.iloc[-1]["close"])
    trend_mode = config.darvas_box.market_trend_filter.mode
    sector_trend_bullish = 0

    open_by_symbol = {p.symbol: p for p in context.open_positions if p.is_active}
    last_closes: dict[str, float] = {}

    acfg = config.universe_filters.adaptive_new_high_lookback
    regime_index = acfg.regime_index if acfg.enabled else config.darvas_box.market_trend_filter.index
    if regime_index == config.darvas_box.market_trend_filter.index:
        regime_bars = price_data.index_bars
    else:
        regime_bars = data_lake.get_daily_bars(regime_index, context.target_date, 252 * 6)
    session_lookback, _lookback_meta = resolve_new_high_lookback_sessions(
        regime_bars, config, context.target_date
    )

    for symbol in universe:
        bars = price_data.get(symbol)
        if bars.empty:
            continue
        bar_date = bars.iloc[-1]["date"]
        if bar_date != context.target_date:
            if debug and config.backtest.debug_log.include_gate_rejections:
                debug.reject(
                    context.target_date,
                    symbol,
                    "GATE",
                    "STALE_BARS",
                    f"{symbol}: latest bar {bar_date} != session {context.target_date}",
                    bar_date=str(bar_date),
                    expected_session=str(context.target_date),
                )
            continue
        last_closes[symbol] = float(bars.iloc[-1]["close"])

        state = state_registry.get(symbol, BoxState(symbol=symbol))
        has_pos = symbol in open_by_symbol
        sector_label = data_lake.get_sector(symbol)
        sym_trend_ok = symbol_trend_ok(
            symbol,
            sector_label,
            price_data.index_bars,
            sector_etf_bars,
            sector_index_bars,
            config,
        )
        if sym_trend_ok:
            sector_trend_bullish += 1
        state = update_box_state(
            state, bars, config, sym_trend_ok, has_pos, context.target_date, debug,
            new_high_lookback_sessions=session_lookback,
        )
        state_registry[symbol] = state

        if has_pos:
            pos = open_by_symbol[symbol]
            planned_stop = pos.initial_stop_loss if pos.initial_stop_loss is not None else pos.current_stop_loss
            planned_target = pos.initial_target if pos.initial_target is not None else pos.current_target
            if symbol not in context.symbols_with_oco and planned_stop > 0:
                actions.append(
                    PlannedGTTAction(
                        symbol=symbol,
                        action_type=ActionType.ESTABLISH_OCO,
                        stop_loss_price=planned_stop,
                        target_price=planned_target,
                        quantity=pos.quantity,
                        idempotency_key=make_idempotency_key(
                            symbol, context.target_date, ActionType.ESTABLISH_OCO.value
                        ),
                    )
                )
            anchor = pos.hold_anchor_date or pos.entry_date
            hold_sessions = (
                count_hold_sessions(anchor, context.target_date, trading_days_to_date)
                if anchor is not None
                else 0
            )
            trail = compute_trail_action(
                pos,
                state,
                context.account_equity,
                context.target_date,
                config,
                hold_sessions=hold_sessions,
                last_close=last_closes.get(symbol),
            )
            if trail:
                actions.append(trail)
                if debug:
                    if pos.stale_escalation_active:
                        reason = "STALE_BOX_TSL"
                    elif config.r_managed_runner.enabled and trail.stop_loss_price >= pos.entry_price:
                        reason = "R_MANAGED_BREAKEVEN"
                    else:
                        reason = "TRAIL"
                    debug.log(
                        context.target_date,
                        "RISK",
                        reason,
                        f"Trail stop for {symbol} to {trail.stop_loss_price:.2f}",
                        symbol=symbol,
                        hold_sessions=hold_sessions,
                    )
            dyn_target = compute_dynamic_atr_target_action(
                pos, state, bars, config, context.target_date
            )
            if dyn_target:
                actions.append(dyn_target)
                if debug:
                    debug.log(
                        context.target_date,
                        "RISK",
                        "ATR_DYNAMIC_TARGET",
                        f"Raise target for {symbol} to {dyn_target.target_price:.2f}",
                        symbol=symbol,
                    )

    for symbol in universe:
        prev_str = prev_box_states.get(symbol)
        if not prev_str:
            continue
        curr_state = state_registry.get(symbol)
        if curr_state is None:
            continue
        if (
            prev_str == BoxStateEnum.BREAKOUT.value
            and curr_state.box_state == BoxStateEnum.SCANNING
        ):
            breakout_reentry_blocked.discard(symbol)
            if symbol in pending_symbols:
                actions.append(
                    PlannedGTTAction(
                        symbol=symbol,
                        action_type=ActionType.CANCEL_BUY_GTT,
                        idempotency_key=make_idempotency_key(
                            symbol, context.target_date, ActionType.CANCEL_BUY_GTT.value
                        ),
                    )
                )
                if debug:
                    debug.log(
                        context.target_date,
                        "BOX",
                        "RESET_CANCEL_GTT",
                        f"Box reset to SCANNING — cancelled pending GTT for {symbol}",
                        symbol=symbol,
                    )

    if debug:
        debug.session_start(
            context.target_date,
            trend_ok=trend_ok,
            universe_size=len(universe),
            index_close=index_close,
            trend_mode=trend_mode,
            sector_trend_bullish=sector_trend_bullish,
        )
        if trend_mode == "nifty" and not trend_ok:
            debug.log(
                context.target_date,
                "SESSION",
                "FILTER",
                "NIFTY trend filter failed — index not above all required moving averages",
            )
        elif trend_mode == "sector_index":
            debug.log(
                context.target_date,
                "SESSION",
                "FILTER",
                f"Sector-index trend mode — {sector_trend_bullish}/{len(universe)} "
                "symbols in bullish sector (index/ETF above MAs)",
                details={
                    "sector_trend_bullish": sector_trend_bullish,
                    "universe_size": len(universe),
                },
            )

    breakout_candidates: list[BreakoutCandidate] = []

    for symbol in universe:
        state = state_registry.get(symbol, BoxState(symbol=symbol))
        bars = price_data.get(symbol)
        u_pass, u_reason = check_universe_filters(symbol, bars, context.target_date, data_lake, config)
        f_pass, f_reason = (False, u_reason) if not u_pass else check_fundamental_filters(
            symbol, context.target_date, data_lake, config
        )
        filter_pass = u_pass and f_pass
        fail_reason = u_reason or f_reason

        if filter_pass and state.box_state == BoxStateEnum.BREAKOUT and not bars.empty:
            safe, safety_reason = check_entry_safety(
                bars, config, breakout_date=state.breakout_date
            )
            if not safe:
                filter_pass = False
                fail_reason = safety_reason
                if debug:
                    debug.reject(
                        context.target_date,
                        symbol,
                        "ENTRY",
                        safety_reason or "ENTRY_UNSAFE",
                        humanize_filter_reason(safety_reason),
                    )

        structural_rr: float | None = None
        skip_reason: str | None = None
        action_type = ActionType.NO_CHANGE.value
        selected = False
        rank: int | None = None
        trigger = stop = target = None
        qty = None

        if state.box_state == BoxStateEnum.BREAKOUT and symbol not in open_by_symbol:
            if debug:
                if state.box_top is not None and state.box_bottom is not None:
                    debug.consider_breakout(
                        context.target_date, symbol, state.box_top, state.box_bottom
                    )
                else:
                    debug.consider_breakout(context.target_date, symbol, 0.0, 0.0)

            if not filter_pass and debug:
                debug.reject(
                    context.target_date,
                    symbol,
                    "FILTER",
                    fail_reason or "FILTER_FAIL",
                    humanize_filter_reason(fail_reason),
                )
            elif filter_pass and debug and context.kill_switch_active:
                debug.reject(
                    context.target_date,
                    symbol,
                    "RISK",
                    SkipReason.KILL_SWITCH.value,
                    humanize_skip_reason(SkipReason.KILL_SWITCH.value),
                )

        if state.box_state == BoxStateEnum.BREAKOUT and filter_pass and symbol not in open_by_symbol:
            close = last_closes.get(symbol)
            if close is None and not bars.empty:
                close = float(bars.iloc[-1]["close"])
            if (
                config.risk_management.require_box_reset_for_reentry
                and symbol in breakout_reentry_blocked
            ):
                skip_reason = SkipReason.BOX_RESET_REQUIRED.value
                if debug:
                    debug.reject(
                        context.target_date,
                        symbol,
                        "BOX",
                        SkipReason.BOX_RESET_REQUIRED.value,
                        "Re-entry blocked until box resets to SCANNING",
                    )
            elif state.box_top is None or state.box_bottom is None:
                fail_reason = fail_reason or "NO_BOX"
                if debug:
                    debug.reject(context.target_date, symbol, "BOX", "NO_BOX")
            elif close is None:
                fail_reason = fail_reason or "NO_CLOSE"
            elif close < state.box_bottom:
                skip_reason = SkipReason.BREAKOUT_FAILED.value
                if debug:
                    debug.reject(
                        context.target_date,
                        symbol,
                        "BOX",
                        SkipReason.BREAKOUT_FAILED.value,
                        f"Close {close:.2f} below box bottom {state.box_bottom:.2f}",
                    )
            elif close > state.box_top * (
                1 + config.darvas_box.breakout_reset_above_top_pct / 100
            ):
                skip_reason = SkipReason.STALE_BREAKOUT.value
                if debug:
                    debug.reject(
                        context.target_date,
                        symbol,
                        "BOX",
                        SkipReason.STALE_BREAKOUT.value,
                        f"Close {close:.2f} too far above box top {state.box_top:.2f}",
                    )
            else:
                entry, trigger_p, stop_p, target_p = compute_entry_prices(
                    state.box_top, state.box_bottom, config
                )
                structural_rr = compute_structural_rr(entry, stop_p, target_p)
                if not passes_structural_r_min(structural_rr, config):
                    skip_reason = SkipReason.STRUCTURAL_R_BELOW_MIN.value
                    if debug:
                        debug.reject(
                            context.target_date,
                            symbol,
                            "RISK",
                            skip_reason,
                            f"Structural RR {structural_rr:.4f} below minimum "
                            f"{config.risk_management.min_structural_r_ratio}",
                            structural_rr=structural_rr,
                        )
                else:
                    sized = size_position(
                        entry, stop_p, context.account_equity, context.settled_cash_inr, config
                    )
                    vol_sma = state.volume_sma_20 or 1.0
                    vol = int(bars.iloc[-1]["volume"]) if not bars.empty else 0
                    if sized is None and debug:
                        debug.reject(
                            context.target_date,
                            symbol,
                            "RISK",
                            SkipReason.INSUFFICIENT_CASH.value,
                            "Position size rounded to zero or exceeds cash/risk limits",
                        )
                    breakout_candidates.append(
                        BreakoutCandidate(
                            symbol=symbol,
                            box_top=state.box_top,
                            box_bottom=state.box_bottom,
                            entry_price=entry,
                            trigger_price=trigger_p,
                            stop_loss_price=stop_p,
                            target_price=target_p,
                            structural_rr=structural_rr,
                            sector=data_lake.get_sector(symbol),
                            sector_rs_percentile=sector_rs_map.get(
                                data_lake.get_sector(symbol), 0.0
                            ),
                            breakout_volume_ratio=vol / vol_sma if vol_sma else 1.0,
                            quantity=sized or 0,
                        )
                    )

        decision_rows.append(
            DecisionLogRow(
                date=context.target_date,
                symbol=symbol,
                box_state=state.box_state.value,
                box_top=state.box_top,
                box_bottom=state.box_bottom,
                filter_pass=filter_pass,
                filter_fail_reason=fail_reason,
                structural_rr=structural_rr,
                rank=rank,
                selected=selected,
                action_type=action_type,
                skip_reason=skip_reason,
                trigger_price=trigger,
                stop_loss_price=stop,
                target_price=target,
                quantity=qty,
            )
        )

    buy_actions, skip_map = select_candidates(
        breakout_candidates,
        context,
        config,
        last_closes,
        pending_symbols,
        debug,
        breakout_reentry_blocked=breakout_reentry_blocked,
    )
    actions.extend(buy_actions)

    if config.risk_management.require_box_reset_for_reentry:
        for action in buy_actions:
            if action.action_type == ActionType.PLACE_BUY_GTT:
                breakout_reentry_blocked.add(action.symbol)

    selected_symbols = {a.symbol for a in buy_actions}
    for row in decision_rows:
        for c in breakout_candidates:
            if row.symbol != c.symbol:
                continue
            row.structural_rr = c.structural_rr
            row.trigger_price = c.trigger_price
            row.stop_loss_price = c.stop_loss_price
            row.target_price = c.target_price
            row.quantity = c.quantity
            if c.symbol in selected_symbols:
                row.selected = True
                row.action_type = ActionType.PLACE_BUY_GTT.value
            elif c.symbol in skip_map:
                row.skip_reason = skip_map[c.symbol].value
                row.action_type = ActionType.NO_CHANGE.value

    ranked = sorted(
        breakout_candidates,
        key=lambda c: (-c.structural_rr, -c.sector_rs_percentile, -c.breakout_volume_ratio),
    )
    rank_lookup = {c.symbol: i for i, c in enumerate(ranked, start=1)}
    for row in decision_rows:
        if row.symbol in rank_lookup:
            row.rank = rank_lookup[row.symbol]

    return actions, state_registry, decision_rows
