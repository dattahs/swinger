#!/usr/bin/env python3
"""Day-by-day POLYCAB box state replay."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config_relaxed
from src.engine.darvas import _compute_hybrid_box, compute_atr, update_box_state
from src.models import BoxState, BoxStateEnum
from src.repository.sqlite import SqliteDataLake

cfg = load_config_relaxed(ROOT / "config.yaml")
dl = SqliteDataLake(ROOT / "data/processed/swinger_data.db")
sym = "POLYCAB"
days = dl.get_trading_days(date(2025, 10, 1), date(2026, 6, 19))
hist = cfg.darvas_box.required_price_history_days + 50

state = BoxState(symbol=sym)
transitions: list[str] = []

for session in days:
    bars = dl.get_daily_bars(sym, session, hist)
    if bars.empty:
        continue
    prev = state.box_state
    state = update_box_state(
        state,
        bars,
        cfg,
        trend_ok=True,
        has_open_position=False,
        target_date=session,
        debug=None,
    )
    if state.box_state != prev or session >= date(2026, 6, 10):
        close = float(bars.iloc[-1]["close"])
        vol = int(bars.iloc[-1]["volume"])
        rev = state.reversal_high
        atr = compute_atr(bars, cfg.darvas_box.atr_period)
        bounds_note = ""
        if rev:
            b = _compute_hybrid_box(bars, rev, len(bars) - 1, cfg.darvas_box)
            if b:
                dt, db = b
                darvas_top = float(bars.iloc[-3:]["high"].max())
                atr_top = rev + cfg.darvas_box.atr_multiplier * atr
                bounds_note = (
                    f" hybrid->top={dt:.2f} (min darvas3d={darvas_top:.2f}, atr_cap={atr_top:.2f})"
                )
        if state.box_state != prev or session >= date(2026, 6, 15):
            transitions.append(
                f"{session}  {prev.value:10} -> {state.box_state.value:10}  "
                f"C={close:8.2f}  box=[{state.box_bottom or 0:.2f}, {state.box_top or 0:.2f}]  "
                f"rev_hi={rev or 0:.2f}  days={state.days_in_box}{bounds_note}"
            )

print("=== POLYCAB box transitions (Oct 2025 to 19 Jun 2026) ===\n")
for line in transitions:
    print(line)

# Key dates detail
print("\n=== WHY box_top ≈ 8168 on 19-Jun ===")
bars = dl.get_daily_bars(sym, date(2026, 6, 19), hist)
rev = 7619.5
atr = compute_atr(bars, 20)
dcfg = cfg.darvas_box
darvas_top = float(bars.iloc[-3:]["high"].max())
darvas_bottom = float(bars.iloc[-1]["low"])
atr_top = rev + dcfg.atr_multiplier * atr
atr_bottom = rev - dcfg.atr_multiplier * atr
print(f"reversal_high (frozen):     {rev}")
print(f"ATR(20):                    {atr:.2f}")
print(f"darvas 3-day high (top):    {darvas_top}")
print(f"darvas last-day low (bot):  {darvas_bottom}")
print(f"ATR band top (rev+2ATR):    {atr_top:.2f}")
print(f"ATR band bottom (rev-2ATR): {atr_bottom:.2f}")
print(f"box_top = min(darvas, atr): {min(darvas_top, atr_top):.2f}")
print(f"box_bottom = max(...):      {max(darvas_bottom, atr_bottom):.2f}")
print(f"close 19-Jun:               {float(bars.iloc[-1]['close']):.2f}")
print(f"GTT trigger (top+0.05):     {min(darvas_top, atr_top) + cfg.risk_management.gtt_trigger_buffer_inr:.2f}")
