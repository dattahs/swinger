#!/usr/bin/env python3
"""Session GTT recommendations with configurable warmup and clean slate."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.engine.engine import PriceDataMatrix, run_daily_strategy_iteration
from src.engine.adaptive_lookback import resolve_new_high_lookback_sessions
from src.models import ActionType, MarketContext
from src.repository.sqlite import SqliteDataLake
from scripts.fast_preview import _resolve_registry, _warm_end


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="2026-06-22")
    parser.add_argument("--warmup-from", default="2025-06-23")
    parser.add_argument("--capital", type=float, default=500_000.0)
    parser.add_argument("--force-warmup", action="store_true")
    args = parser.parse_args()

    session = date.fromisoformat(args.session)
    warmup_from = date.fromisoformat(args.warmup_from)
    cfg = load_config_relaxed(ROOT / "config.yaml")

    dl = SqliteDataLake(ROOT / cfg.backtest.data_db_path)
    pricing = dl.get_latest_trading_day(on_or_before=session)
    if pricing is None:
        print("No price data")
        return 2

    trading_days = dl.get_trading_days(warmup_from, pricing)
    warm_end = _warm_end(trading_days, session, pricing)

    t0 = time.perf_counter()
    registry, _blocked = _resolve_registry(
        cfg, ROOT, dl, warmup_from, warm_end, force_warmup=True
    )
    warm_sec = time.perf_counter() - t0

    universe = dl.get_universe(pricing)
    days = cfg.darvas_box.required_price_history_days + 50
    symbols = dl.filter_symbols_with_bar_on(universe, pricing)
    bars = {s: dl.get_daily_bars(s, pricing, days) for s in symbols}
    index_bars = dl.get_daily_bars(cfg.darvas_box.market_trend_filter.index, pricing, 250)
    price_data = PriceDataMatrix(bars, index_bars)

    _, lb_meta = resolve_new_high_lookback_sessions(
        dl.get_daily_bars(cfg.universe_filters.adaptive_new_high_lookback.regime_index, pricing, 1600),
        cfg,
        session,
    )
    lookback = lb_meta.get("lookback_sessions", 126)
    ctx = MarketContext(
        target_date=session,
        account_equity=args.capital,
        settled_cash_inr=args.capital,
    )
    actions, _, rows = run_daily_strategy_iteration(
        ctx,
        price_data,
        dl,
        {k: v.model_copy(deep=True) for k, v in registry.items()},
        cfg,
        universe,
        pending_symbols=set(),
        breakout_reentry_blocked=set(),
    )
    strat_sec = time.perf_counter() - t0 - warm_sec

    buys = [a for a in actions if a.action_type == ActionType.PLACE_BUY_GTT]
    budget = args.capital * cfg.risk_management.gtt_capital_overcommit_factor

    print("=" * 72)
    print(f"RECOMMENDATIONS for session {session}  (pricing EOD {pricing})")
    print(f"Capital INR {args.capital:,.0f}  |  GTT budget INR {budget:,.0f}")
    print(f"New-high gate: {cfg.universe_filters.require_new_52wk_high}  |  "
          f"lookback {cfg.universe_filters.new_high_lookback_weeks} weeks ({lookback} sessions)")
    print(f"Warmup {warmup_from} -> {warm_end} ({warm_sec:.0f}s)  |  strategy {strat_sec:.1f}s")
    print(f"Assumptions: no pending GTTs, no re-entry blocks, no open positions")
    print("=" * 72)

    if not buys:
        print("\nNo BUY GTT recommendations.")
    else:
        total = 0.0
        print(f"\nPLACE_BUY_GTT ({len(buys)} orders):\n")
        print(f"{'#':>3}  {'Symbol':12}  {'Trigger':>10}  {'Stop':>10}  {'Target':>10}  {'Qty':>5}  {'Cost':>12}  {'RR':>6}")
        print("-" * 72)
        for i, a in enumerate(buys, 1):
            row = next((r for r in rows if r.symbol == a.symbol), None)
            rr = row.structural_rr if row else 0
            cost = a.quantity * (a.trigger_price - cfg.risk_management.gtt_trigger_buffer_inr)
            total += cost
            print(
                f"{i:3}  {a.symbol:12}  {a.trigger_price:10.2f}  {a.stop_loss_price:10.2f}  "
                f"{a.target_price:10.2f}  {a.quantity:5}  {cost:12,.0f}  {rr:6.4f}"
            )
        print("-" * 72)
        print(f"Total committed (entry): INR {total:,.0f}")

    ranked = sorted(
        [r for r in rows if r.structural_rr and r.quantity and r.quantity >= 1],
        key=lambda r: (-(r.structural_rr or 0),),
    )
    selected_syms = {a.symbol for a in buys}
    skipped = [r for r in ranked if r.symbol not in selected_syms]
    if skipped:
        print(f"\nSized breakouts not selected ({len(skipped)}):")
        for r in skipped[:12]:
            reason = r.skip_reason or r.filter_fail_reason or "RANKED_OUT"
            print(f"  {r.symbol:12} RR={r.structural_rr:.4f} qty={r.quantity}  -> {reason}")
        if len(skipped) > 12:
            print(f"  ... +{len(skipped) - 12} more")

    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
