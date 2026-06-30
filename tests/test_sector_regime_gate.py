"""Tests for sector regime gate evaluation."""

from src.config import SectorRegimeGateConfig
from src.engine.sector_regime_gate import evaluate_gate_from_summary


def test_gate_blocks_ranging_low_exposure():
    cfg = SectorRegimeGateConfig(enabled=True)
    summary = {
        "dominant_regime": "RANGING",
        "regime_dispersion": "LOW",
        "recommended_overall_exposure": 0.25,
    }
    blocked, reason = evaluate_gate_from_summary(summary, cfg)
    assert blocked is True
    assert "RANGING" in reason


def test_gate_disabled_passes_through():
    cfg = SectorRegimeGateConfig(enabled=False)
    summary = {
        "dominant_regime": "RANGING",
        "regime_dispersion": "LOW",
        "recommended_overall_exposure": 0.10,
    }
    blocked, _ = evaluate_gate_from_summary(summary, cfg)
    assert blocked is False


def test_gate_allows_high_exposure_ranging():
    cfg = SectorRegimeGateConfig(enabled=True, max_recommended_exposure=0.30)
    summary = {
        "dominant_regime": "RANGING",
        "regime_dispersion": "LOW",
        "recommended_overall_exposure": 0.35,
    }
    blocked, _ = evaluate_gate_from_summary(summary, cfg)
    assert blocked is False


def test_gate_allows_strong_trend():
    cfg = SectorRegimeGateConfig(enabled=True)
    summary = {
        "dominant_regime": "STRONG_TREND_UP",
        "regime_dispersion": "HIGH",
        "recommended_overall_exposure": 0.80,
    }
    blocked, _ = evaluate_gate_from_summary(summary, cfg)
    assert blocked is False
