"""Load and validate config.yaml — REQUIREMENTS v1.2 Section 13."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
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
    live_backend: str = "sqlite"
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
    send_email_on_complete: bool = True
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
    min_box_duration_days: int = 4
    max_box_duration_days: int = 30
    min_box_height_pct: float = 3.0
    max_box_height_pct: float = 20.0
    breakout_volume_multiplier: float = 1.5
    breakout_volume_sma_period: int = 20
    required_price_history_days: int = 280
    price_history_buffer_days: int = 50
    breakout_reset_above_top_pct: float = 2.0
    market_trend_filter: MarketTrendFilter = Field(default_factory=MarketTrendFilter)


@dataclass(frozen=True)
class DarvasTuningParam:
    """One tunable knob in the Darvas scan → entry → trail pipeline."""

    key: str
    path: str
    group: Literal["box", "universe", "entry", "trail", "ranking"]
    description: str = ""


# Short keys map to dot-paths on AppConfig — use with darvas_algo_snapshot / apply_darvas_algo_overrides.
DARVAS_ALGO_TUNING_PARAMS: tuple[DarvasTuningParam, ...] = (
    DarvasTuningParam("darvas_reversal_days", "darvas_box.darvas_reversal_days", "box"),
    DarvasTuningParam("atr_period", "darvas_box.atr_period", "box"),
    DarvasTuningParam("atr_multiplier", "darvas_box.atr_multiplier", "box"),
    DarvasTuningParam("min_box_duration_days", "darvas_box.min_box_duration_days", "box"),
    DarvasTuningParam("max_box_duration_days", "darvas_box.max_box_duration_days", "box"),
    DarvasTuningParam("min_box_height_pct", "darvas_box.min_box_height_pct", "box"),
    DarvasTuningParam("max_box_height_pct", "darvas_box.max_box_height_pct", "box"),
    DarvasTuningParam("breakout_volume_multiplier", "darvas_box.breakout_volume_multiplier", "box"),
    DarvasTuningParam("breakout_volume_sma_period", "darvas_box.breakout_volume_sma_period", "box"),
    DarvasTuningParam(
        "breakout_reset_above_top_pct", "darvas_box.breakout_reset_above_top_pct", "box"
    ),
    DarvasTuningParam(
        "required_price_history_days", "darvas_box.required_price_history_days", "box"
    ),
    DarvasTuningParam(
        "price_history_buffer_days", "darvas_box.price_history_buffer_days", "box"
    ),
    DarvasTuningParam("trend_mode", "darvas_box.market_trend_filter.mode", "box"),
    DarvasTuningParam("trend_index", "darvas_box.market_trend_filter.index", "box"),
    DarvasTuningParam(
        "trend_moving_averages", "darvas_box.market_trend_filter.moving_averages", "box"
    ),
    DarvasTuningParam(
        "allow_sector_trend_override",
        "darvas_box.market_trend_filter.allow_sector_trend_override",
        "box",
    ),
    DarvasTuningParam("require_new_52wk_high", "universe_filters.require_new_52wk_high", "universe"),
    DarvasTuningParam(
        "new_high_lookback_weeks", "universe_filters.new_high_lookback_weeks", "universe"
    ),
    DarvasTuningParam(
        "lookback_years_for_52wk_high",
        "universe_filters.lookback_years_for_52wk_high",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_lookback_enabled",
        "universe_filters.adaptive_new_high_lookback.enabled",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_regime_index",
        "universe_filters.adaptive_new_high_lookback.regime_index",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_sma_period",
        "universe_filters.adaptive_new_high_lookback.sma_period",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_min_lookback_weeks",
        "universe_filters.adaptive_new_high_lookback.min_lookback_weeks",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_max_lookback_weeks",
        "universe_filters.adaptive_new_high_lookback.max_lookback_weeks",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_low_percentile",
        "universe_filters.adaptive_new_high_lookback.low_percentile",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_high_percentile",
        "universe_filters.adaptive_new_high_lookback.high_percentile",
        "universe",
    ),
    DarvasTuningParam(
        "adaptive_spread_jump_threshold_pct",
        "universe_filters.adaptive_new_high_lookback.spread_jump_threshold_pct",
        "universe",
    ),
    DarvasTuningParam(
        "gtt_trigger_buffer_inr", "risk_management.gtt_trigger_buffer_inr", "entry"
    ),
    DarvasTuningParam(
        "stop_loss_buffer_fraction_inr",
        "risk_management.stop_loss_buffer_fraction_inr",
        "entry",
    ),
    DarvasTuningParam(
        "min_structural_r_ratio", "risk_management.min_structural_r_ratio", "entry"
    ),
    DarvasTuningParam(
        "require_box_reset_for_reentry",
        "risk_management.require_box_reset_for_reentry",
        "entry",
    ),
    DarvasTuningParam(
        "entry_require_close_above_sma",
        "risk_management.entry_require_close_above_sma",
        "entry",
    ),
    DarvasTuningParam("entry_sma_period", "risk_management.entry_sma_period", "entry"),
    DarvasTuningParam(
        "entry_post_breakout_consecutive_red_high_vol",
        "risk_management.entry_post_breakout_consecutive_red_high_vol",
        "entry",
    ),
    DarvasTuningParam(
        "target_box_height_multiplier",
        "risk_management.target_box_height_multiplier",
        "entry",
    ),
    DarvasTuningParam(
        "dynamic_atr_target_enabled",
        "risk_management.dynamic_atr_target_enabled",
        "entry",
    ),
    DarvasTuningParam(
        "dynamic_atr_target_band_pct",
        "risk_management.dynamic_atr_target_band_pct",
        "entry",
    ),
    DarvasTuningParam("max_hold_sessions", "risk_management.max_hold_sessions", "trail"),
    DarvasTuningParam(
        "stale_box_tsl_daily_pct", "risk_management.stale_box_tsl_daily_pct", "trail"
    ),
    DarvasTuningParam("box_same_tolerance_pct", "risk_management.box_same_tolerance_pct", "trail"),
    DarvasTuningParam("trail_min_ratchet_inr", "trailing_stop.min_ratchet_inr", "trail"),
    DarvasTuningParam("trail_max_risk_pct", "trailing_stop.max_trail_risk_pct", "trail"),
    DarvasTuningParam(
        "sector_rs_lookback_days", "candidate_ranking.sector_rs_lookback_days", "ranking"
    ),
)

DARVAS_ALGO_PARAM_PATHS: dict[str, str] = {p.key: p.path for p in DARVAS_ALGO_TUNING_PARAMS}

# Repo-relative paths for canonical strategy configs.
DEFAULT_CONFIG_PATH = "config.yaml"
BASELINE_NEXT_BEST_CONFIG_PATH = "configs/baseline-next-best.yaml"


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
    target_box_height_multiplier: float = 1.0
    dynamic_atr_target_enabled: bool = False
    dynamic_atr_target_band_pct: float = 20.0


class TrailingStopConfig(BaseModel):
    method: str = "box_bottom_ratchet"
    min_ratchet_inr: float = 0.05
    max_trail_risk_pct: float = 10.0


class RManagedRunnerConfig(BaseModel):
    """Optional winner-management policy: breakeven at 2R, box ratchet, 5R target cap."""

    enabled: bool = False
    breakeven_r_threshold: float = 2.0
    max_target_r: float = 5.0


class CandidateRankingConfig(BaseModel):
    primary_metric: str = "structural_rr"
    tiebreakers: list[str] = Field(default_factory=lambda: ["sector_rs_percentile", "breakout_volume_ratio"])
    sector_rs_lookback_days: int = 63


class SectorRegimeGateConfig(BaseModel):
    """Optional council gate: skip new entries in synchronized ranging / low-exposure regimes."""

    enabled: bool = False
    council_window_months: int = 6
    require_dominant_regime: str = "RANGING"
    require_dispersion: str = "LOW"
    max_recommended_exposure: float = 0.30
    vix_csv_path: str = "./data/processed/india_vix_daily.csv"
    skip_breadth: bool = True


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
    mock_broker: bool = False
    initial_capital_inr: float = 500_000.0
    override_broker_capital: bool = False
    assume_gtt_fills_from_bars: bool = False
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
    r_managed_runner: RManagedRunnerConfig = Field(default_factory=RManagedRunnerConfig)
    candidate_ranking: CandidateRankingConfig = Field(default_factory=CandidateRankingConfig)
    sector_regime_gate: SectorRegimeGateConfig = Field(default_factory=SectorRegimeGateConfig)

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
        if live == "s3":
            raise ValueError("live_backend s3 is not supported — use sqlite on VPS")
        if live not in ("sqlite", "dynamodb"):
            raise ValueError("live_backend must be sqlite (v1 VPS) or dynamodb (v2)")
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


def _config_get(cfg: AppConfig, path: str) -> Any:
    obj: Any = cfg
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _config_set(cfg: AppConfig, path: str, value: Any) -> None:
    parts = path.split(".")
    obj: Any = cfg
    for part in parts[:-1]:
        obj = getattr(obj, part)
    setattr(obj, parts[-1], value)


def darvas_price_history_days(cfg: AppConfig) -> int:
    """Bars to load for Darvas / ATR warmup (required history + buffer)."""
    d = cfg.darvas_box
    return d.required_price_history_days + d.price_history_buffer_days


def darvas_algo_snapshot(cfg: AppConfig) -> dict[str, Any]:
    """Flatten all Darvas-algo tuning knobs using short keys."""
    return {key: _config_get(cfg, path) for key, path in DARVAS_ALGO_PARAM_PATHS.items()}


def apply_darvas_algo_overrides(cfg: AppConfig, overrides: dict[str, Any]) -> AppConfig:
    """Return a copy of cfg with Darvas tuning overrides (short or dot-path keys)."""
    cfg = cfg.model_copy(deep=True)
    for raw_key, value in overrides.items():
        path = DARVAS_ALGO_PARAM_PATHS.get(raw_key, raw_key)
        _config_set(cfg, path, value)
    return cfg


def darvas_algo_fingerprint(cfg: AppConfig) -> str:
    """Hash every field that affects Darvas state during warmup / replay."""
    payload = darvas_algo_snapshot(cfg)
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


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
        r_managed_runner=RManagedRunnerConfig.model_validate(raw.get("r_managed_runner", {})),
        candidate_ranking=CandidateRankingConfig.model_validate(raw.get("candidate_ranking", {})),
        sector_regime_gate=SectorRegimeGateConfig.model_validate(raw.get("sector_regime_gate", {})),
        live=LiveConfig.model_validate(raw.get("live", {})),
    )
