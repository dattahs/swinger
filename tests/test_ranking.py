from datetime import date

from src.engine.ranking import select_candidates
from src.models import BreakoutCandidate, MarketContext, OpenPosition, SkipReason
from tests.test_darvas import _minimal_config


def test_sector_cap_blocks_second():
    cfg = _minimal_config()
    ctx = MarketContext(
        target_date=date(2018, 1, 1),
        account_equity=500_000,
        settled_cash_inr=500_000,
        open_positions=[
            OpenPosition(
                symbol="A",
                quantity=900,
                entry_price=100,
                current_stop_loss=90,
                current_target=120,
                sector="TECH",
            )
        ],
    )
    candidates = [
        BreakoutCandidate(
            symbol="B",
            box_top=110,
            box_bottom=100,
            entry_price=110,
            trigger_price=110.05,
            stop_loss_price=99.95,
            target_price=120,
            structural_rr=5.0,
            sector="TECH",
            quantity=900,
        ),
        BreakoutCandidate(
            symbol="C",
            box_top=110,
            box_bottom=100,
            entry_price=110,
            trigger_price=110.05,
            stop_loss_price=99.95,
            target_price=120,
            structural_rr=4.0,
            sector="TECH",
            quantity=900,
        ),
    ]
    actions, skips = select_candidates(candidates, ctx, cfg, {"A": 100, "B": 110, "C": 110}, set())
    assert len(actions) <= 1
    if len(actions) == 1:
        assert skips.get("C") == SkipReason.SECTOR_CAP or skips.get("B") == SkipReason.SECTOR_CAP
