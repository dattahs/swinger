"""Performance metrics for backtest validation — Profit Factor primary."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


def profit_factor(trades: pd.DataFrame) -> float | None:
    if trades.empty:
        return None
    gross_profit = float(trades.loc[trades["pnl"] > 0, "pnl"].sum())
    gross_loss = abs(float(trades.loc[trades["pnl"] <= 0, "pnl"].sum()))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else None
    return gross_profit / gross_loss


def payoff_ratio(trades: pd.DataFrame) -> float | None:
    if trades.empty:
        return None
    wins = trades.loc[trades["pnl"] > 0, "pnl"]
    losses = trades.loc[trades["pnl"] <= 0, "pnl"]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def win_rate(trades: pd.DataFrame) -> float | None:
    if trades.empty:
        return None
    return float((trades["pnl"] > 0).mean())


def max_drawdown_pct(equity: pd.DataFrame) -> float:
    if equity.empty or "equity" not in equity.columns:
        return 0.0
    eq = equity["equity"].astype(float)
    peak = eq.cummax()
    dd = 100.0 * (peak - eq) / peak.replace(0, float("nan"))
    return float(dd.max()) if not dd.empty else 0.0


def return_pct(equity: pd.DataFrame, initial_capital: float) -> float | None:
    if equity.empty:
        return None
    start = initial_capital
    end = float(equity.iloc[-1]["equity"])
    if start <= 0:
        return None
    return 100.0 * (end - start) / start


def recovery_days(equity: pd.DataFrame, pre_crash_high: float | None = None) -> int | None:
    """Trading days from trough to recovery above pre-crash equity high."""
    if equity.empty:
        return None
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"])
    eq = eq.sort_values("date")
    values = eq["equity"].astype(float)
    peak_before = float(pre_crash_high) if pre_crash_high is not None else float(values.iloc[0])
    trough_idx = int(values.idxmin())
    trough_val = float(values.loc[trough_idx])
    if trough_val >= peak_before:
        return 0
    after = eq.loc[eq.index >= trough_idx]
    for i, row in after.iterrows():
        if float(row["equity"]) >= peak_before:
            return int((after["date"] <= row["date"]).sum() - 1)
    return None


def compute_trade_metrics(
    closed_trades: list[dict] | pd.DataFrame,
    equity_curve: list[dict] | pd.DataFrame,
    *,
    initial_capital: float,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    trades = (
        pd.DataFrame(closed_trades)
        if not isinstance(closed_trades, pd.DataFrame)
        else closed_trades.copy()
    )
    equity = (
        pd.DataFrame(equity_curve)
        if not isinstance(equity_curve, pd.DataFrame)
        else equity_curve.copy()
    )

    if not trades.empty:
        trades["entry_date"] = pd.to_datetime(trades["entry_date"])
        trades["exit_date"] = pd.to_datetime(trades["exit_date"])
        if start_date is not None:
            trades = trades[trades["entry_date"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            trades = trades[trades["entry_date"] <= pd.Timestamp(end_date)]

    if not equity.empty:
        equity["date"] = pd.to_datetime(equity["date"])
        if start_date is not None:
            equity = equity[equity["date"] >= pd.Timestamp(start_date)]
        if end_date is not None:
            equity = equity[equity["date"] <= pd.Timestamp(end_date)]

    pf = profit_factor(trades)
    return {
        "profit_factor": round(pf, 3) if pf is not None and pf != float("inf") else pf,
        "max_drawdown_pct": round(max_drawdown_pct(equity), 2),
        "win_rate_pct": round(100 * win_rate(trades), 1) if win_rate(trades) is not None else None,
        "payoff_ratio": round(payoff_ratio(trades), 2) if payoff_ratio(trades) is not None else None,
        "num_trades": len(trades),
        "return_pct": round(return_pct(equity, initial_capital), 2)
        if return_pct(equity, initial_capital) is not None
        else None,
        "gross_profit_inr": round(float(trades.loc[trades["pnl"] > 0, "pnl"].sum()), 2)
        if not trades.empty
        else 0.0,
        "gross_loss_inr": round(float(trades.loc[trades["pnl"] <= 0, "pnl"].sum()), 2)
        if not trades.empty
        else 0.0,
    }


def slice_equity_by_regime(
    equity: pd.DataFrame,
    regime_by_date: dict[date, str],
    regime: str,
) -> pd.DataFrame:
    if equity.empty:
        return equity
    eq = equity.copy()
    eq["date"] = pd.to_datetime(eq["date"]).dt.date
    mask = eq["date"].map(lambda d: regime_by_date.get(d) == regime)
    return eq.loc[mask].reset_index(drop=True)


def slice_trades_by_entry_regime(
    trades: pd.DataFrame,
    regime_by_date: dict[date, str],
    regime: str,
) -> pd.DataFrame:
    if trades.empty:
        return trades
    t = trades.copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"]).dt.date
    mask = t["entry_date"].map(lambda d: regime_by_date.get(d) == regime)
    return t.loc[mask].reset_index(drop=True)
