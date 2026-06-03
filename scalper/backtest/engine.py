"""Backtest engine: forward returns at every signal fire (Phase 1).

For each target, measure the forward return over ``hold_minutes`` at every bar,
then split those into the **signal-conditioned** set (bars where the signal
fired) versus the **unconditional baseline** (all bars). :mod:`scalper.backtest.stats`
turns these into the validation metrics.

Pure and offline: operates entirely on an aligned close-price DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from scalper.config import SignalParams
from scalper.signal.definition import generate_signal


def forward_returns(
    closes: pd.DataFrame,
    target: str,
    hold_minutes: int,
    *,
    same_session: bool = True,
) -> pd.Series:
    """Forward return of ``target`` over ``hold_minutes`` at each bar.

    ``r_t = close[t + hold] / close[t] - 1``.

    Args:
        closes: Aligned close prices (must contain ``target``).
        target: Symbol to measure.
        hold_minutes: Forward window in bars.
        same_session: If True, NaN-out any forward return whose end bar falls on
            a different UTC date than the entry bar, so the time-stop never
            "holds" across an overnight gap (which the live strategy cannot do).

    Returns:
        Float Series aligned to ``closes`` index; the last ``hold_minutes`` rows
        (and any cross-session bars) are NaN.
    """
    if hold_minutes < 1:
        raise ValueError("hold_minutes must be >= 1")
    if target not in closes.columns:
        raise KeyError(f"target {target!r} not in closes columns")

    price = closes[target]
    fwd = price.shift(-hold_minutes) / price - 1.0

    if same_session and isinstance(closes.index, pd.DatetimeIndex):
        entry_date = closes.index.normalize()
        exit_date = closes.index.to_series().shift(-hold_minutes).dt.normalize()
        cross = entry_date.values != exit_date.values
        fwd = fwd.mask(pd.Series(cross, index=closes.index))

    fwd.name = f"{target}_fwd_{hold_minutes}m"
    return fwd


@dataclass
class TargetResult:
    """Backtest outcome for a single target.

    Attributes:
        target: The traded symbol.
        forward: Forward returns at every bar (baseline universe), NaN-free.
        signal_mask: Boolean Series, True where the signal fired (aligned to
            ``forward`` after dropping NaNs).
        conditioned: Forward returns on bars where the signal fired.
        baseline: Forward returns across all bars (== ``forward``).
        n_fires: Number of signal fires with a valid forward return.
    """

    target: str
    forward: pd.Series
    signal_mask: pd.Series
    conditioned: pd.Series
    baseline: pd.Series

    @property
    def n_fires(self) -> int:
        return int(len(self.conditioned))


def run_backtest(
    closes: pd.DataFrame,
    basket: list[str],
    targets: list[str],
    params: SignalParams,
    *,
    same_session: bool = True,
) -> dict[str, TargetResult]:
    """Run the Phase 1 backtest for every target.

    Args:
        closes: Aligned close prices (columns must include basket + targets).
        basket: Basket symbols driving the signal.
        targets: Symbols whose forward moves are measured.
        params: Signal parameters (lookback, threshold, count, hold).
        same_session: Forwarded to :func:`forward_returns`.

    Returns:
        Mapping ``target -> TargetResult``.
    """
    signal = generate_signal(closes, basket, params)

    results: dict[str, TargetResult] = {}
    for target in targets:
        fwd = forward_returns(
            closes, target, params.hold_minutes, same_session=same_session
        )
        valid = fwd.notna()
        fwd_valid = fwd[valid]
        sig_valid = signal[valid].astype(bool)
        conditioned = fwd_valid[sig_valid]
        results[target] = TargetResult(
            target=target,
            forward=fwd_valid,
            signal_mask=sig_valid,
            conditioned=conditioned,
            baseline=fwd_valid,
        )
    return results


def signal_fire_count(
    closes: pd.DataFrame,
    basket: list[str],
    params: SignalParams,
) -> int:
    """Convenience: total number of signal fires over the history."""
    return int(generate_signal(closes, basket, params).sum())


__all__ = [
    "TargetResult",
    "forward_returns",
    "run_backtest",
    "signal_fire_count",
]
