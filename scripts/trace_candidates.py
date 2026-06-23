#!/usr/bin/env python3
"""Full gate-by-gate trace for breakout candidates on a session date."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.debug_log import humanize_filter_reason, humanize_skip_reason
from src.engine.darvas import volume_sma
from src.engine.engine import PriceDataMatrix, run_daily_strategy_iteration
from src.engine.entry_safety import _close_sma, check_entry_safety
from src.engine.filters import (
    check_fundamental_filters,
    check_universe_filters,
    index_trend_ok,
)
from src.engine.risk import compute_entry_prices, compute_structural_rr, size_position
from src.live.warmup_cache import try_load_warmup_cache
from src.models import ActionType, BoxStateEnum, MarketContext
from src.repository.sqlite import SqliteDataLake

from scripts.fast_preview import _warm_end


def _d(raw) -> date:
    if isinstance(raw, date):
        return raw
    return date.fromisoformat(str(raw)[:10])


def _post_breakout_red_days(bars, breakout_date: date | None, period: int) -> list[dict]:
    if breakout_date is None:
        return []
    out = []
    for i in range(len(bars)):
        row = bars.iloc[i]
        session = _d(row["date"])
        if session <= breakout_date:
            continue
        vol_sma = volume_sma(bars.iloc[: i + 1], period)
        red = float(row["close"]) < float(row["open"])
        high_vol = int(row["volume"]) > vol_sma
        out.append(
            {
                "date": session,
                "open": float(row["open"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "vol_sma": vol_sma,
                "red": red,
                "high_vol": high_vol,
            }
        )
    return out


def trace_symbol(
    sym: str,
    *,
    session: date,
    pricing: date,
    cfg,
    dl: SqliteDataLake,
    registry: dict,
    blocked: set[str],
    price_data: PriceDataMatrix,
    dec,
    selected: set[str],
) -> None:
    print("=" * 80)
    print(f"  {sym}")
    print("=" * 80)

    state = registry.get(sym)
    bars = price_data.get(sym)
    period = cfg.risk_management.entry_sma_period

    print("\n[STATE REGISTRY] (warmup through pricing EOD)")
    if state:
        print(f"  box_state      : {state.box_state.value}")
        print(f"  box_top/bottom : {state.box_top} / {state.box_bottom}")
        print(f"  box_start/end  : {state.box_start_date} / {state.box_end_date}")
        print(f"  breakout_date  : {state.breakout_date}")
        print(f"  reversal_high  : {state.reversal_high}")
        print(f"  last_close     : {state.last_close}")
        print(f"  vol_sma_20     : {state.volume_sma_20}")
        print(f"  reentry_blocked: {sym in blocked}")
    else:
        print("  (not in registry)")

    print(f"\n[BARS] last 12 sessions through pricing {pricing}")
    if bars.empty:
        print("  (no bars)")
    else:
        for _, r in bars.tail(12).iterrows():
            d = _d(r["date"])
            red = "RED" if r["close"] < r["open"] else "grn"
            marker = " <-- pricing" if d == pricing else ""
            print(
                f"  {d}  O={r['open']:8.2f} H={r['high']:8.2f} "
                f"L={r['low']:8.2f} C={r['close']:8.2f}  V={int(r['volume']):>10,}  {red}{marker}"
            )
        if len(bars) >= period:
            sma = _close_sma(bars, period)
            close = float(bars.iloc[-1]["close"])
            print(f"\n  close {close:.2f} vs SMA({period}) {sma:.2f}  -> {'PASS' if close > sma else 'FAIL'}")

    print("\n[GATE 1 — UNIVERSE FILTERS]")
    u_pass, u_reason = check_universe_filters(sym, bars, session, dl, cfg)
    if not bars.empty:
        last = bars.iloc[-1]
        uf = cfg.universe_filters
        print(f"  close INR {last['close']:.2f}  (min INR {uf.min_stock_price_inr})")
        print(f"  volume {int(last['volume']):,}  (min {uf.min_daily_volume_shares:,})")
        turnover = (last.get("turnover_inr") or 0) / 1e7
        print(f"  turnover {turnover:.2f} Cr  (min {uf.min_daily_turnover_inr_cr} Cr)")
        print(f"  ASM/GSM: {dl.is_asm_gsm(sym, session)}")
    print(f"  -> {'PASS' if u_pass else 'FAIL: ' + str(u_reason)}")

    print("\n[GATE 2 — FUNDAMENTAL FILTERS]")
    f_pass, f_reason = (False, u_reason) if not u_pass else check_fundamental_filters(sym, session, dl, cfg)
    if u_pass:
        metrics = dl.get_fundamentals_pit(sym, session)
        ff = cfg.fundamental_filters
        if metrics:
            for key, thr, label in [
                ("revenue_growth_pct", ff.min_revenue_growth_pct, "rev_growth"),
                ("eps_growth_pct", ff.min_eps_growth_pct, "eps_growth"),
                ("roe_pct", ff.min_roe_pct, "roe"),
                ("roce_pct", ff.min_roce_pct, "roce"),
                ("promoter_holding_pct", ff.min_promoter_holding_pct, "promoter"),
            ]:
                val = metrics.get(key)
                ok = val is not None and val >= thr
                print(f"  {label:12} {val if val is not None else '—':>8}  (min {thr})  {'PASS' if ok else 'FAIL'}")
            de = metrics.get("debt_to_equity")
            print(f"  D/E          {de if de is not None else '—':>8}  (max {ff.max_debt_to_equity})")
            print(f"  earnings blackout: {dl.has_upcoming_earnings(sym, session, ff.avoid_days_before_earnings)}")
        else:
            print("  no PIT fundamentals")
        print(f"  -> {'PASS' if f_pass else 'FAIL: ' + str(f_reason)}")
    else:
        print("  skipped (universe failed)")

    in_breakout = state and state.box_state == BoxStateEnum.BREAKOUT
    print("\n[GATE 3 — BOX STATE]")
    print(f"  state == BREAKOUT : {in_breakout}")

    print("\n[GATE 4 — ENTRY SAFETY]")
    safe = True
    safety = None
    if in_breakout and f_pass and u_pass:
        safe, safety = check_entry_safety(bars, cfg, breakout_date=state.breakout_date)
        for rd in _post_breakout_red_days(bars, state.breakout_date, period):
            streak_note = ""
            if rd["red"] and rd["high_vol"]:
                streak_note = "  <- red + high volume"
            print(
                f"    {rd['date']}  O={rd['open']:.2f} C={rd['close']:.2f} "
                f"V={rd['volume']:,} vol_sma={rd['vol_sma']:,.0f}  "
                f"{'RED' if rd['red'] else 'grn'}{' +HV' if rd['high_vol'] else ''}{streak_note}"
            )
        print(f"  -> {'PASS' if safe else 'FAIL: ' + safety}")
        if safety:
            print(f"     ({humanize_filter_reason(safety)})")
    else:
        print("  skipped")

    print("\n[GATE 5 — BREAKOUT VALIDITY + SIZING]")
    validity_ok = False
    if in_breakout and state and state.box_top and state.box_bottom and not bars.empty:
        close = float(bars.iloc[-1]["close"])
        bot, top = state.box_bottom, state.box_top
        stale_pct = cfg.darvas_box.breakout_reset_above_top_pct
        stale_thresh = top * (1 + stale_pct / 100)
        print(f"  close {close:.2f}  box [{bot:.2f}, {top:.2f}]")
        failed = close < bot
        stale = close > stale_thresh
        reentry = sym in blocked and cfg.risk_management.require_box_reset_for_reentry
        print(f"  below box bottom     : {failed}")
        print(f"  stale (>top+{stale_pct}%): {stale}  (threshold {stale_thresh:.2f})")
        print(f"  re-entry blocked set : {reentry}")
        entry, trig, stop, target = compute_entry_prices(top, bot, cfg)
        rr = compute_structural_rr(entry, stop, target)
        sized = size_position(entry, stop, 100_000, 100_000, cfg)
        print(f"  entry/trigger/stop/target: {entry:.2f} / {trig:.2f} / {stop:.2f} / {target:.2f}")
        print(f"  structural RR: {rr:.4f}  (min {cfg.risk_management.min_structural_r_ratio})")
        print(f"  position size @ INR 1L: {sized} shares  (cost INR {(sized or 0)*entry:,.0f})")
        vol = int(bars.iloc[-1]["volume"])
        vol_sma = state.volume_sma_20 or volume_sma(bars, 20)
        print(f"  session vol / vol_sma: {vol:,} / {vol_sma:,.0f} = {vol/vol_sma:.2f}x")
        validity_ok = not failed and not stale and not reentry and rr >= cfg.risk_management.min_structural_r_ratio

    print("\n[GATE 6 — RANKING / SELECTION]")
    if dec:
        print(f"  rank               : {dec.rank}")
        print(f"  filter_pass        : {dec.filter_pass}")
        print(f"  filter_fail_reason : {dec.filter_fail_reason}")
        print(f"  skip_reason        : {dec.skip_reason}")
        print(f"  structural_rr      : {dec.structural_rr}")
        print(f"  trigger/stop/target: {dec.trigger_price} / {dec.stop_loss_price} / {dec.target_price}")
        print(f"  quantity           : {dec.quantity}")
        print(f"  selected           : {dec.selected}")
        print(f"  action             : {dec.action_type}")
    if sym in selected:
        print("  -> SELECTED - PLACE_BUY_GTT")
    elif dec and dec.skip_reason:
        print(f"  -> REJECTED: {dec.skip_reason} ({humanize_skip_reason(dec.skip_reason)})")
    elif dec and not dec.filter_pass:
        print(f"  -> REJECTED at filters: {dec.filter_fail_reason}")
    elif dec and dec.structural_rr and dec.quantity and dec.quantity >= 1:
        print("  -> REJECTED: RANKED_OUT (lower priority vs selected names)")

    filter_pass = u_pass and f_pass and safe
    print("\n[PIPELINE MAP]")
    stages = [
        ("Universe", u_pass),
        ("Fundamentals", f_pass),
        ("BREAKOUT state", bool(in_breakout)),
        ("Entry safety", safe if in_breakout else None),
        ("Breakout validity", validity_ok if in_breakout and filter_pass else None),
        ("In candidate pool", bool(dec and dec.structural_rr and dec.quantity and dec.quantity >= 1)),
        ("Selected GTT", sym in selected),
    ]
    for name, ok in stages:
        if ok is None:
            print(f"  {name:20} [----] n/a")
        else:
            print(f"  {name:20} [{'████' if ok else '....'}] {'PASS' if ok else 'FAIL'}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default="2026-06-22")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["VTL", "SPLPETRO", "ENGINERSIN", "PARADEEP", "ACE", "VBL"],
    )
    parser.add_argument("--capital", type=float, default=100_000)
    args = parser.parse_args()

    session = date.fromisoformat(args.session)
    syms = args.symbols
    cfg = load_config_relaxed(ROOT / "config.yaml")
    dl = SqliteDataLake(ROOT / cfg.backtest.data_db_path)
    pricing = dl.get_latest_trading_day(on_or_before=session) or session
    warmup_from = cfg.live.warmup_from
    trading_days = dl.get_trading_days(warmup_from, pricing)
    warm_end = _warm_end(trading_days, session, pricing)

    cached = try_load_warmup_cache(ROOT, cfg, cfg.live.warmup_cache_dir, warmup_from, warm_end)
    if not cached:
        print("Warmup cache missing — run: python scripts/fast_preview.py --session ... --force-warmup")
        return 2
    registry, blocked = cached

    universe = dl.get_universe(pricing)
    days = cfg.darvas_box.required_price_history_days + 50
    symbols = dl.filter_symbols_with_bar_on(universe, pricing)
    bars_map = {s: dl.get_daily_bars(s, pricing, days) for s in symbols}
    index_bars = dl.get_daily_bars(cfg.darvas_box.market_trend_filter.index, pricing, 250)
    price_data = PriceDataMatrix(bars_map, index_bars)

    nifty_ok = index_trend_ok(index_bars, cfg)
    print("=" * 80)
    print(f"SESSION {session}  |  pricing EOD {pricing}  |  capital INR {args.capital:,.0f}")
    print(f"Warmup {warmup_from} -> {warm_end}  |  NIFTY trend: {'PASS' if nifty_ok else 'FAIL'}")
    print(f"GTT budget = cash x {cfg.risk_management.gtt_capital_overcommit_factor}")
    print("=" * 80)

    ctx = MarketContext(
        target_date=session,
        account_equity=args.capital,
        settled_cash_inr=args.capital,
    )
    reg_copy = {k: v.model_copy(deep=True) for k, v in registry.items()}
    actions, _, decision_rows = run_daily_strategy_iteration(
        ctx,
        price_data,
        dl,
        reg_copy,
        cfg,
        universe,
        pending_symbols=set(),
        breakout_reentry_blocked=set(blocked),
    )
    selected = {a.symbol for a in actions if a.action_type == ActionType.PLACE_BUY_GTT}
    decision_by_sym = {r.symbol: r for r in decision_rows}

    ranked = sorted(
        [r for r in decision_rows if r.structural_rr is not None and r.quantity and r.quantity >= 1],
        key=lambda r: (-(r.structural_rr or 0),),
    )
    print("\n[ALL SIZED BREAKOUT CANDIDATES - rank by structural RR]")
    for i, r in enumerate(ranked, 1):
        tag = "SELECTED" if r.symbol in selected else (r.skip_reason or r.filter_fail_reason or "?")
        print(
            f"  #{i:2} {r.symbol:12} RR={r.structural_rr:.4f} qty={r.quantity}  "
            f"trigger={r.trigger_price:.2f}  -> {tag}"
        )
    print()

    for sym in syms:
        trace_symbol(
            sym,
            session=session,
            pricing=pricing,
            cfg=cfg,
            dl=dl,
            registry=registry,
            blocked=blocked,
            price_data=price_data,
            dec=decision_by_sym.get(sym),
            selected=selected,
        )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
