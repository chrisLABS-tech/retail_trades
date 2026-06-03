"""Phase 1 signal: the trading signal as a pure function of aligned bars."""

from scalper.signal.definition import (
    fired_at,
    generate_signal,
    lookback_returns,
    red_count,
)

__all__ = ["fired_at", "generate_signal", "lookback_returns", "red_count"]
