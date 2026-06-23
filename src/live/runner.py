"""Daily live orchestration — REQUIREMENTS v1.2 Section 9."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.backtest.backtester import evaluate_kill_switch
from src.broker.auth import TokenStore, UpstoxLoginAutomator
from src.broker.executor import GTTExecutor
from src.broker.instruments import InstrumentResolver
from src.broker.reconcile import compute_equity, reconcile_broker_state
from src.broker.upstox import UpstoxGTTClient
from src.config import AppConfig, LiveConfig
from src.engine.engine import PriceDataMatrix, run_daily_strategy_iteration
from src.models import ActionType, MarketContext, PlannedGTTAction, make_idempotency_key
from src.repository.base import Repository
from src.repository.sqlite import SqliteDataLake
from src.repository.sqlite_live import SqliteLiveRepository

logger = logging.getLogger(__name__)


@dataclass
class LiveRunReport:
    session_date: date
    reconciliation_synced: bool
    drift_count: int
    equity_inr: float
    actions_planned: int
    actions_executed: int
    execution_failures: int
    kill_switch_active: bool


class LiveRunner:
    """End-of-day live pipeline: auth → reconcile → strategy → execute."""

    def __init__(
        self,
        config: AppConfig,
        *,
        repo_root: Path | None = None,
        force_login: bool = False,
        skip_warmup: bool = False,
        force_warmup: bool = False,
    ) -> None:
        self.config = config
        self.live: LiveConfig = config.live
        self.repo_root = repo_root or Path.cwd()
        self.force_login = force_login
        self.skip_warmup = skip_warmup
        self.force_warmup = force_warmup

        db_path = self._resolve(self.live.local_db_path)
        data_path = self._resolve(config.backtest.data_db_path)
        self.repo: Repository = SqliteLiveRepository(db_path)
        self.data_lake = SqliteDataLake(data_path)
        self.instruments = InstrumentResolver(self._resolve(self.live.instrument_map_path))

        token_store = TokenStore(self._resolve(self.live.token_file))
        self._token_store = token_store
        self._login = UpstoxLoginAutomator(
            api_key=os.environ.get("UPSTOX_API_KEY", ""),
            api_secret=os.environ.get("UPSTOX_API_SECRET", ""),
            redirect_uri=os.environ.get(
                "UPSTOX_REDIRECT_URI",
                self.live.upstox_redirect_uri,
            ),
            token_store=token_store,
            browser_profile_dir=self._resolve(self.live.browser_profile_dir),
            headless=self.live.login_headless,
            timeout_sec=self.live.login_timeout_sec,
        )

    def _resolve(self, path_str: str) -> Path:
        p = Path(path_str)
        return p if p.is_absolute() else self.repo_root / p

    def _build_broker(self, access_token: str) -> UpstoxGTTClient:
        return UpstoxGTTClient(
            access_token=access_token,
            instruments=self.instruments,
            api_base=self.live.upstox_api_base,
            paper_mode=self.live.paper_mode,
            timeout_sec=self.live.api_timeout_sec,
        )

    def run(self, session_date: date | None = None) -> LiveRunReport:
        session_date = session_date or self.data_lake.get_latest_trading_day()
        if session_date is None:
            raise RuntimeError("No trading days in data lake — run bhavcopy ingest first")

        pricing_date = self._resolve_pricing_date(session_date)

        if not self.skip_warmup:
            self._warm_state_registry(session_date, pricing_date)

        access_token = self._ensure_token()
        broker = self._build_broker(access_token)

        recon = reconcile_broker_state(
            session_date,
            broker,
            self.repo,
            adopt_broker_truth=self.live.adopt_broker_truth,
        )
        settled_cash = recon.settled_cash_inr
        if self.live.paper_mode and settled_cash <= 0 and not self.repo.get_system_state("equity_snapshot"):
            settled_cash = self.live.initial_capital_inr
            self.repo.set_system_state(
                "broker_sync",
                {
                    **(self.repo.get_system_state("broker_sync") or {}),
                    "settled_cash_inr": settled_cash,
                    "paper_seeded_capital": True,
                },
            )
        if not recon.is_synced and not self.live.allow_drift:
            raise RuntimeError(
                f"Broker/ledger drift ({len(recon.drifts)} items) — "
                "fix manually or set live.allow_drift=true"
            )

        open_positions = self.repo.get_open_positions()
        price_map = self._close_prices(pricing_date)
        equity = compute_equity(settled_cash, open_positions, price_map)

        prev_equity = (self.repo.get_system_state("equity_snapshot") or {}).get("equity_inr")
        kill_state = self.repo.get_system_state("kill_switch") or {"active": False}
        kill_state = evaluate_kill_switch(
            equity,
            float(prev_equity) if prev_equity is not None else None,
            self.config,
            kill_state,
        )
        self.repo.set_system_state("kill_switch", kill_state)
        self.repo.set_system_state(
            "equity_snapshot",
            {"date": session_date.isoformat(), "equity_inr": equity},
        )

        universe = self.data_lake.get_universe(pricing_date)
        bars = self._load_bars(universe, pricing_date)
        index_bars = self.data_lake.get_daily_bars(
            self.config.darvas_box.market_trend_filter.index,
            pricing_date,
            250,
        )
        price_data = PriceDataMatrix(bars, index_bars)
        state_registry = self.repo.get_state_registry()
        trading_days = [d for d in self.data_lake.get_trading_days(
            self.config.backtest.price_warmup_start_date,
            session_date,
        ) if d <= session_date]

        expiry_cancels = self._expire_stale_pending(session_date, trading_days)
        blocked_raw = self.repo.get_system_state("breakout_reentry_blocked") or {}
        breakout_reentry_blocked = set(blocked_raw.get("symbols", []))
        context = MarketContext(
            target_date=session_date,
            account_equity=equity,
            settled_cash_inr=settled_cash,
            open_positions=open_positions,
            kill_switch_active=bool(kill_state.get("active")),
        )

        actions, new_registry, decision_rows = run_daily_strategy_iteration(
            context,
            price_data,
            self.data_lake,
            state_registry,
            self.config,
            universe,
            pending_symbols=recon.pending_symbols,
            breakout_reentry_blocked=breakout_reentry_blocked,
        )
        actions = expiry_cancels + actions
        self.repo.upsert_state_registry(new_registry)
        self.repo.append_decision_log(decision_rows)

        executor = GTTExecutor(
            broker,
            self.repo,
            self.instruments,
            paper_mode=self.live.paper_mode,
        )
        exec_report = executor.apply_planned_actions(session_date, actions)

        return LiveRunReport(
            session_date=session_date,
            reconciliation_synced=recon.is_synced,
            drift_count=len(recon.drifts),
            equity_inr=equity,
            actions_planned=len([a for a in actions if a.action_type != ActionType.NO_CHANGE]),
            actions_executed=len([r for r in exec_report.results if r.success]),
            execution_failures=len(exec_report.failures),
            kill_switch_active=bool(kill_state.get("active")),
        )

    def _ensure_token(self) -> str:
        env_token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
        if env_token and not self.force_login:
            return env_token

        record = self._token_store.load()
        if record and self._token_store.is_valid_for_today(record) and not self.force_login:
            return record.access_token

        if os.environ.get("UPSTOX_API_KEY", "").strip():
            return self._login.ensure_access_token(force_login=self.force_login)

        if self.live.paper_mode:
            logger.warning(
                "No Upstox credentials — broker reconcile will be empty. "
                "Copy .env.example to .env and run with --login"
            )
            return ""
        raise RuntimeError("Set UPSTOX_API_KEY + UPSTOX_API_SECRET in .env, then run with --login")

    def _close_prices(self, session_date: date) -> dict[str, float]:
        symbols = {p.symbol for p in self.repo.get_open_positions()}
        pending = self.repo.get_system_state("pending_gtts") or {}
        symbols |= set(pending.keys())
        out: dict[str, float] = {}
        for sym in symbols:
            df = self.data_lake.get_daily_bars(sym, session_date, 1)
            if not df.empty:
                out[sym] = float(df.iloc[-1]["close"])
        return out

    def _load_bars(self, universe: list[str], end: date) -> dict:
        days = self.config.darvas_box.required_price_history_days + 50
        symbols = self.data_lake.filter_symbols_with_bar_on(universe, end)
        return {s: self.data_lake.get_daily_bars(s, end, days) for s in symbols}

    def _expire_stale_pending(
        self,
        session_date: date,
        trading_days: list[date],
    ) -> list[PlannedGTTAction]:
        from src.backtest.virtual_broker import count_sessions_waiting

        max_sess = self.config.risk_management.gtt_expiry_sessions
        pending = self.repo.get_system_state("pending_gtts") or {}
        cancels: list[PlannedGTTAction] = []
        for sym, row in list(pending.items()):
            placed_raw = row.get("placed_date", "")
            if not placed_raw:
                continue
            placed = date.fromisoformat(str(placed_raw)[:10])
            if count_sessions_waiting(placed, session_date, trading_days) > max_sess:
                cancels.append(
                    PlannedGTTAction(
                        symbol=sym,
                        action_type=ActionType.CANCEL_BUY_GTT,
                        idempotency_key=make_idempotency_key(
                            sym, session_date, ActionType.CANCEL_BUY_GTT.value
                        ),
                    )
                )
        return cancels

    def _warm_state_registry(self, session_date: date, pricing_date: date) -> None:
        if self.skip_warmup:
            return
        if self.repo.get_state_registry() and not self.force_warmup:
            return
        if not self.live.warmup_state:
            logger.warning("Empty Darvas state registry — enable live.warmup_state or import state")
            return
        trading_days = self.data_lake.get_trading_days(self.live.warmup_from, pricing_date)
        if not trading_days:
            return
        if session_date > pricing_date:
            warm_end = pricing_date
        else:
            warm_end = (
                trading_days[-2]
                if trading_days[-1] == pricing_date and len(trading_days) > 1
                else trading_days[-1]
            )
        if warm_end >= session_date and session_date <= pricing_date and not self.force_warmup:
            return

        from src.live.warmup_cache import (
            darvas_warmup_fingerprint,
            save_warmup_cache,
            try_load_warmup_cache,
            warmup_cache_path,
        )

        if not self.force_warmup:
            cached = try_load_warmup_cache(
                self.repo_root,
                self.config,
                self.live.warmup_cache_dir,
                self.live.warmup_from,
                warm_end,
            )
            if cached:
                registry, blocked = cached
                self.repo.upsert_state_registry(registry)
                self.repo.set_system_state(
                    "breakout_reentry_blocked",
                    {"symbols": sorted(blocked)},
                )
                logger.info("Warmup from cache — %d symbols in state registry", len(registry))
                return

        logger.info(
            "Warming Darvas state registry %s -> %s (session %s, no broker actions)",
            self.live.warmup_from,
            warm_end,
            session_date,
        )
        from src.backtest.backtester import Backtester

        cfg = self.config.model_copy(deep=True)
        cfg.backtest.progress_log.enabled = False
        cfg.backtest.debug_log.enabled = False
        bt = Backtester(cfg, repo_root=self.repo_root)
        bt.run(start=self.live.warmup_from, end=warm_end)
        registry = bt.repo.get_state_registry()
        self.repo.upsert_state_registry(registry)
        blocked = set(bt._breakout_reentry_blocked)
        self.repo.set_system_state(
            "breakout_reentry_blocked",
            {"symbols": sorted(blocked)},
        )
        fp = darvas_warmup_fingerprint(self.config)
        cache_path = warmup_cache_path(
            self.repo_root,
            self.live.warmup_cache_dir,
            self.live.warmup_from,
            warm_end,
            fp,
        )
        save_warmup_cache(
            cache_path,
            registry,
            blocked,
            warmup_from=self.live.warmup_from,
            warm_end=warm_end,
            fingerprint=fp,
        )
        logger.info("Warmup complete — %d symbols in state registry", len(registry))

    def _resolve_pricing_date(self, session_date: date) -> date:
        latest = self.data_lake.get_latest_trading_day(on_or_before=session_date)
        if latest is None:
            latest = self.data_lake.get_latest_trading_day()
        if latest is None:
            raise RuntimeError("No price bars in data lake — run bhavcopy ingest first")
        if session_date > latest:
            logger.warning(
                "Session %s is ahead of ingested data (%s) — strategy uses %s EOD bars",
                session_date,
                latest,
                latest,
            )
            return latest
        return session_date
