"""Tests for the signal definition (pure function of aligned bars)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scalper.config import SignalParams
from scalper.signal.definition import (
    fired_at,
    generate_signal,
    lookback_returns,
    red_count,
)


def _closes(index, **series):
    return pd.DataFrame(series, index=index)


def test_no_fire_on_flat_prices(flat_closes, basket):
    params = SignalParams(lookback_minutes=5, red_threshold=0.0, min_red_count=3)
    sig = generate_signal(flat_closes, basket, params)
    assert sig.dtype == bool
    assert not sig.any()


def test_early_bars_never_fire():
    idx = pd.date_range("2024-01-02 14:30", periods=10, freq="1min", tz="UTC")
    # All declining, but the first `lookback` bars have no return -> no fire.
    falling = np.linspace(100, 95, 10)
    closes = _closes(idx, A=falling, B=falling, C=falling)
    params = SignalParams(lookback_minutes=3, red_threshold=0.0, min_red_count=3)
    sig = generate_signal(closes, ["A", "B", "C"], params)
    assert not sig.iloc[:3].any()
    assert sig.iloc[3:].all()


def test_min_red_count_threshold():
    idx = pd.date_range("2024-01-02 14:30", periods=6, freq="1min", tz="UTC")
    falling = np.linspace(100, 98, 6)
    rising = np.linspace(100, 102, 6)
    closes = _closes(idx, A=falling, B=falling, C=rising)
    # 2 of 3 red. Fires when min_red_count <= 2, not when == 3.
    p2 = SignalParams(lookback_minutes=2, red_threshold=0.0, min_red_count=2)
    p3 = SignalParams(lookback_minutes=2, red_threshold=0.0, min_red_count=3)
    assert generate_signal(closes, ["A", "B", "C"], p2).iloc[2:].all()
    assert not generate_signal(closes, ["A", "B", "C"], p3).any()


def test_red_threshold_requires_strict_negativity():
    idx = pd.date_range("2024-01-02 14:30", periods=6, freq="1min", tz="UTC")
    # ~ -0.1% per 2-bar lookback once moving.
    small_drop = np.array([100, 100, 99.9, 99.8, 99.7, 99.6])
    closes = _closes(idx, A=small_drop, B=small_drop, C=small_drop)
    # Threshold -0.5% should NOT count this as red; 0.0 should.
    loose = SignalParams(lookback_minutes=2, red_threshold=0.0, min_red_count=3)
    strict = SignalParams(lookback_minutes=2, red_threshold=-0.005, min_red_count=3)
    assert generate_signal(closes, ["A", "B", "C"], loose).iloc[2:].any()
    assert not generate_signal(closes, ["A", "B", "C"], strict).any()


def test_red_count_values():
    idx = pd.date_range("2024-01-02 14:30", periods=4, freq="1min", tz="UTC")
    closes = _closes(
        idx,
        A=[100, 99, 98, 97],   # red
        B=[100, 101, 102, 103],  # green
        C=[100, 99.5, 99, 98.5],  # red
    )
    counts = red_count(closes, ["A", "B", "C"], 1, 0.0)
    assert counts.iloc[0] == 0  # NaN return -> not red
    assert counts.iloc[1] == 2


def test_lookback_returns_validation(flat_closes, basket):
    with pytest.raises(ValueError):
        lookback_returns(flat_closes, basket, 0)
    with pytest.raises(KeyError):
        lookback_returns(flat_closes, ["NOPE"], 3)


def test_fired_at_matches_generate_signal():
    idx = pd.date_range("2024-01-02 14:30", periods=12, freq="1min", tz="UTC")
    rng = np.random.default_rng(1)
    closes = _closes(
        idx,
        A=100 * np.exp(np.cumsum(rng.normal(0, 0.002, 12))),
        B=100 * np.exp(np.cumsum(rng.normal(0, 0.002, 12))),
        C=100 * np.exp(np.cumsum(rng.normal(0, 0.002, 12))),
    )
    params = SignalParams(lookback_minutes=4, red_threshold=0.0, min_red_count=2)
    vec = generate_signal(closes, ["A", "B", "C"], params)
    for end in range(params.lookback_minutes, len(closes)):
        window = closes.iloc[: end + 1]
        assert fired_at(window, ["A", "B", "C"], params) == bool(vec.iloc[end])


def test_fired_at_rejects_short_window(flat_closes, basket):
    params = SignalParams(lookback_minutes=5, red_threshold=0.0, min_red_count=3)
    with pytest.raises(ValueError):
        fired_at(flat_closes.iloc[:3], basket, params)
