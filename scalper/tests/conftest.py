"""Shared pytest fixtures: synthetic aligned bars for the signal/backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def basket() -> list[str]:
    return ["A", "B", "C"]


@pytest.fixture
def targets() -> list[str]:
    return ["T"]


@pytest.fixture
def minute_index() -> pd.DatetimeIndex:
    # Two trading sessions so same-session masking can be exercised.
    day1 = pd.date_range("2024-01-02 14:30", periods=30, freq="1min", tz="UTC")
    day2 = pd.date_range("2024-01-03 14:30", periods=30, freq="1min", tz="UTC")
    return day1.append(day2)


@pytest.fixture
def flat_closes(minute_index, basket, targets) -> pd.DataFrame:
    """Perfectly flat prices: no symbol is ever red."""
    cols = basket + targets
    data = {c: np.full(len(minute_index), 100.0) for c in cols}
    return pd.DataFrame(data, index=minute_index)
