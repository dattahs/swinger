#!/usr/bin/env python3
"""Run backtest with config overrides (no file edits)."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.backtest.backtester import Backtester
from src.config import load_config_relaxed, UniverseFilters, AdaptiveNewHighLookbackConfig


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--no-new-high", action="store_true")
    args = parser.parse_args()

    cfg = load_config_relaxed(ROOT / "config.yaml")
    if args.no_new_high:
        cfg = cfg.model_copy(
            update={
                "universe_filters": cfg.universe_filters.model_copy(
                    update={
                        "require_new_52wk_high": False,
                        "adaptive_new_high_lookback": cfg.universe_filters.adaptive_new_high_lookback.model_copy(
                            update={"enabled": False}
                        ),
                    }
                )
            }
        )

    bt = Backtester(cfg, repo_root=ROOT)
    cfg.backtest.progress_log.enabled = False
    out = bt.run(start=date.fromisoformat(args.start), end=date.fromisoformat(args.end))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
