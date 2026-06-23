"""Tests for backtest results email notification."""

from __future__ import annotations

import json
import zipfile
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.notify.backtest_email import (
    BacktestEmailSettings,
    BacktestRunIncompleteError,
    REQUIRED_SUMMARY,
    build_insights,
    create_run_zip,
    resolve_run_directory,
    send_backtest_results_email,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RUN = ROOT / "backtest_outputs" / "run_20260623_101314"


@pytest.fixture
def minimal_run(tmp_path: Path) -> Path:
    run = tmp_path / "run_test"
    run.mkdir()
    summary = {
        "initial_capital_inr": 500_000.0,
        "final_equity_inr": 550_000.0,
        "cagr": 0.10,
        "max_drawdown_pct": 3.5,
        "total_closed_trades": 2,
        "win_rate": 0.5,
        "start_date": "2024-01-01",
        "end_date": "2024-03-31",
        "invoked_at": "2026-06-23T12:00:00",
    }
    (run / "summary_report.json").write_text(json.dumps(summary), encoding="utf-8")
    (run / "run_manifest.json").write_text(json.dumps({"invoked_at": summary["invoked_at"]}))
    (run / "closed_trades.csv").write_text(
        "trade_id,symbol,entry_date,exit_date,entry_price,exit_price,quantity,exit_reason,pnl\n"
        "a,AAA,2024-01-10,2024-01-20,100,110,10,TARGET_HIT,100\n"
        "b,BBB,2024-02-01,2024-02-15,200,180,5,STOP_LOSS_HIT,-100\n"
    )
    (run / "equity_curve.csv").write_text(
        "date,equity,drawdown_pct,open_positions_count\n"
        "2024-01-31,505000,0.5,1\n"
        "2024-02-29,510000,1.0,0\n"
        "2024-03-31,550000,0.2,0\n"
    )
    (run / "decision_log.csv").write_text(
        "date,symbol,box_state,filter_pass,selected,skip_reason\n"
        "2024-01-01,AAA,SCANNING,1,0,\n"
        "2024-01-02,AAA,BREAKOUT,1,1,\n"
        "2024-01-01,BBB,FORMING,1,0,\n"
    )
    return run


@pytest.mark.skipif(not SAMPLE_RUN.is_dir(), reason="sample backtest output not present")
def test_resolve_latest_run():
    run = resolve_run_directory(export_directory=ROOT / "backtest_outputs", latest=True)
    assert run.name.startswith("run_")


def test_resolve_latest_skips_incomplete_runs(tmp_path: Path):
    incomplete = tmp_path / "run_newer"
    complete = tmp_path / "run_older"
    incomplete.mkdir()
    complete.mkdir()
    (complete / REQUIRED_SUMMARY).write_text("{}", encoding="utf-8")
    incomplete.touch()  # newer mtime
    import os
    import time

    os.utime(incomplete, (time.time() + 10, time.time() + 10))
    resolved = resolve_run_directory(export_directory=tmp_path, latest=True)
    assert resolved == complete.resolve()


def test_resolve_run_directory_explicit(minimal_run: Path):
    resolved = resolve_run_directory(minimal_run)
    assert resolved == minimal_run.resolve()


def test_build_insights(minimal_run: Path):
    plain, html = build_insights(minimal_run)
    assert "Swinger backtest results" in plain
    assert "CAGR" in plain
    assert "Decision funnel" in plain
    assert "BREAKOUT" in plain
    assert "<pre" in html


def test_build_insights_requires_summary(tmp_path: Path):
    run = tmp_path / "run_empty"
    run.mkdir()
    with pytest.raises(BacktestRunIncompleteError):
        build_insights(run)


def test_create_run_zip(minimal_run: Path, tmp_path: Path):
    zip_path = create_run_zip(minimal_run, tmp_path / "bundle.zip")
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "summary_report.json" in names
    assert "closed_trades.csv" in names
    assert "decision_log.csv" in names


def test_send_backtest_results_email_dry_run(minimal_run: Path):
    settings = BacktestEmailSettings(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_password="pass",
        from_addr="user@example.com",
        to_addrs=("ops@example.com",),
    )
    with patch("src.notify.backtest_email.smtplib.SMTP") as smtp_cls:
        smtp = MagicMock()
        smtp_cls.return_value.__enter__.return_value = smtp
        send_backtest_results_email(minimal_run, settings, dry_run=False, keep_zip=False)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("user", "pass")
        sent = smtp.send_message.call_args[0][0]
        assert sent["Subject"].startswith("Swinger Backtest")
        assert sent.get_payload()  # multipart


@pytest.mark.skipif(not SAMPLE_RUN.is_dir(), reason="sample backtest output not present")
def test_insights_on_real_run():
    plain, _ = build_insights(SAMPLE_RUN)
    assert "run_20260623_101314" in plain
    assert "Performance" in plain
