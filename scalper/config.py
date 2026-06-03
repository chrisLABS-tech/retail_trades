"""Static configuration for the scalper: symbols, signal params, risk limits.

This module contains **no secrets**. Credentials live in ``scalper/.env`` and
are loaded via :mod:`scalper.auth.client`. Everything here is plain data so it
can be imported by the backtest and tests without any network or auth.
"""

from __future__ import annotations

import enum
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


# --------------------------------------------------------------------------- #
# Live monitor (Phase 3) — the 15-second statistics & greek sampler.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MonitorParams:
    """Cadence and rolling-window settings for the live monitor.

    Attributes:
        sample_seconds: How often the latest-tick cache is sampled. The user
            asked for a 15-second cadence; this is the single source of truth.
        roll_window: Number of recent 15-second samples kept per symbol to
            compute rolling statistics (trailing return, realized vol).
        max_tick_age_s: A sampled tick older than this is treated as stale and
            its stats/greeks are flagged (do not act on a frozen feed).
    """

    sample_seconds: float = 15.0
    roll_window: int = 20
    max_tick_age_s: float = 30.0


MONITOR = MonitorParams()


# --------------------------------------------------------------------------- #
# Option selection (Phase 4/5) — how the ATM put to trade/track is chosen.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OptionParams:
    """Which contract to pick when buying a put (or tracking a basket greek).

    Attributes:
        contract_type: ``"PUT"`` for the traded leg; the basket tracker uses the
            same near-ATM logic to gauge sensitivity.
        min_dte: Minimum days-to-expiration to consider (avoid same-day gamma
            unless explicitly wanted).
        max_dte: Maximum days-to-expiration (short-dated only).
        moneyness_tolerance: Fraction of spot within which a strike counts as
            "at the money" when ranking candidates (e.g. 0.05 == +/-5%).
    """

    contract_type: str = "PUT"
    min_dte: int = 0
    max_dte: int = 7
    moneyness_tolerance: float = 0.05


OPTION = OptionParams()


# --------------------------------------------------------------------------- #
# Execution (Phase 5) — order construction and the safety ramp.
# --------------------------------------------------------------------------- #
class ExecutionMode(enum.Enum):
    """Safety ramp for order placement. Default is the safest (no orders).

    DRY_RUN: build and log the order, never send it.
    PAPER:   send to a paper/sandbox account (still real API calls).
    LIVE:    send to the live brokerage account (tiny size only).
    """

    DRY_RUN = "dry-run"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class ExecutionParams:
    """Order construction parameters for the put entry/exit.

    Attributes:
        mode: The safety-ramp mode; defaults to ``DRY_RUN`` so nothing is ever
            sent by accident.
        limit_offset_pct: When pricing a marketable limit, how far through the
            mid toward the far touch to place the limit, as a fraction of the
            bid/ask spread (0 == mid, 1 == far touch). Buys cross up, sells
            cross down by this fraction.
        take_profit_pct: Optional take-profit on the option's mark, as a
            positive fraction (e.g. 0.5 == +50%). ``None`` disables it.
        stop_loss_pct: Optional stop-loss on the option's mark, as a positive
            fraction (e.g. 0.5 == -50%). ``None`` disables it.
    """

    mode: ExecutionMode = ExecutionMode.DRY_RUN
    limit_offset_pct: float = 0.5
    take_profit_pct: float | None = None
    stop_loss_pct: float | None = None


EXECUTION = ExecutionParams()


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
    monitor: MonitorParams = MONITOR
    option: OptionParams = OPTION
    execution: ExecutionParams = EXECUTION
    max_spread_pct: float = MAX_SPREAD_PCT


DEFAULT_SETTINGS = Settings()
