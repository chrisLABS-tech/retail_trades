"""Tests for validation statistics and the spread-haircut check."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from scalper.backtest.stats import (
    distribution_stats,
    significance,
    spread_adjusted_edge,
)


def test_distribution_stats_basic():
    s = pd.Series([-0.01, -0.005, 0.0, 0.005, 0.01])
    stats = distribution_stats(s, move_threshold=-0.002)
    assert stats.n == 5
    assert stats.mean == pytest.approx(0.0, abs=1e-12)
    assert stats.median == 0.0
    # two values <= -0.002
    assert stats.hit_rate == 2 / 5


def test_distribution_stats_empty():
    stats = distribution_stats(pd.Series([], dtype=float), move_threshold=-0.002)
    assert stats.n == 0
    assert np.isnan(stats.mean)


def test_significance_detects_real_difference():
    rng = np.random.default_rng(0)
    baseline = pd.Series(rng.normal(0.0, 0.01, 1000))
    conditioned = pd.Series(rng.normal(-0.01, 0.01, 200))  # clearly lower mean
    sig = significance(conditioned, baseline, n_boot=2000, seed=1)
    assert sig.mean_diff < 0
    assert sig.t_pvalue is not None and sig.t_pvalue < 0.01
    # Conditioned mean is reliably below baseline -> bootstrap p near 0.
    assert sig.bootstrap_pvalue is not None and sig.bootstrap_pvalue < 0.05


def test_significance_no_difference():
    rng = np.random.default_rng(2)
    baseline = pd.Series(rng.normal(0.0, 0.01, 1000))
    conditioned = pd.Series(rng.normal(0.0, 0.01, 200))
    sig = significance(conditioned, baseline, n_boot=2000, seed=3)
    assert sig.t_pvalue is not None and sig.t_pvalue > 0.05


def test_significance_empty_inputs():
    sig = significance(pd.Series([], dtype=float), pd.Series([1.0]))
    assert np.isnan(sig.mean_diff)
    assert sig.t_pvalue is None
    assert sig.bootstrap_pvalue is None


def test_spread_adjusted_edge_sign():
    # A 0.5% favorable underlying move does NOT survive an 8% spread.
    assert spread_adjusted_edge(-0.005, 8.0) < 0
    # A 10% favorable move does survive an 8% spread.
    assert spread_adjusted_edge(-0.10, 8.0) > 0
    # NaN in -> NaN out
    assert np.isnan(spread_adjusted_edge(float("nan"), 8.0))
