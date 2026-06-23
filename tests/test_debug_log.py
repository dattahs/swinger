"""Tests for progress and action debug logging."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from src.backtest.backtester import make_run_output_dir
from src.config import AppConfig, BacktestConfig, DebugLogConfig, ProgressLogConfig, SystemConfig
from src.debug_log import ActionDebugLogger, ProgressLogger, humanize_filter_reason


def test_humanize_filter_reason() -> None:
    assert "volume" in humanize_filter_reason("VOLUME_TOO_LOW").lower()
    assert "15" in humanize_filter_reason("ROE<15.0")


def test_progress_logger_writes_and_flushes(tmp_path: Path) -> None:
    log_file = tmp_path / "progress.log"
    logger = ProgressLogger(ProgressLogConfig(enabled=True, log_to_console=False, log_file=str(log_file)))
    logger.open(log_file)
    logger.session(date(2025, 1, 2), 1, 10, 500_000.0, 2, universe_size=400)
    logger.close()
    text = log_file.read_text(encoding="utf-8")
    assert "Day 1/10" in text
    assert "universe=400" in text


def test_action_debug_logger_csv(tmp_path: Path) -> None:
    log_file = tmp_path / "action_debug.csv"
    logger = ActionDebugLogger(DebugLogConfig(enabled=True, log_to_console=False, log_file=str(log_file)))
    logger.open(log_file)
    logger.consider_breakout(date(2025, 1, 2), "INFY", 1800.0, 1700.0)
    logger.reject(date(2025, 1, 2), "INFY", "FILTER", "VOLUME_TOO_LOW")
    logger.close()
    text = log_file.read_text(encoding="utf-8")
    assert "INFY" in text
    assert "CONSIDER" in text
    assert "REJECT" in text


def test_make_run_output_dir_timestamped(tmp_path: Path) -> None:
    cfg = AppConfig.model_construct(
        system=SystemConfig(),
        backtest=BacktestConfig(
            start_date=date(2025, 1, 1),
            end_date=date(2025, 3, 31),
            price_warmup_start_date=date(2016, 9, 1),
            export_directory=str(tmp_path / "backtest_outputs"),
            timestamped_runs=True,
        ),
    )
    out1 = make_run_output_dir(cfg, tmp_path)
    out2 = make_run_output_dir(cfg, tmp_path)
    assert out1.name.startswith("run_")
    assert out1 != out2
    assert out1.parent == tmp_path / "backtest_outputs"
