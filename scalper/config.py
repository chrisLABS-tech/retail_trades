"""Static configuration for the scalper: symbols, signal params, risk limits.

This module contains **no secrets**. Credentials live in ``scalper/.env`` and
are loaded via :mod:`scalper.auth.client`. Everything here is plain data so it
can be imported by the backtest and tests without any network or auth.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --------------------------------------------------------------------------- #
# Symbols
# --------------------------------------------------------------------------- #
# The proxy "basket": broadly-liquid tech proxies whose collective weakness is
# the signal input. When enough of these are red, the signal fires.
BASKET: list[str] = ["VT", "VGT", "MRVL", "MU", "SNDSK"]

# Names actually traded (we buy short-dated puts on these). During Phase 1 we
# only validate forward moves on the *underlying* as a proxy for the option.
TARGETS: list[str] = ["INFQ", "P"]


# --------------------------------------------------------------------------- #
# Signal parameters
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SignalParams:
    """Parameters defining when the signal fires.

    Attributes:
        lookback_minutes: Window (in bars/minutes) over which "red" is measured.
        red_threshold: How negative a proxy's lookback return must be to count
            as red. ``0.0`` means "any decline"; ``-0.001`` means "down > 0.1%".
        min_red_count: How many basket members must be red for the signal to
            fire. Defaults to the whole basket (strictest).
        hold_minutes: Forward window for the target's measured move; also the
            live time-stop in later phases.
    """

    lookback_minutes: int = 5
    red_threshold: float = 0.0
    min_red_count: int = len(BASKET)
    hold_minutes: int = 10


# Default signal configuration. ``min_red_count`` defaults to the full basket.
SIGNAL = SignalParams()


# --------------------------------------------------------------------------- #
# Liquidity (used by the Phase 4 screener; surfaced here so the Phase 1 report
# can flag the spread caveat against a concrete number).
# --------------------------------------------------------------------------- #
# Skip a setup if the option bid/ask spread exceeds this percent of mid.
MAX_SPREAD_PCT: float = 8.0


# --------------------------------------------------------------------------- #
# Risk limits (enforced in Phase 5; defined here so there is a single source
# of truth and nothing is hard-coded later).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RiskLimits:
    max_contracts: int = 1
    max_trades_per_day: int = 3  # mind the PDT rule if on margin and < $25k
    daily_loss_limit: float = 250.0  # USD


RISK = RiskLimits()


# --------------------------------------------------------------------------- #
# Data / backtest defaults
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DataParams:
    """Defaults for history pulls and the validation report.

    Attributes:
        bar_minutes: Intraday bar size in minutes (Schwab supports 1/5/...).
        history_days: How many days of intraday history to request.
        move_threshold: For the hit-rate metric, a "hit" is a forward move at
            or below this (negative) return — i.e. the target fell at least
            this much. Defaults to ``-0.002`` (-0.2%).
        assumed_spread_pct: Round-trip option spread cost assumed when showing
            a spread-adjusted view of the underlying edge. This is a ceiling
            reminder, NOT a claim of net option P&L.
    """

    bar_minutes: int = 1
    history_days: int = 10
    move_threshold: float = -0.002
    assumed_spread_pct: float = MAX_SPREAD_PCT


DATA = DataParams()


# All symbols we need bars for (basket drives the signal, targets are measured).
def all_symbols() -> list[str]:
    """Return the de-duplicated union of basket and target symbols."""
    seen: dict[str, None] = {}
    for sym in (*BASKET, *TARGETS):
        seen.setdefault(sym, None)
    return list(seen)


@dataclass(frozen=True)
class Settings:
    """Convenience bundle of all config, handy to pass around or override."""

    basket: list[str] = field(default_factory=lambda: list(BASKET))
    targets: list[str] = field(default_factory=lambda: list(TARGETS))
    signal: SignalParams = SIGNAL
    risk: RiskLimits = RISK
    data: DataParams = DATA
    max_spread_pct: float = MAX_SPREAD_PCT


DEFAULT_SETTINGS = Settings()
