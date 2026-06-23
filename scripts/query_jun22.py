import sqlite3
from pathlib import Path

db = sqlite3.connect(Path(__file__).resolve().parents[1] / "data/live/sessions/run_20260622.db")
db.row_factory = sqlite3.Row

print("=== PLACED 22-Jun ===")
for r in db.execute(
    """SELECT symbol, rank, box_top, box_bottom, trigger_price, quantity, structural_rr
       FROM decision_log WHERE date='2026-06-22' AND selected=1 ORDER BY rank"""
):
    print(dict(r))

print("\n=== FILTERED (POLYCAB, BAJAJ, BRITANNIA) ===")
for r in db.execute(
    """SELECT symbol, box_state, box_top, box_bottom, action_type, skip_reason
       FROM decision_log WHERE date='2026-06-22'
       AND symbol IN ('POLYCAB','BAJAJ-AUTO','BRITANNIA')"""
):
    print(dict(r))
