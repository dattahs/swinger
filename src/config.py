"""Load and validate config.yaml — REQUIREMENTS v1.2 Section 13."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class BrokerConfig(BaseModel):
    provider: str = "upstox"


class AuthConfig(BaseModel):
    token_refresh_strategy: str = "manual_daily_login"
    access_token_secret_arn: str = ""
    totp_secret_arn: str = ""


class StorageConfig(BaseModel):
    live_backend: str = "dynamodb"
    backtest_backend: str = "sqlite"


class SystemConfig(BaseModel):
    mode: str = "discretionary"
    execution_segment: str = "CASH"
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)


class ProgressLogConfig(BaseModel):
    enabled: bool = True
    log_to_console: bool = True
    log_file: str = "progress.log"


class DebugLogConfig(BaseModel):
    enabled: bool = False
    log_to_console: bool = True
    log_file: str = "action_debug.csv"
    include_box_transitions: bool = True
    include_per_symbol_scanning: bool = False
    include_gate_rejections: bool = True


class BacktestConfig(BaseModel):
    target_segment: str = "NIFTY_500"
    start_date: date
    end_date: date
    initial_capital_inr: float = 500_000.0
    price_warmup_start_date: date
    export_directory: str = "./backtest_outputs"
    timestamped_runs: bool = True
    simulation_slippage_pct: float = 0.05
    execution_environment: str = "local"
    data_db_path: str = "./data/processed/swinger_data.db"
    progress_log: ProgressLogConfig = Field(default_factory=ProgressLogConfig)
    debug_log: DebugLogConfig = Field(default_factory=DebugLogConfig)


class AdaptiveNewHighLookbackConfig(BaseModel):
    """Map market regime (index vs SMA) to new-high lookback in weeks."""

    enabled: bool = False
    regime_index: str = "NIFTY 50"
    sma_period: int = 50
    min_lookback_weeks: float = 9.0
    max_lookback_weeks: float = 39.0
    calibration_years: float = 5.0
    low_percentile: float = 10.0
    high_percentile: float = 90.0
    recalibration_cadence: str = "daily"
    spread_jump_threshold_pct: float = 2.0


class UniverseFilters(BaseModel):
    min_daily_volume_shares: int = 500_000
    min_daily_turnover_inr_cr: float = 10.0
    min_stock_price_inr: float = 100.0
    lookback_years_for_52wk_high: int = 1
    new_high_lookback_weeks: int = 26
    require_new_52wk_high: bool = True
    adaptive_new_high_lookback: AdaptiveNewHighLookbackConfig = Field(
        default_factory=AdaptiveNewHighLookbackConfig
    )
    exclude_asm_gsm: bool = True


class FundamentalFilters(BaseModel):
    source: str = "nse_official_xbrl_pit"
    point_in_time_required: bool = True
    min_revenue_growth_pct: float = 15.0
    min_eps_growth_pct: float = 15.0
    min_roe_pct: float = 15.0
    min_roce_pct: float = 15.0
    max_debt_to_equity: float = 0.5
    min_promoter_holding_pct: float = 40.0
    avoid_days_before_earnings: int = 5
    enforce_long_term_growth_group: bool = True


class MarketTrendFilter(BaseModel):
    """Market trend gate for Darvas box formation / breakout.

    mode:
      - nifty: require NIFTY 50 above MAs; optional sector ETF override.
      - sector_index: require each symbol's NSE sector index (or ETF fallback) above MAs.
    """

    mode: Literal["nifty", "sector_index"] = "nifty"
    index: str = "NIFTY 50"
    moving_averages: list[int] = Field(default_factory=lambda: [50, 200])
    rule: str = "index_close_above_both_mas"
    allow_sector_trend_override: bool = True

    @model_validator(mode="after")
    def validate_mode(self) -> MarketTrendFilter:
        if self.mode not in ("nifty", "sector_index"):
            raise ValueError("market_trend_filter.mode must be 'nifty' or 'sector_index'")
        return self


class DarvasBoxConfig(BaseModel):
    box_bound_rule: str = "hybrid_darvas_atr"
    darvas_reversal_days: int = 3
    atr_period: int = 20
    atr_multiplier: float = 2.0
    min_box_duration_days: int = 5
    max_box_duration_days: int = 30
    min_box_height_pct: float = 3.0
    max_box_height_pct: float = 20.0
    breakout_volume_multiplier: float = 1.5
    required_price_history_days: int = 280
    breakout_reset_above_top_pct: float = 2.0
    market_trend_filter: MarketTrendFilter = Field(default_factory=MarketTrendFilter)


class RiskManagementConfig(BaseModel):
    account_risk_pct: float = 1.0
    max_capital_per_trade_pct: float = 10.0
    max_sector_exposure_pct: float = 30.0
    max_concurrent_positions: int = 10
    stop_loss_buffer_fraction_inr: float = 0.05
    gtt_trigger_buffer_inr: float = 0.05
    max_portfolio_loss_per_trade_pct: float = 10.0
    min_structural_r_ratio: float = 3.0
    kill_switch_daily_loss_limit_inr: float = 25_000.0
    kill_switch_evaluation_timing: str = "eod_only"
    kill_switch_action: str = "halt_new_entries"
    sector_classification_source: str = "nse_official"
    gtt_expiry_sessions: int = 5
    require_box_reset_for_reentry: bool = True
    max_hold_sessions: int = 63
    stale_box_tsl_daily_pct: float = 10.0
    box_same_tolerance_pct: float = 2.0
    gtt_capital_overcommit_factor: float = 1.0
    entry_require_close_above_sma: bool = True
    entry_sma_period: int = 20
    entry_post_breakout_consecutive_red_high_vol: int = 2


class TrailingStopConfig(BaseModel):
    method: str = "box_bottom_ratchet"
    min_ratchet_inr: float = 0.05
    max_trail_risk_pct: float = 10.0


class CandidateRankingConfig(BaseModel):
    primary_metric: str = "structural_rr"
    tiebreakers: list[str] = Field(default_factory=lambda: ["sector_rs_percentile", "breakout_volume_ratio"])
    sector_rs_lookback_days: int = 63


class LiveConfig(BaseModel):
    """Local / paper live execution settings (Section 9)."""

    paper_mode: bool = True
    local_db_path: str = "./data/live/swinger_live.db"
    instrument_map_path: str = "./data/instruments/upstox_nse_eq.json"
    token_file: str = "./data/live/upstox_token.json"
    upstox_api_base: str = "https://api.upstox.com"
    upstox_redirect_uri: str = "http://127.0.0.1:5000/callback"
    api_timeout_sec: int = 30
    login_headless: bool = False
    login_timeout_sec: int = 300
    browser_profile_dir: str = "./data/.upstox_browser"
    adopt_broker_truth: bool = True
    allow_drift: bool = False
    initial_capital_inr: float = 500_000.0
    warmup_state: bool = True
    warmup_from: date = date(2025, 10, 1)
    warmup_cache_dir: str = "./data/live/warmup_cache"


class AppConfig(BaseModel):
    system: SystemConfig = Field(default_factory=SystemConfig)
    backtest: BacktestConfig
    live: LiveConfig = Field(default_factory=LiveConfig)
    universe_filters: UniverseFilters = Field(default_factory=UniverseFilters)
    fundamental_filters: FundamentalFilters = Field(default_factory=FundamentalFilters)
    darvas_box: DarvasBoxConfig = Field(default_factory=DarvasBoxConfig)
    risk_management: RiskManagementConfig = Field(default_factory=RiskManagementConfig)
    trailing_stop: TrailingStopConfig = Field(default_factory=TrailingStopConfig)
    candidate_ranking: CandidateRankingConfig = Field(default_factory=CandidateRankingConfig)

    @model_validator(mode="after")
    def validate_auth_binding(self) -> AppConfig:
        mode = self.system.mode
        strategy = self.system.auth.token_refresh_strategy
        if mode == "discretionary" and strategy != "manual_daily_login":
            raise ValueError("discretionary mode requires manual_daily_login")
        if mode == "fully_automated" and strategy != "totp_automated_login":
            raise ValueError("fully_automated mode requires totp_automated_login")
        if self.fundamental_filters.source != "nse_official_xbrl_pit":
            raise ValueError("fundamental_filters.source must be nse_official_xbrl_pit")
        live = self.system.storage.live_backend.lower()
        if live in ("sqlite", "s3"):
            raise ValueError("live_backend must be dynamodb, not sqlite/s3")
        return self


def _parse_dates(raw: dict[str, Any]) -> dict[str, Any]:
    if "backtest" in raw:
        bt = raw["backtest"]
        for key in ("start_date", "end_date", "price_warmup_start_date"):
            if key in bt and isinstance(bt[key], str):
                bt[key] = date.fromisoformat(bt[key])
    live = raw.get("live")
    if live and isinstance(live.get("warmup_from"), str):
        live["warmup_from"] = date.fromisoformat(live["warmup_from"])
    return raw


def load_config(path: str | Path, *, validate_auth: bool = True) -> AppConfig:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raise ValueError(f"Empty config: {path}")
    if "lookback_years_for_doubling" in raw.get("universe_filters", {}):
        raise ValueError("Deprecated key lookback_years_for_doubling — use lookback_years_for_52wk_high")
    raw = _parse_dates(raw)
    if raw.get("system", {}).get("broker", {}).get("provider") is None:
        raw.setdefault("system", {}).setdefault("broker", {})["provider"] = "upstox"
    cfg = AppConfig.model_validate(raw)
    if not validate_auth:
        return cfg
    return cfg


def load_config_relaxed(path: str | Path) -> AppConfig:
    """Load config skipping auth/storage checks (tests only)."""
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw = _parse_dates(raw)
    return AppConfig.model_construct(
        system=SystemConfig.model_validate(raw.get("system", {})),
        backtest=BacktestConfig.model_validate(raw["backtest"]),
        universe_filters=UniverseFilters.model_validate(raw.get("universe_filters", {})),
        fundamental_filters=FundamentalFilters.model_validate(raw.get("fundamental_filters", {})),
        darvas_box=DarvasBoxConfig.model_validate(raw.get("darvas_box", {})),
        risk_management=RiskManagementConfig.model_validate(raw.get("risk_management", {})),
        trailing_stop=TrailingStopConfig.model_validate(raw.get("trailing_stop", {})),
        candidate_ranking=CandidateRankingConfig.model_validate(raw.get("candidate_ranking", {})),
        live=LiveConfig.model_validate(raw.get("live", {})),
    )
