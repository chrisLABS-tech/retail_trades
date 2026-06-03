"""Validation statistics: is the signal better than baseline? (Phase 1).

Turns the engine's conditioned vs. baseline forward returns into the metrics
that decide the GO/NO-GO gate:

* hit rate — fraction of fires where the target fell past a (negative) threshold
* mean / median forward move
* the same metrics for the unconditional baseline, for comparison
* a significance test (Welch t-test and a bootstrap) of the conditioned mean
  vs. the baseline mean

Crucially, all moves here are on the **underlying**. Option spread/slippage can
erase a real underlying edge, so :func:`spread_adjusted_edge` exposes a
spread-haircut view and the report flags it loudly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from scalper.backtest.engine import TargetResult


@dataclass
class DistributionStats:
    """Summary stats for one set of forward returns."""

    n: int
    mean: float
    median: float
    std: float
    hit_rate: float  # fraction at or below move_threshold (a downside "hit")
    p25: float
    p75: float


@dataclass
class SignificanceStats:
    """Significance of conditioned mean vs. baseline mean."""

    mean_diff: float  # conditioned mean - baseline mean
    t_stat: Optional[float]
    t_pvalue: Optional[float]
    bootstrap_pvalue: Optional[float]  # P(diff <= 0) under resampling
    bootstrap_ci_low: Optional[float]
    bootstrap_ci_high: Optional[float]


@dataclass
class TargetStats:
    """Full validation stats for one target."""

    target: str
    conditioned: DistributionStats
    baseline: DistributionStats
    significance: SignificanceStats
    move_threshold: float


def _clean(series: pd.Series) -> np.ndarray:
    return series.dropna().to_numpy(dtype=float)


def distribution_stats(
    returns: pd.Series,
    move_threshold: float,
) -> DistributionStats:
    """Compute summary statistics for a set of forward returns.

    Args:
        returns: Forward returns (NaNs ignored).
        move_threshold: A "hit" is a return <= this (negative) value, i.e. the
            target fell at least this much — the direction the puts profit from.
    """
    arr = _clean(returns)
    n = int(arr.size)
    if n == 0:
        return DistributionStats(0, float("nan"), float("nan"), float("nan"),
                                 float("nan"), float("nan"), float("nan"))
    return DistributionStats(
        n=n,
        mean=float(np.mean(arr)),
        median=float(np.median(arr)),
        std=float(np.std(arr, ddof=1)) if n > 1 else 0.0,
        hit_rate=float(np.mean(arr <= move_threshold)),
        p25=float(np.percentile(arr, 25)),
        p75=float(np.percentile(arr, 75)),
    )


def significance(
    conditioned: pd.Series,
    baseline: pd.Series,
    *,
    n_boot: int = 10_000,
    seed: int = 12345,
) -> SignificanceStats:
    """Test whether the conditioned mean differs from the baseline mean.

    Uses Welch's t-test (unequal variance) plus a bootstrap of the difference
    in means. The bootstrap p-value is the one-sided probability that the
    conditioned mean is **not** below the baseline mean (puts want a *lower*,
    i.e. more negative, conditioned forward return).

    Args:
        conditioned: Forward returns on signal fires.
        baseline: Forward returns across all bars.
        n_boot: Bootstrap resamples.
        seed: RNG seed for reproducibility.
    """
    cond = _clean(conditioned)
    base = _clean(baseline)
    if cond.size == 0 or base.size == 0:
        return SignificanceStats(float("nan"), None, None, None, None, None)

    mean_diff = float(np.mean(cond) - np.mean(base))

    t_stat: Optional[float] = None
    t_p: Optional[float] = None
    if cond.size > 1 and base.size > 1:
        res = scipy_stats.ttest_ind(cond, base, equal_var=False)
        t_stat = float(res.statistic)
        t_p = float(res.pvalue)

    rng = np.random.default_rng(seed)
    boot_diffs = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        c = rng.choice(cond, size=cond.size, replace=True)
        b = rng.choice(base, size=base.size, replace=True)
        boot_diffs[i] = c.mean() - b.mean()
    # One-sided: how often the conditioned mean fails to be below baseline.
    boot_p = float(np.mean(boot_diffs >= 0.0))
    ci_low = float(np.percentile(boot_diffs, 2.5))
    ci_high = float(np.percentile(boot_diffs, 97.5))

    return SignificanceStats(
        mean_diff=mean_diff,
        t_stat=t_stat,
        t_pvalue=t_p,
        bootstrap_pvalue=boot_p,
        bootstrap_ci_low=ci_low,
        bootstrap_ci_high=ci_high,
    )


def analyze_target(
    result: TargetResult,
    move_threshold: float,
    *,
    n_boot: int = 10_000,
    seed: int = 12345,
) -> TargetStats:
    """Produce full :class:`TargetStats` from a :class:`TargetResult`."""
    return TargetStats(
        target=result.target,
        conditioned=distribution_stats(result.conditioned, move_threshold),
        baseline=distribution_stats(result.baseline, move_threshold),
        significance=significance(
            result.conditioned, result.baseline, n_boot=n_boot, seed=seed
        ),
        move_threshold=move_threshold,
    )


def spread_adjusted_edge(
    mean_underlying_move: float,
    assumed_spread_pct: float,
) -> float:
    """Underlying mean move minus an assumed round-trip option spread cost.

    This is a *sanity haircut*, not a P&L model: the puts must overcome the
    option bid/ask spread (a fraction of premium, here approximated as a
    fraction applied to the underlying move's favorable case). A positive
    underlying edge that goes non-positive after this haircut is a red flag.

    Args:
        mean_underlying_move: Mean conditioned forward return of the underlying
            (negative is favorable for puts).
        assumed_spread_pct: Round-trip spread as a percent (e.g. 8 for 8%).

    Returns:
        ``|mean move| - spread_fraction``, signed so positive means edge
        survives the haircut. Returns NaN if the input move is NaN.
    """
    if mean_underlying_move != mean_underlying_move:  # NaN
        return float("nan")
    favorable = -mean_underlying_move  # puts profit when underlying falls
    return favorable - (assumed_spread_pct / 100.0)


__all__ = [
    "DistributionStats",
    "SignificanceStats",
    "TargetStats",
    "analyze_target",
    "distribution_stats",
    "significance",
    "spread_adjusted_edge",
]
