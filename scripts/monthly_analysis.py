"""Monthly backtest breakdown — equity, positions, and trade P&L."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def _month_end_sessions(eq: pd.DataFrame) -> pd.Series:
    eq = eq.copy()
    eq["month"] = eq["date"].dt.to_period("M")
    return eq.groupby("month")["date"].max()


def build_monthly_table(
    eq: pd.DataFrame,
    closed: pd.DataFrame,
    open_buys: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build per-month metrics from equity curve, closed trades, and open positions."""
    eq = eq.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq["month"] = eq["date"].dt.to_period("M")

    month_ends = _month_end_sessions(eq)
    monthly_eq = eq.groupby("month").agg(
        equity=("equity", "last"),
        max_dd=("drawdown_pct", "max"),
    )

    carried = (
        eq[eq["date"].isin(month_ends.values)]
        .set_index("month")["open_positions_count"]
        .rename("trades_carried")
    )

    closed = closed.copy()
    if not closed.empty:
        closed["entry_date"] = pd.to_datetime(closed["entry_date"])
        closed["exit_date"] = pd.to_datetime(closed["exit_date"])
        closed["exit_month"] = closed["exit_date"].dt.to_period("M")

        exits = closed.groupby("exit_month").agg(
            trades_with_gain=("pnl", lambda s: int((s > 0).sum())),
            trades_with_loss=("pnl", lambda s: int((s < 0).sum())),
            gain_per_trade=("pnl", lambda s: float(s[s > 0].mean()) if (s > 0).any() else 0.0),
            loss_per_trade=("pnl", lambda s: float(s[s < 0].mean()) if (s < 0).any() else 0.0),
        )
    else:
        exits = pd.DataFrame()

    entry_frames: list[pd.DataFrame] = []
    if not closed.empty:
        entry_frames.append(closed[["entry_date"]])
    if open_buys is not None and not open_buys.empty:
        entry_frames.append(open_buys[["entry_date"]])
    if entry_frames:
        entries = pd.concat(entry_frames, ignore_index=True)
        entries["entry_date"] = pd.to_datetime(entries["entry_date"])
        new_trades = entries.groupby(entries["entry_date"].dt.to_period("M")).size().rename(
            "new_trades_taken"
        )
    else:
        new_trades = pd.Series(dtype=int, name="new_trades_taken")

    table = monthly_eq.join(carried, how="left")
    table = table.join(new_trades, how="left")
    if not exits.empty:
        table = table.join(exits, how="left")
    table = table.fillna(
        {
            "trades_carried": 0,
            "new_trades_taken": 0,
            "trades_with_gain": 0,
            "trades_with_loss": 0,
            "gain_per_trade": 0.0,
            "loss_per_trade": 0.0,
        }
    )
    for col in ("trades_carried", "new_trades_taken", "trades_with_gain", "trades_with_loss"):
        table[col] = table[col].astype(int)

    return table


def load_run_frames(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    run_dir = Path(run_dir)
    eq = pd.read_csv(run_dir / "equity_curve.csv", parse_dates=["date"])
    closed_path = run_dir / "closed_trades.csv"
    if not closed_path.exists():
        raise FileNotFoundError(
            f"{closed_path} not found — re-run backtest to export closed trade P&L."
        )
    closed = pd.read_csv(closed_path, parse_dates=["entry_date", "exit_date"])
    ledger_path = run_dir / "trade_ledger.csv"
    open_buys = None
    if ledger_path.exists():
        ledger = pd.read_csv(ledger_path)
        open_buys = ledger[(ledger["direction"] == "BUY") & (ledger["is_active"].astype(int) == 1)]
    return eq, closed, open_buys


def print_monthly_table(table: pd.DataFrame) -> None:
    print("\n--- Monthly breakdown ---")
    display = table.copy()
    display["equity"] = display["equity"].map(lambda x: f"{x:,.0f}")
    display["max_dd"] = display["max_dd"].map(lambda x: f"{x:.2f}%")
    display["gain_per_trade"] = display["gain_per_trade"].map(lambda x: f"{x:,.0f}")
    display["loss_per_trade"] = display["loss_per_trade"].map(lambda x: f"{x:,.0f}")
    print(display.to_string())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Monthly backtest trade breakdown")
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    eq, closed, open_buys = load_run_frames(args.run_dir)
    print_monthly_table(build_monthly_table(eq, closed, open_buys))
