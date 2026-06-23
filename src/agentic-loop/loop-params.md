Stale breakout fix (darvas_box)
breakout_reset_above_top_pct (2.0)
If close rises more than this % above box_top while still in BREAKOUT, box resets to SCANNING.
Stops stale boxes where price has run far above a frozen top (e.g. POLYCAB).

new_high_lookback_weeks (26)
Fixed lookback in weeks when adaptive mode is off; converted to ~126 trading sessions.
Higher = stricter (must beat a longer prior peak); lower = more setups qualify.

adaptive_new_high_lookback.enabled (true)
Switches from fixed lookback to regime-based lookback using index vs SMA.
When true, new_high_lookback_weeks is ignored for the gate.

adaptive_new_high_lookback.min_lookback_weeks (9, ~2 months)
Shortest new-high window — applied when index is “much above” SMA (bullish).
More breakouts allowed in strong markets.

adaptive_new_high_lookback.max_lookback_weeks (39, ~9 months)
Longest new-high window — applied when index is “much below” SMA (bearish).
Fewer, higher-quality setups in weak markets.

Stale breakout fix (darvas_box)
breakout_reset_above_top_pct (2.0)
If close rises more than this % above box_top while still in BREAKOUT, box resets to SCANNING.
Stops stale boxes where price has run far above a frozen top (e.g. POLYCAB).

