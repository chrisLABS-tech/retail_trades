"""Tests for the backtest engine: forward returns and conditioning."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scalper.backtest.engine import (
    forward_returns,
    run_backtest,
    signal_fire_count,
)
from scalper.config import SignalParams


def _closes(index, **series):
    return pd.DataFrame(series, index=index)


def test_forward_returns_basic_math():
    idx = pd.date_range("2024-01-02 14:30", periods=5, freq="1min", tz="UTC")
    closes = _closes(idx, T=[100, 110, 121, 90, 80])
    fwd = forward_returns(closes, "T", 1, same_session=False)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert fwd.iloc[1] == pytest.approx(0.10)
    assert np.isnan(fwd.iloc[-1])  # no bar hold_minutes ahead


def test_forward_returns_same_session_masks_overnight():
    day1 = pd.date_range("2024-01-02 15:58", periods=2, freq="1min", tz="UTC")
    day2 = pd.date_range("2024-01-03 14:30", periods=2, freq="1min", tz="UTC")
    idx = day1.append(day2)
    closes = _closes(idx, T=[100, 101, 102, 103])
    # hold=1: the last bar of day1 (idx=1) would look into day2 -> masked.
    fwd = forward_returns(closes, "T", 1, same_session=True)
    assert not np.isnan(fwd.iloc[0])  # within day1
    assert np.isnan(fwd.iloc[1])      # crosses into day2 -> masked
    fwd_nomask = forward_returns(closes, "T", 1, same_session=False)
    assert not np.isnan(fwd_nomask.iloc[1])


def test_run_backtest_conditioning_counts():
    idx = pd.date_range("2024-01-02 14:30", periods=20, freq="1min", tz="UTC")
    # Basket all steadily falling => signal fires after lookback warms up.
    falling = np.linspace(100, 90, 20)
    target = np.linspace(50, 45, 20)
    closes = _closes(idx, A=falling, B=falling, C=falling, T=target)
    params = SignalParams(
        lookback_minutes=3, red_threshold=0.0, min_red_count=3, hold_minutes=2
    )
    results = run_backtest(closes, ["A", "B", "C"], ["T"], params)
    res = results["T"]
    # conditioned is a subset of baseline
    assert res.n_fires <= len(res.baseline)
    assert set(res.conditioned.index).issubset(set(res.baseline.index))
    # Falling target => conditioned forward returns are negative.
    assert res.conditioned.mean() < 0


def test_signal_fire_count_matches_mask():
    idx = pd.date_range("2024-01-02 14:30", periods=15, freq="1min", tz="UTC")
    falling = np.linspace(100, 95, 15)
    closes = _closes(idx, A=falling, B=falling, C=falling, T=falling)
    params = SignalParams(lookback_minutes=2, red_threshold=0.0, min_red_count=3)
    count = signal_fire_count(closes, ["A", "B", "C"], params)
    assert count == 13  # 15 bars - 2 warmup bars
