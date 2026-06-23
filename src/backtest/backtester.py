"""Day-loop backtest runner — REQUIREMENTS v1.2 Section 8."""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.backtest.data_cache import BacktestDataCache
from src.backtest.virtual_broker import VirtualBroker
from src.config import AppConfig
from src.debug_log import ActionDebugLogger, ProgressLogger
from src.data.sector_etfs import SECTOR_INDEX_SYMBOLS
from src.engine.adaptive_lookback import reset_lookback_cadence_state
from src.engine.engine import PriceDataMatrix, run_daily_strategy_iteration
from src.models import ActionType, BoxStateEnum, MarketContext
from src.repository.sqlite import SqliteBacktestRepository, SqliteDataLake


def evaluate_kill_switch(
    equity_today: float,
    equity_yesterday: float | None,
    cfg: AppConfig,
    kill_state: dict,
) -> dict:
    if equity_yesterday is None:
        return kill_state
    daily_loss = max(0.0, equity_yesterday - equity_today)
    limit = cfg.risk_management.kill_switch_daily_loss_limit_inr
    if daily_loss >= limit:
        return {
            "active": True,
            "tripped_on_date": kill_state.get("tripped_on_date"),
            "daily_loss_inr_at_trip": daily_loss,
        }
    return kill_state


def _resolve_path(base: Path, path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else base / path


def make_run_output_dir(config: AppConfig, repo_root: Path) -> Path:
    """Create a per-invocation output folder under export_directory."""
    base = _resolve_path(repo_root, config.backtest.export_directory)
    if config.backtest.timestamped_runs:
        stamp = datetime.now().strftime("run_%Y%m%d_%H%M%S")
        out_dir = base / stamp
    else:
        out_dir = base
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


class Backtester:
    def __init__(self, config: AppConfig, *, repo_root: Path | None = None) -> None:
        self.config = config
        self.repo_root = repo_root or Path.cwd()
        db_path = _resolve_path(self.repo_root, config.backtest.data_db_path)
        self.data_lake = SqliteDataLake(db_path)
        self.repo = SqliteBacktestRepository()
        self.broker = VirtualBroker(config.backtest.simulation_slippage_pct)
        self.broker.set_initial_cash(config.backtest.initial_capital_inr)
        self.equity_curve: list[dict] = []
        self._prev_equity: float | None = None
        self._run_start: date | None = None
        self._run_end: date | None = None
        self._run_dir: Path | None = None
        self._invoked_at: datetime | None = None
        self._progress = ProgressLogger(config.backtest.progress_log)
        self._debug = ActionDebugLogger(config.backtest.debug_log)
        self._prev_box_states: dict[str, str] = {}
        self._breakout_reentry_blocked: set[str] = set()
        self._cache: BacktestDataCache | None = None

    def _load_bars_for_universe(self, universe: list[str], end: date) -> dict[str, pd.DataFrame]:
        days = self.config.darvas_box.required_price_history_days + 50
        symbols = sorted(set(universe) | set(SECTOR_INDEX_SYMBOLS.values()))
        cache = self._cache
        if cache is None:
            symbols = self.data_lake.filter_symbols_with_bar_on(symbols, end)
            return {s: self.data_lake.get_daily_bars(s, end, days) for s in symbols}
        symbols = cache.filter_symbols_with_bar_on(symbols, end)
        return {s: cache.slice_bars(s, end, days) for s in symbols}

    def _index_bars(self, end: date) -> pd.DataFrame:
        idx = self.config.darvas_box.market_trend_filter.index
        days = 250
        if self._cache is not None:
            return self._cache.slice_bars(idx, end, days)
        return self.data_lake.get_daily_bars(idx, end, days)

    def run(self, start: date | None = None, end: date | None = None) -> Path:
        start = start or self.config.backtest.start_date
        end = end or self.config.backtest.end_date
        self._run_start = start
        self._run_end = end
        trading_days = self.data_lake.get_trading_days(start, end)
        if not trading_days:
            raise RuntimeError(
                f"No trading days between {start} and {end}. Run data ingest first."
            )

        out_dir = make_run_output_dir(self.config, self.repo_root)
        self._run_dir = out_dir
        self._invoked_at = datetime.now()
        progress_path = out_dir / Path(self.config.backtest.progress_log.log_file).name
        debug_path = out_dir / Path(self.config.backtest.debug_log.log_file).name

        manifest = {
            "invoked_at": self._invoked_at.isoformat(timespec="seconds"),
            "run_directory": str(out_dir),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "trading_days": len(trading_days),
            "initial_capital_inr": self.config.backtest.initial_capital_inr,
            "data_db_path": str(_resolve_path(self.repo_root, self.config.backtest.data_db_path)),
            "debug_log_enabled": self.config.backtest.debug_log.enabled,
            "progress_log_enabled": self.config.backtest.progress_log.enabled,
        }
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        self._progress.open(progress_path)
        self._debug.open(debug_path)
        self._progress.info(f"Output directory: {out_dir}")
        self._progress.info(
            f"Backtest starting: {start} to {end} ({len(trading_days)} trading days)"
        )
        if self._debug.enabled:
            self._progress.info(f"Action debug log: {debug_path}")

        reset_lookback_cadence_state()
        run_start = time.monotonic()
        self._progress.info("Warming in-memory data cache...")
        warm_t0 = time.monotonic()
        self._cache = BacktestDataCache(self.data_lake, self.config)
        self._cache.warm(start, end)
        self._progress.info(
            f"Cache ready in {time.monotonic() - warm_t0:.1f}s "
            f"({len(self._cache._bars_by_symbol)} symbols)"
        )

        kill_state = self.repo.get_system_state("kill_switch") or {"active": False}
        state_registry = self.repo.get_state_registry()
        total_days = len(trading_days)

        try:
            for day_num, session in enumerate(trading_days, start=1):
                self.broker.settle_cash(session)
                days_to_date = [d for d in trading_days if d <= session]
                expiry_cancels = self.broker.expire_stale_pending_buys(
                    session,
                    self.config.risk_management.gtt_expiry_sessions,
                    days_to_date,
                )
                if expiry_cancels:
                    self.broker.apply_actions(session, expiry_cancels)
                    if self._debug.enabled:
                        for cancel in expiry_cancels:
                            self._debug.broker(
                                session,
                                cancel.symbol,
                                "GTT_EXPIRED",
                                f"Cancelled stale buy GTT for {cancel.symbol} "
                                f"(>{self.config.risk_management.gtt_expiry_sessions} sessions)",
                                symbol=cancel.symbol,
                            )
                universe = self._cache.get_scan_universe(session)
                if not universe:
                    self._progress.session(
                        session,
                        day_num,
                        total_days,
                        self.broker.portfolio.settled_cash,
                        0,
                        universe_size=0,
                        elapsed_sec=time.monotonic() - run_start,
                    )
                    continue

                bars_map = self._load_bars_for_universe(universe, session)
                index_bars = self._index_bars(session)
                price_data = PriceDataMatrix(bars_map, index_bars)

                session_bars = {
                    sym: df.iloc[-1]
                    for sym, df in bars_map.items()
                    if not df.empty and df.iloc[-1]["date"] == session
                }

                trade_events = self.broker.process_session(session, session_bars)
                for ev in trade_events:
                    self.repo.record_trade(ev)
                    if ev.direction == "BUY" and ev.symbol in self.broker.portfolio.positions:
                        pos = self.broker.portfolio.positions[ev.symbol]
                        pos.sector = self._cache.get_sector(ev.symbol)
                    if self._debug.enabled:
                        if ev.direction == "BUY":
                            self._debug.broker(
                                session,
                                ev.symbol,
                                "FILL_BUY",
                                f"Filled buy GTT for {ev.symbol} at {ev.price:.2f} x {ev.quantity}",
                                price=ev.price,
                                quantity=ev.quantity,
                            )
                            if self.config.risk_management.require_box_reset_for_reentry:
                                self._breakout_reentry_blocked.add(ev.symbol)
                        elif ev.direction == "SELL":
                            self._debug.broker(
                                session,
                                ev.symbol,
                                "FILL_SELL",
                                f"Closed {ev.symbol} at {ev.price:.2f} ({ev.exit_reason})",
                                price=ev.price,
                                quantity=ev.quantity,
                                exit_reason=str(ev.exit_reason),
                            )

                last_closes = {s: float(row["close"]) for s, row in session_bars.items()}
                equity = self.broker.mark_to_market(last_closes)

                if self._prev_equity is not None:
                    daily_loss = max(0.0, self._prev_equity - equity)
                    if daily_loss >= self.config.risk_management.kill_switch_daily_loss_limit_inr:
                        kill_state = {
                            "active": True,
                            "tripped_on_date": session.isoformat(),
                            "daily_loss_inr_at_trip": daily_loss,
                        }
                        if self._debug.enabled:
                            self._debug.log(
                                session,
                                "RISK",
                                "KILL_SWITCH",
                                f"Kill switch tripped — daily loss {daily_loss:,.0f} INR",
                                details={"daily_loss_inr": daily_loss},
                            )
                self._prev_equity = equity

                open_positions = self.broker.get_open_positions()
                context = MarketContext(
                    target_date=session,
                    account_equity=equity,
                    settled_cash_inr=self.broker.portfolio.settled_cash,
                    open_positions=open_positions,
                    kill_switch_active=bool(kill_state.get("active")),
                )

                actions, state_registry, decisions = run_daily_strategy_iteration(
                    context,
                    price_data,
                    self._cache,
                    state_registry,
                    self.config,
                    universe,
                    self.broker.pending_symbols(),
                    self._debug if self._debug.enabled else None,
                    prev_box_states=self._prev_box_states,
                    breakout_reentry_blocked=self._breakout_reentry_blocked,
                    trading_days_to_date=days_to_date,
                )
                breakout_count = sum(1 for d in decisions if d.box_state == BoxStateEnum.BREAKOUT.value)
                buy_actions = sum(1 for a in actions if a.action_type.value == "PLACE_BUY_GTT")

                self.broker.apply_actions(session, actions)
                for action in actions:
                    if action.action_type == ActionType.CANCEL_BUY_GTT and self._debug.enabled:
                        self._debug.broker(
                            session,
                            action.symbol,
                            "CANCEL_GTT",
                            f"Cancelled buy GTT for {action.symbol}",
                            symbol=action.symbol,
                        )
                    if action.action_type == ActionType.PLACE_BUY_GTT and self._debug.enabled:
                        self._debug.broker(
                            session,
                            action.symbol,
                            "PLACE_GTT",
                            f"Placed buy GTT for {action.symbol} trigger={action.trigger_price:.2f} "
                            f"qty={action.quantity}",
                            trigger_price=action.trigger_price,
                            stop_loss_price=action.stop_loss_price,
                            target_price=action.target_price,
                            quantity=action.quantity,
                        )

                self._prev_box_states = {
                    sym: s.box_state.value for sym, s in state_registry.items()
                }

                self.repo.upsert_state_registry(state_registry)
                self.repo.append_decision_log(decisions)
                self.repo.set_system_state("kill_switch", kill_state)
                self.repo.set_system_state(
                    "equity_snapshot",
                    {"date": session.isoformat(), "equity_at_close_inr": equity},
                )

                peak = max((r["equity"] for r in self.equity_curve), default=equity)
                dd = 100.0 * (peak - equity) / peak if peak > 0 else 0.0
                self.equity_curve.append(
                    {
                        "date": session.isoformat(),
                        "equity": equity,
                        "settled_cash": self.broker.portfolio.settled_cash,
                        "drawdown_pct": dd,
                        "kill_switch_active": int(kill_state.get("active", False)),
                        "open_positions_count": len(open_positions),
                    }
                )

                self._progress.session(
                    session,
                    day_num,
                    total_days,
                    equity,
                    len(open_positions),
                    universe_size=len(universe),
                    breakout_count=breakout_count,
                    buy_actions=buy_actions,
                    closed_trades=len(self.broker.portfolio.closed_trades),
                    elapsed_sec=time.monotonic() - run_start,
                )
        finally:
            self._progress.info("Backtest loop finished — writing output files")
            self._progress.close()
            self._debug.close()

        return self.export_results(out_dir)

    def export_results(self, out_dir: Path | None = None) -> Path:
        out_dir = out_dir or _resolve_path(self.repo_root, self.config.backtest.export_directory)
        out_dir.mkdir(parents=True, exist_ok=True)

        decision_df = self.repo.get_decision_log_df()
        decision_df.to_csv(out_dir / "decision_log.csv", index=False)

        trades = self.repo.get_all_trades()
        pd.DataFrame(trades).to_csv(out_dir / "trade_ledger.csv", index=False)

        closed = pd.DataFrame(self.broker.portfolio.closed_trades)
        if not closed.empty:
            closed.to_csv(out_dir / "closed_trades.csv", index=False)

        eq_df = pd.DataFrame(self.equity_curve)
        eq_df.to_csv(out_dir / "equity_curve.csv", index=False)

        summary = self._build_summary(eq_df)
        summary["run_directory"] = str(out_dir)
        if self._invoked_at:
            summary["invoked_at"] = self._invoked_at.isoformat(timespec="seconds")
        (out_dir / "summary_report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return out_dir

    def _build_summary(self, eq_df: pd.DataFrame) -> dict:
        if eq_df.empty:
            return {"trades": 0, "cagr": 0.0, "max_drawdown_pct": 0.0}
        start_eq = self.config.backtest.initial_capital_inr
        end_eq = float(eq_df.iloc[-1]["equity"])
        years = max(len(eq_df) / 252, 0.01)
        cagr = (end_eq / start_eq) ** (1 / years) - 1 if start_eq > 0 else 0.0
        max_dd = float(eq_df["drawdown_pct"].max()) if "drawdown_pct" in eq_df else 0.0
        closed = self.broker.portfolio.closed_trades
        wins = sum(1 for t in closed if t["pnl"] > 0)
        return {
            "initial_capital_inr": start_eq,
            "final_equity_inr": end_eq,
            "cagr": round(cagr, 4),
            "max_drawdown_pct": round(max_dd, 2),
            "total_closed_trades": len(closed),
            "win_rate": round(wins / len(closed), 4) if closed else 0.0,
            "start_date": str(self._run_start or self.config.backtest.start_date),
            "end_date": str(self._run_end or self.config.backtest.end_date),
            "r_managed_runner_enabled": self.config.r_managed_runner.enabled,
        }
