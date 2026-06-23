"""R-managed runner exit policy — optional, config-gated (revert via r_managed_runner.enabled=false)."""

from __future__ import annotations

from src.config import AppConfig
from src.models import OpenPosition


def initial_risk_per_share(entry_price: float, initial_stop: float) -> float:
    return max(0.0, entry_price - initial_stop)


def unrealized_r(last_close: float, entry_price: float, initial_risk: float) -> float:
    if initial_risk <= 0:
        return 0.0
    return (last_close - entry_price) / initial_risk


def resolve_initial_stop(position: OpenPosition) -> float:
    if position.initial_stop_loss is not None:
        return position.initial_stop_loss
    return position.current_stop_loss


def breakeven_stop(entry_price: float) -> float:
    return entry_price


def apply_breakeven_floor(
    candidate_stop: float,
    last_close: float,
    position: OpenPosition,
    cfg: AppConfig,
) -> float:
    rrm = cfg.r_managed_runner
    if not rrm.enabled:
        return candidate_stop
    risk = initial_risk_per_share(position.entry_price, resolve_initial_stop(position))
    if unrealized_r(last_close, position.entry_price, risk) < rrm.breakeven_r_threshold:
        return candidate_stop
    return max(candidate_stop, breakeven_stop(position.entry_price))


def cap_target_at_r(
    entry_price: float,
    initial_stop: float,
    structural_target: float,
    cfg: AppConfig,
) -> float:
    rrm = cfg.r_managed_runner
    if not rrm.enabled:
        return structural_target
    risk = initial_risk_per_share(entry_price, initial_stop)
    if risk <= 0:
        return structural_target
    cap = entry_price + rrm.max_target_r * risk
    return min(structural_target, cap)
