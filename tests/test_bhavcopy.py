"""Tests for NSE bhavcopy parsing."""

from __future__ import annotations

from datetime import date
from io import StringIO

import pandas as pd
import pytest

from src.data.bhavcopy import _parse_bhavcopy_df


def test_parse_sec_bhavdata_full_columns():
    raw = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS
RELIANCE, EQ, 08-Jan-2025, 1200.0, 1210.0, 1225.0, 1205.0, 1220.0, 1220.0, 1215.0, 2500000, 30500.50
ABC, BE, 08-Jan-2025, 10.0, 10.5, 11.0, 10.0, 10.8, 10.8, 10.5, 1000, 1.05
"""
    df = _parse_bhavcopy_df(pd.read_csv(StringIO(raw)))
    eq = df[df["symbol"] == "RELIANCE"].iloc[0]
    assert eq["close"] == 1220.0
    assert eq["volume"] == 2_500_000
    assert eq["date"] == date(2025, 1, 8)
    assert eq["turnover_inr"] == pytest.approx(3_050_050_000.0)
    assert df["symbol"].tolist() == ["RELIANCE"]
