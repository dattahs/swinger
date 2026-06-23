from datetime import date
from pathlib import Path

from src.backtest.backtester import Backtester
from src.config import load_config_relaxed
from src.data.seed import seed_demo_data

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"


def test_smoke_backtest_runs(tmp_path: Path):
    cfg = load_config_relaxed(CONFIG)
    db = tmp_path / "data.db"
    cfg.backtest.data_db_path = str(db)
    cfg.backtest.export_directory = str(tmp_path / "out")
    seed_demo_data(db)
    bt = Backtester(cfg)
    out = bt.run(start=date(2018, 1, 1), end=date(2018, 2, 28))
    assert (out / "decision_log.csv").exists()
    assert (out / "equity_curve.csv").exists()
    assert (out / "summary_report.json").exists()
