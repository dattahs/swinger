"""Tests for monthly backtest analysis."""

from __future__ import annotations

from datetime import date

import pandas as pd

from scripts.monthly_analysis import build_monthly_table


def test_build_monthly_table():
    eq = pd.DataFrame(
        {
            "date": pd.to_datetime(
                ["2025-10-01", "2025-10-15", "2025-10-31", "2025-11-15", "2025-11-30"]
            ),
            "equity": [500_000, 510_000, 520_000, 515_000, 530_000],
            "drawdown_pct": [0.0, 0.5, 1.0, 0.8, 0.3],
            "open_positions_count": [0, 2, 3, 1, 2],
        }
    )
    closed = pd.DataFrame(
        {
            "entry_date": [date(2025, 10, 5), date(2025, 10, 10), date(2025, 10, 20)],
            "exit_date": [date(2025, 10, 15), date(2025, 10, 31), date(2025, 11, 10)],
            "pnl": [5000.0, -2000.0, 3000.0],
        }
    )
    table = build_monthly_table(eq, closed)
    oct_row = table.loc[pd.Period("2025-10", freq="M")]
    assert oct_row["trades_carried"] == 3
    assert oct_row["new_trades_taken"] == 3
    assert oct_row["trades_with_gain"] == 1
    assert oct_row["trades_with_loss"] == 1
    assert oct_row["gain_per_trade"] == 5000.0
    assert oct_row["loss_per_trade"] == -2000.0

    nov_row = table.loc[pd.Period("2025-11", freq="M")]
    assert nov_row["trades_with_gain"] == 1
    assert nov_row["trades_carried"] == 2
