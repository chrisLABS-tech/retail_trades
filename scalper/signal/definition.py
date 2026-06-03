"""The trading signal as a pure function of aligned bar data (Phase 1).

The signal: over the trailing ``lookback_minutes``, count how many basket
members are "red" (lookback return below ``red_threshold``). The signal fires
when at least ``min_red_count`` are red.

Everything here is pure (no I/O, no client): it takes an aligned close-price
DataFrame and returns booleans/Series. This is what the backtest evaluates and
what the live monitor will reuse unchanged, guaranteeing the live signal is
identical to the validated one.
"""

from __future__ import annotations

import pandas as pd

from scalper.config import SignalParams


def lookback_returns(
    closes: pd.DataFrame,
    basket: list[str],
    lookback_minutes: int,
) -> pd.DataFrame:
    """Per-bar trailing returns for each basket member over the lookback.

    Args:
        closes: Aligned close prices (columns = symbols), one row per bar.
        basket: Basket symbols to measure (must be columns of ``closes``).
        lookback_minutes: Number of bars in the trailing window.

    Returns:
        DataFrame of returns aligned to ``closes`` index; the first
        ``lookback_minutes`` rows are NaN (insufficient history).
    """
    if lookback_minutes < 1:
        raise ValueError("lookback_minutes must be >= 1")
    missing = [s for s in basket if s not in closes.columns]
    if missing:
        raise KeyError(f"basket symbols missing from closes: {missing}")
    return closes[basket].pct_change(periods=lookback_minutes)


def red_count(
    closes: pd.DataFrame,
    basket: list[str],
    lookback_minutes: int,
    red_threshold: float,
) -> pd.Series:
    """Number of basket members red at each bar (NaN returns count as not red).

    Returns:
        Integer Series aligned to ``closes`` index.
    """
    rets = lookback_returns(closes, basket, lookback_minutes)
    reds = rets.lt(red_threshold)  # NaN -> False, so early bars never fire
    return reds.sum(axis=1).astype(int)


def generate_signal(
    closes: pd.DataFrame,
    basket: list[str],
    params: SignalParams,
) -> pd.Series:
    """Vectorized signal: True at every bar where the signal fires.

    A bar fires when ``>= params.min_red_count`` basket members have a trailing
    ``params.lookback_minutes`` return strictly below ``params.red_threshold``.

    Args:
        closes: Aligned close prices (columns must include all of ``basket``).
        basket: Basket symbols driving the signal.
        params: Signal parameters.

    Returns:
        Boolean Series aligned to ``closes`` index.
    """
    counts = red_count(
        closes, basket, params.lookback_minutes, params.red_threshold
    )
    fired = counts >= params.min_red_count
    fired.name = "signal"
    return fired


def fired_at(
    window: pd.DataFrame,
    basket: list[str],
    params: SignalParams,
) -> bool:
    """Pure scalar evaluation of the signal on a single trailing window.

    Mirrors :func:`generate_signal` for one point in time, which is how the
    live monitor evaluates the most recent bars.

    Args:
        window: Close prices for at least ``lookback_minutes + 1`` trailing
            bars (columns must include ``basket``). Only the first and last
            rows of the window are used to compute the lookback return.
        basket: Basket symbols driving the signal.
        params: Signal parameters.

    Returns:
        True if the signal fires at the end of ``window``.

    Raises:
        ValueError: If the window is too short to cover the lookback.
    """
    needed = params.lookback_minutes + 1
    if len(window) < needed:
        raise ValueError(
            f"window has {len(window)} rows; need >= {needed} for a "
            f"{params.lookback_minutes}-bar lookback"
        )
    missing = [s for s in basket if s not in window.columns]
    if missing:
        raise KeyError(f"basket symbols missing from window: {missing}")

    first = window[basket].iloc[-needed]
    last = window[basket].iloc[-1]
    rets = (last - first) / first
    reds = int((rets < params.red_threshold).sum())
    return reds >= params.min_red_count
