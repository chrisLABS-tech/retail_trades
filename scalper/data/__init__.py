"""Phase 0 data: intraday history, quote-freshness checks, and local storage."""

from scalper.data.history import (
    align_closes,
    candles_to_frame,
    confirm_realtime,
    fetch_aligned_closes,
    fetch_intraday,
    is_realtime,
    price_history_to_frame,
    quote_age_seconds,
)

__all__ = [
    "align_closes",
    "candles_to_frame",
    "confirm_realtime",
    "fetch_aligned_closes",
    "fetch_intraday",
    "is_realtime",
    "price_history_to_frame",
    "quote_age_seconds",
]
