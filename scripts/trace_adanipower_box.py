#!/usr/bin/env python3
"""Replay Darvas state for ADANIPOWER — matches engine bar loading."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed, darvas_price_history_days
from src.engine.adaptive_lookback import resolve_new_high_lookback_sessions
from src.engine.darvas import _compute_hybrid_box, update_box_state
from src.engine.filters import symbol_trend_ok
from src.models import BoxState
from src.repository.sqlite import SqliteDataLake
from src.data.sector_etfs import SECTOR_ETF_SYMBOLS, SECTOR_INDEX_SYMBOLS

SYM = "ADANIPOWER"
cfg = load_config_relaxed(ROOT / "scripts/deploy/vps/config.yaml")
dl = SqliteDataLake(ROOT / cfg.backtest.data_db_path)
hist_days = darvas_price_history_days(cfg)

warm_from = date(2025, 10, 1)
end = date(2026, 6, 25)
trading_days = dl.get_trading_days(warm_from, end)
sector_label = dl.get_sector(SYM)

state = BoxState(symbol=SYM)

print(f"{'date':<12} {'state':<10} {'close':>8} {'low':>8} {'box_top':>8} {'box_bot':>8}  note")
print("-" * 85)

for d in trading_days:
    bars = dl.get_daily_bars(SYM, d, hist_days)
    if len(bars) < cfg.darvas_box.required_price_history_days:
        continue

    index_bars = dl.get_daily_bars(cfg.darvas_box.market_trend_filter.index, d, 250)
    sector_etf_bars = {s: dl.get_daily_bars(s, d, hist_days) for s in SECTOR_ETF_SYMBOLS}
    sector_index_bars = {s: dl.get_daily_bars(s, d, hist_days) for s in SECTOR_INDEX_SYMBOLS.values()}
    lookback, _ = resolve_new_high_lookback_sessions(index_bars, cfg, d)
    trend_ok = symbol_trend_ok(
        SYM, sector_label, index_bars, sector_etf_bars, sector_index_bars, cfg
    )

    prev = state.box_state
    state = update_box_state(
        state, bars, cfg, trend_ok, False, d, None, new_high_lookback_sessions=lookback
    )

    if d < date(2026, 5, 20) and state.box_state.value == "SCANNING" and prev.value == "SCANNING":
        continue

    last = bars.iloc[-1]
    close, low = float(last["close"]), float(last["low"])
    top, bot = state.box_top, state.box_bottom
    notes = []
    if top and bot:
        if close < bot:
            notes.append("CLOSE below box_bot")
        if low < bot:
            notes.append("LOW below box_bot")
    if prev != state.box_state:
        notes.append(f"{prev.value}->{state.box_state.value}")
    if d >= date(2026, 6, 8) and d <= date(2026, 6, 20):
        print(
            f"{d} {state.box_state.value:<10} {close:8.2f} {low:8.2f} "
            f"{(top or 0):8.2f} {(bot or 0):8.2f}  {', '.join(notes)}"
        )

print("\n--- Bounds BEFORE vs AFTER daily recompute on 2026-06-11 ---")
d = date(2026, 6, 11)
bars = dl.get_daily_bars(SYM, d, hist_days)
# replay to d-1 to get state entering the day
state2 = BoxState(symbol=SYM)
for td in trading_days:
    if td > d:
        break
    b = dl.get_daily_bars(SYM, td, hist_days)
    ib = dl.get_daily_bars(cfg.darvas_box.market_trend_filter.index, td, 250)
    se = {s: dl.get_daily_bars(s, td, hist_days) for s in SECTOR_ETF_SYMBOLS}
    si = {s: dl.get_daily_bars(s, td, hist_days) for s in SECTOR_INDEX_SYMBOLS.values()}
    lb, _ = resolve_new_high_lookback_sessions(ib, cfg, td)
    tr = symbol_trend_ok(SYM, sector_label, ib, se, si, cfg)
    state2 = update_box_state(state2, b, cfg, tr, False, td, None, new_high_lookback_sessions=lb)

old_top, old_bot = state2.box_top, state2.box_bottom
close = float(bars.iloc[-1]["close"])
rev_idx = len(bars) - 1
new_bounds = _compute_hybrid_box(bars, state2.reversal_high, rev_idx, cfg.darvas_box)
print(f"Entering {d}: state={state2.box_state.value} box=[{old_bot}, {old_top}] reversal_high={state2.reversal_high}")
print(f"Close={close}")
print(f"After _compute_hybrid_box: {new_bounds}")
if new_bounds:
    nt, nb = new_bounds
    print(f"Would fail breakout? close < new_bot: {close < nb}")
