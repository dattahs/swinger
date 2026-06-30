"""Sector regime council gate — block new entries in low-conviction ranging markets."""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from src.analysis.sector_regime_council import CouncilRequest, run_sector_regime_council
from src.config import SectorRegimeGateConfig


@dataclass(frozen=True)
class GateSnapshot:
    blocked: bool
    reason: str
    dominant_regime: str
    dispersion: str
    recommended_exposure: float
    council_as_of: date


def evaluate_gate_from_summary(
    summary: dict,
    cfg: SectorRegimeGateConfig,
) -> tuple[bool, str]:
    """Return (block_new_entries, human-readable reason)."""
    if not cfg.enabled:
        return False, "gate disabled"

    cs = summary
    dominant = cs.get("dominant_regime", "")
    dispersion = cs.get("regime_dispersion", "")
    exposure = float(cs.get("recommended_overall_exposure", 1.0))

    if cfg.require_dominant_regime and dominant != cfg.require_dominant_regime:
        return False, f"dominant={dominant} (not {cfg.require_dominant_regime})"
    if cfg.require_dispersion and dispersion != cfg.require_dispersion:
        return False, f"dispersion={dispersion} (not {cfg.require_dispersion})"
    if exposure >= cfg.max_recommended_exposure:
        return False, f"exposure={exposure:.0%} >= {cfg.max_recommended_exposure:.0%}"

    return (
        True,
        f"RANGING chop: dominant={dominant}, dispersion={dispersion}, exposure={exposure:.0%}",
    )


def _month_end_anchors(trading_days: list[date]) -> list[date]:
    by_month: dict[tuple[int, int], date] = {}
    for d in trading_days:
        by_month[(d.year, d.month)] = d
    return sorted(by_month.values())


def build_gate_schedule(
    trading_days: list[date],
    *,
    db_path: Path,
    vix_path: Path,
    cfg: SectorRegimeGateConfig,
    repo_root: Path,
) -> dict[date, GateSnapshot]:
    """
    Precompute council gate state on month-end anchors; forward-fill to each session.

    Uses skip_breadth from config for backtest performance (index-level regime signals).
    """
    if not cfg.enabled or not trading_days:
        return {}

    extended_start = trading_days[0]
    extended_end = trading_days[-1]
    all_days = trading_days
    if hasattr(trading_days, "__iter__"):
        pass

    from src.repository.sqlite import SqliteDataLake

    lake = SqliteDataLake(db_path)
    warmup_days = lake.get_trading_days(
        date(extended_start.year - 1, extended_start.month, 1),
        extended_end,
    )
    anchors = _month_end_anchors(warmup_days)
    anchor_snapshots: dict[date, GateSnapshot] = {}

    vix = vix_path if vix_path.is_absolute() else repo_root / vix_path

    for anchor in anchors:
        result = run_sector_regime_council(
            CouncilRequest(
                as_of=anchor,
                window_months=cfg.council_window_months,
                db_path=db_path,
                vix_csv_path=vix,
                skip_breadth=cfg.skip_breadth,
            )
        )
        blocked, reason = evaluate_gate_from_summary(result["council_summary"], cfg)
        cs = result["council_summary"]
        anchor_snapshots[anchor] = GateSnapshot(
            blocked=blocked,
            reason=reason,
            dominant_regime=str(cs.get("dominant_regime", "")),
            dispersion=str(cs.get("regime_dispersion", "")),
            recommended_exposure=float(cs.get("recommended_overall_exposure", 0)),
            council_as_of=anchor,
        )

    sorted_anchors = sorted(anchor_snapshots)
    schedule: dict[date, GateSnapshot] = {}
    for session in trading_days:
        idx = bisect.bisect_right(sorted_anchors, session) - 1
        if idx < 0:
            schedule[session] = GateSnapshot(
                blocked=False,
                reason="no council history before session",
                dominant_regime="",
                dispersion="",
                recommended_exposure=1.0,
                council_as_of=session,
            )
        else:
            schedule[session] = anchor_snapshots[sorted_anchors[idx]]
    return schedule


def gate_active_on(schedule: dict[date, GateSnapshot], session: date) -> bool:
    snap = schedule.get(session)
    return bool(snap and snap.blocked)
