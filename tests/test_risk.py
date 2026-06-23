from src.engine.risk import compute_entry_prices, size_position
from tests.test_darvas import _minimal_config


def test_gtt_trigger_offset():
    cfg = _minimal_config()
    entry, trigger, stop, target = compute_entry_prices(110, 100, cfg)
    assert entry == 110
    assert trigger == 110.05


def test_insufficient_cash_rejects():
    cfg = _minimal_config()
    qty = size_position(110, 100, 500_000, 1000, cfg)
    assert qty is None
