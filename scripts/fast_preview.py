#!/usr/bin/env python3
"""Fast live-day preview — warmup cache + one strategy pass (no broker)."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.engine.engine import PriceDataMatrix, run_daily_strategy_iteration
from src.live.warmup_cache import (
    darvas_warmup_fingerprint,
    save_warmup_cache,
    try_load_warmup_cache,
    warmup_cache_path,
)
from src.models import ActionType, MarketContext
from src.repository.sqlite import SqliteDataLake

logger = logging.getLogger(__name__)


def _warm_end(trading_days: list[date], session_date: date, pricing_date: date) -> date:
    if session_date > pricing_date:
        return pricing_date
    if trading_days[-1] == pricing_date and len(trading_days) > 1:
        return trading_days[-2]
    return trading_days[-1]


def _resolve_registry(
    cfg,
    repo_root: Path,
    data_lake: SqliteDataLake,
    warmup_from: date,
    warm_end: date,
    *,
    force_warmup: bool,
) -> tuple[dict, set[str]]:
    cache_dir = cfg.live.warmup_cache_dir
    if not force_warmup:
        cached = try_load_warmup_cache(repo_root, cfg, cache_dir, warmup_from, warm_end)
        if cached:
            return cached

    from src.backtest.backtester import Backtester

    logger.info("Running full warmup %s -> %s", warmup_from, warm_end)
    bt_cfg = cfg.model_copy(deep=True)
    bt_cfg.backtest.progress_log.enabled = False
    bt_cfg.backtest.debug_log.enabled = False
    bt = Backtester(bt_cfg, repo_root=repo_root)
    bt.run(start=warmup_from, end=warm_end)
    registry = bt.repo.get_state_registry()
    blocked = set(bt._breakout_reentry_blocked)
    fp = darvas_warmup_fingerprint(cfg)
    path = warmup_cache_path(repo_root, cache_dir, warmup_from, warm_end, fp)
    save_warmup_cache(
        path,
        registry,
        blocked,
        warmup_from=warmup_from,
        warm_end=warm_end,
        fingerprint=fp,
    )
    return registry, blocked


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preview GTT picks for one session using cached Darvas warmup"
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--session", required=True, help="Session date YYYY-MM-DD")
    parser.add_argument("--warmup-from", default=None, help="Override live.warmup_from")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--force-warmup", action="store_true", help="Rebuild warmup cache")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    session = date.fromisoformat(args.session)
    cfg = load_config_relaxed(ROOT / args.config)
    warmup_from = (
        date.fromisoformat(args.warmup_from)
        if args.warmup_from
        else cfg.live.warmup_from
    )

    data_path = ROOT / cfg.backtest.data_db_path
    data_lake = SqliteDataLake(data_path)
    pricing_date = data_lake.get_latest_trading_day(on_or_before=session)
    if pricing_date is None:
        print("No price data in lake — run bhavcopy ingest first")
        return 2
    if session > pricing_date:
        print(f"Session {session} ahead of data ({pricing_date}) — using {pricing_date} EOD bars")

    trading_days = data_lake.get_trading_days(warmup_from, pricing_date)
    if not trading_days:
        print("No trading days in warmup window")
        return 2
    warm_end = _warm_end(trading_days, session, pricing_date)

    t0 = time.perf_counter()
    registry, blocked = _resolve_registry(
        cfg,
        ROOT,
        data_lake,
        warmup_from,
        warm_end,
        force_warmup=args.force_warmup,
    )
    warm_sec = time.perf_counter() - t0

    universe = data_lake.get_universe(pricing_date)
    days = cfg.darvas_box.required_price_history_days + 50
    symbols = data_lake.filter_symbols_with_bar_on(universe, pricing_date)
    bars = {s: data_lake.get_daily_bars(s, pricing_date, days) for s in symbols}
    index_bars = data_lake.get_daily_bars(
        cfg.darvas_box.market_trend_filter.index,
        pricing_date,
        250,
    )
    price_data = PriceDataMatrix(bars, index_bars)

    ctx = MarketContext(
        target_date=session,
        account_equity=args.capital,
        settled_cash_inr=args.capital,
    )
    t1 = time.perf_counter()
    actions, _, decision_rows = run_daily_strategy_iteration(
        ctx,
        price_data,
        data_lake,
        registry,
        cfg,
        universe,
        pending_symbols=set(),
        breakout_reentry_blocked=blocked,
    )
    strat_sec = time.perf_counter() - t1

    buys = [a for a in actions if a.action_type == ActionType.PLACE_BUY_GTT]
    breakouts = [r for r in decision_rows if r.box_state == "BREAKOUT"]
    rejected = [
        r
        for r in breakouts
        if not r.filter_pass or r.skip_reason or r.filter_fail_reason
    ]

    print("=" * 70)
    print(f"FAST PREVIEW: {session.isoformat()}  (pricing {pricing_date})")
    print(f"Capital INR {args.capital:,.0f}  |  warmup {warm_sec:.1f}s  |  strategy {strat_sec:.1f}s")
    print("=" * 70)

    print(f"\nBUY GTT candidates selected ({len(buys)}):")
    if not buys:
        print("  (none)")
    else:
        for a in buys:
            print(
                f"  {a.symbol}: trigger {a.trigger_price:.2f} "
                f"stop {a.stop_loss_price:.2f} target {a.target_price:.2f} qty {a.quantity}"
            )

    safety = [r for r in rejected if r.filter_fail_reason in (
        "PRICE_BELOW_SMA",
        "DISTRIBUTION_ANTIPATTERN",
        "ENTRY_SMA_HISTORY",
    )]
    if safety:
        print(f"\nEntry safety rejections ({len(safety)}):")
        for r in sorted(safety, key=lambda x: x.symbol)[:15]:
            print(f"  {r.symbol}: {r.filter_fail_reason}")
        if len(safety) > 15:
            print(f"  ... and {len(safety) - 15} more")

    other = [r for r in rejected if r not in safety]
    if other:
        from collections import Counter

        counts = Counter(r.skip_reason or r.filter_fail_reason or "?" for r in other)
        print("\nOther breakout skips:")
        for reason, n in counts.most_common(8):
            print(f"  {reason}: {n}")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
