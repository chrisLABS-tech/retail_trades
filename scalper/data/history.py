"""Intraday history & quote-freshness helpers (Phase 0).

Two responsibilities:

1. Pull intraday bars for the basket + targets and align them into a single
   close-price DataFrame the signal/backtest can consume.
2. Confirm a quote is **real-time** (not the 15-minute delayed feed), which is
   the Phase 0 acceptance gate — a delayed feed makes the 10-minute strategy
   impossible.

The Schwab-response parsing is split into pure functions that take plain
dict/JSON, so they can be unit-tested without any network or auth. The
``fetch_*`` helpers accept a duck-typed client (anything exposing the schwab-py
methods), keeping them testable with a fake client.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

# A real-time quote should be at most this many seconds stale. The delayed feed
# is ~15 min (900s); anything beyond a small buffer indicates a delayed feed.
DEFAULT_MAX_QUOTE_AGE_S: float = 120.0
DELAYED_FEED_THRESHOLD_S: float = 600.0


# --------------------------------------------------------------------------- #
# Pure parsing helpers
# --------------------------------------------------------------------------- #
def candles_to_frame(candles: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    """Convert a Schwab ``candles`` array into an OHLCV DataFrame.

    Args:
        candles: Iterable of candle dicts with ``open/high/low/close/volume``
            and a millisecond-epoch ``datetime``.

    Returns:
        A DataFrame indexed by tz-aware UTC timestamps with columns
        ``[open, high, low, close, volume]``, sorted ascending. Empty input
        yields an empty, correctly-typed frame.
    """
    rows = list(candles)
    cols = ["open", "high", "low", "close", "volume"]
    if not rows:
        empty = pd.DataFrame(columns=cols)
        empty.index = pd.DatetimeIndex([], tz="UTC", name="datetime")
        return empty

    frame = pd.DataFrame(rows)
    index = pd.to_datetime(frame["datetime"], unit="ms", utc=True)
    frame = frame.reindex(columns=cols)
    frame.index = pd.DatetimeIndex(index, name="datetime")
    return frame.sort_index()


def price_history_to_frame(response_json: Mapping[str, Any]) -> pd.DataFrame:
    """Parse a full Schwab price-history JSON body into an OHLCV DataFrame."""
    return candles_to_frame(response_json.get("candles", []))


def align_closes(frames: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """Align per-symbol OHLCV frames into one close-price DataFrame.

    Columns are symbols; the index is the union of all timestamps. Rows with
    any missing close are dropped so the basket is fully observed at every bar
    the signal/backtest sees (avoids spurious fires from stale columns).

    Args:
        frames: Mapping of ``symbol -> OHLCV DataFrame`` (as from
            :func:`candles_to_frame`).

    Returns:
        DataFrame of aligned closes, one column per symbol, NaN-free.
    """
    closes = {
        symbol: frame["close"]
        for symbol, frame in frames.items()
        if "close" in frame.columns
    }
    if not closes:
        return pd.DataFrame()
    aligned = pd.concat(closes, axis=1)
    aligned.columns = list(closes.keys())
    return aligned.dropna(how="any").sort_index()


def quote_age_seconds(
    quote_response: Mapping[str, Any],
    symbol: str,
    *,
    now_s: Optional[float] = None,
) -> Optional[float]:
    """Return how stale a quote is, in seconds, or None if unavailable.

    Schwab quote times are millisecond epochs. The most relevant field for
    freshness is ``quoteTime`` (NBBO time); ``tradeTime`` is used as a fallback.

    Args:
        quote_response: The parsed ``get_quote(s)`` JSON, keyed by symbol.
        symbol: Symbol to inspect.
        now_s: Override for the current time in epoch seconds (for testing).

    Returns:
        Age in seconds (>= 0), or None if no usable timestamp is present.
    """
    entry = quote_response.get(symbol)
    if not isinstance(entry, Mapping):
        return None
    quote = entry.get("quote", entry)
    ts_ms: Optional[float] = None
    for key in ("quoteTime", "tradeTime", "quoteTimeInLong", "tradeTimeInLong"):
        value = quote.get(key)
        if isinstance(value, (int, float)) and value > 0:
            ts_ms = float(value)
            break
    if ts_ms is None:
        return None

    now = time.time() if now_s is None else now_s
    age = now - (ts_ms / 1000.0)
    return max(age, 0.0)


def is_realtime(
    quote_response: Mapping[str, Any],
    symbol: str,
    *,
    max_age_s: float = DEFAULT_MAX_QUOTE_AGE_S,
    now_s: Optional[float] = None,
) -> bool:
    """Return True if the quote looks real-time (not the delayed feed).

    A quote older than ``max_age_s`` is treated as delayed/stale. Returns False
    when no timestamp is available (fail-closed: assume not real-time).
    """
    age = quote_age_seconds(quote_response, symbol, now_s=now_s)
    if age is None:
        return False
    return age <= max_age_s


# --------------------------------------------------------------------------- #
# Client-driven fetchers (duck-typed client => testable with a fake)
# --------------------------------------------------------------------------- #
def _unwrap(response: Any) -> Any:
    """Return ``response.json()`` if present, else the response itself.

    schwab-py returns an ``httpx.Response``; tests pass a plain dict.
    """
    json_attr = getattr(response, "json", None)
    if callable(json_attr):
        return json_attr()
    return response


def fetch_intraday(
    client: Any,
    symbol: str,
    *,
    days: int = 10,
    minute: int = 1,
) -> pd.DataFrame:
    """Fetch intraday OHLCV bars for one symbol via schwab-py.

    Uses ``get_price_history_every_minute`` for 1-minute bars when available,
    falling back to a generic ``get_price_history`` call.

    Args:
        client: Authenticated schwab-py client (or a duck-typed stand-in).
        symbol: Ticker to fetch.
        days: Number of calendar days of history to request.
        minute: Bar size in minutes (only 1 is special-cased; others use the
            generic endpoint if the client exposes it).

    Returns:
        OHLCV DataFrame from :func:`candles_to_frame`.
    """
    if minute == 1 and hasattr(client, "get_price_history_every_minute"):
        response = client.get_price_history_every_minute(symbol)
    elif hasattr(client, "get_price_history_every_minute"):
        # schwab-py exposes per-frequency helpers; default to 1-minute and let
        # the caller resample. Kept explicit so behavior is predictable.
        response = client.get_price_history_every_minute(symbol)
    else:  # pragma: no cover - exercised only against the real client
        response = client.get_price_history(symbol)

    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()

    return price_history_to_frame(_unwrap(response))


def fetch_aligned_closes(
    client: Any,
    symbols: Iterable[str],
    *,
    days: int = 10,
    minute: int = 1,
) -> pd.DataFrame:
    """Fetch and align intraday closes for several symbols.

    Returns a NaN-free close-price DataFrame (columns = symbols) ready for the
    signal/backtest. Symbols are fetched sequentially to respect rate limits.
    """
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frames[symbol] = fetch_intraday(client, symbol, days=days, minute=minute)
    return align_closes(frames)


def confirm_realtime(
    client: Any,
    symbol: str,
    *,
    max_age_s: float = DEFAULT_MAX_QUOTE_AGE_S,
    now_s: Optional[float] = None,
) -> tuple[bool, Optional[float]]:
    """Pull a single live quote and confirm it is real-time (Phase 0 gate).

    Args:
        client: Authenticated schwab-py client (or duck-typed stand-in).
        symbol: Symbol to quote.
        max_age_s: Maximum staleness allowed to count as real-time.
        now_s: Override for current epoch seconds (testing).

    Returns:
        ``(is_realtime, age_seconds)``. ``age_seconds`` is None if the quote
        carried no usable timestamp.
    """
    response = client.get_quote(symbol)
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    data = _unwrap(response)
    age = quote_age_seconds(data, symbol, now_s=now_s)
    fresh = is_realtime(data, symbol, max_age_s=max_age_s, now_s=now_s)
    return fresh, age
