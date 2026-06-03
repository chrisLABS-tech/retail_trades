"""Real-time streaming connectivity & a latest-tick cache (Phase 3).

Two responsibilities, split so the data structure is pure and the I/O is thin:

1. :class:`TickCache` — a pure, in-memory "latest value per symbol" store fed by
   parsed stream messages. No network, no schwab dependency, fully unit-testable.
2. :class:`StreamManager` — a thin wrapper over ``schwab-py``'s ``StreamClient``
   that subscribes to LEVELONE_EQUITIES (basket + target underlyings) and
   LEVELONE_OPTIONS (the chosen near-ATM contracts, which carry live greeks),
   routes every message into a :class:`TickCache`, and reuses the Phase-0
   freshness discipline so we never act on a stale/delayed feed.

Schwab streams content with **numeric** field keys (e.g. ``"3"`` for an
equity's last price). The field-number maps below mirror ``schwab-py``'s
``StreamClient.LevelOneEquityFields`` / ``LevelOneOptionFields`` enums so the
parser produces friendly, named ticks without importing schwab (keeping this
module importable in tests). ``schwab-py`` is imported lazily inside
:class:`StreamManager` only.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, MutableMapping, Optional

# --------------------------------------------------------------------------- #
# Field-number -> name maps (mirror schwab-py field enums; values are stable
# Schwab API field indices). Only the fields the monitor needs are mapped.
# --------------------------------------------------------------------------- #
EQUITY_FIELDS: dict[str, str] = {
    "1": "bid_price",
    "2": "ask_price",
    "3": "last_price",
    "34": "quote_time_ms",
}

OPTION_FIELDS: dict[str, str] = {
    "2": "bid_price",
    "3": "ask_price",
    "4": "last_price",
    "10": "volatility",
    "28": "delta",
    "29": "gamma",
    "30": "theta",
    "31": "vega",
    "32": "rho",
    "35": "underlying_price",
    "38": "quote_time_ms",
}

# Greeks the monitor tracks deltas on, in a stable display order.
GREEK_FIELDS: tuple[str, ...] = ("delta", "gamma", "theta", "vega", "rho", "volatility")


def parse_content_item(
    item: Mapping[str, Any],
    field_map: Mapping[str, str],
) -> tuple[Optional[str], dict[str, Any]]:
    """Translate one stream ``content`` entry into ``(symbol, named-fields)``.

    Args:
        item: A single content dict, e.g. ``{"key": "AAPL", "3": 123.4}``.
        field_map: Numeric-key -> friendly-name mapping (:data:`EQUITY_FIELDS`
            or :data:`OPTION_FIELDS`). Unmapped numeric keys are ignored;
            already-named keys (if a caller pre-labels) pass through.

    Returns:
        ``(symbol, fields)``. ``symbol`` is ``None`` when no ``key`` is present.
    """
    symbol = item.get("key")
    fields: dict[str, Any] = {}
    for raw_key, value in item.items():
        if raw_key == "key":
            continue
        name = field_map.get(str(raw_key))
        if name is not None:
            fields[name] = value
        elif raw_key in field_map.values():
            fields[raw_key] = value
    return (symbol, fields)


def parse_message(
    message: Mapping[str, Any],
    field_map: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    """Parse a full stream message into ``{symbol: named-fields}``.

    A Schwab stream message looks like ``{"content": [ {item}, ... ], ...}``.
    Each item is normalized via :func:`parse_content_item`.
    """
    out: dict[str, dict[str, Any]] = {}
    for item in message.get("content", []) or []:
        symbol, fields = parse_content_item(item, field_map)
        if symbol is not None:
            out[symbol] = fields
    return out


@dataclass
class TickCache:
    """Thread-safe latest-value-per-symbol cache fed by stream messages.

    Each update *merges* new fields into the symbol's existing snapshot (Schwab
    only sends changed fields after the initial image), and records the wall
    clock time the update was received so staleness can be judged.
    """

    _ticks: MutableMapping[str, dict[str, Any]] = field(default_factory=dict)
    _updated_at: MutableMapping[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(
        self,
        symbol: str,
        fields: Mapping[str, Any],
        *,
        now_s: Optional[float] = None,
    ) -> None:
        """Merge ``fields`` into ``symbol``'s snapshot and stamp the time."""
        now = time.time() if now_s is None else now_s
        with self._lock:
            current = self._ticks.setdefault(symbol, {})
            current.update(fields)
            self._updated_at[symbol] = now

    def apply_message(
        self,
        message: Mapping[str, Any],
        field_map: Mapping[str, str],
        *,
        now_s: Optional[float] = None,
    ) -> None:
        """Parse a stream message and merge every symbol it contains."""
        for symbol, fields in parse_message(message, field_map).items():
            self.update(symbol, fields, now_s=now_s)

    def get(self, symbol: str) -> Optional[dict[str, Any]]:
        """Return a copy of ``symbol``'s latest snapshot, or None if unseen."""
        with self._lock:
            snap = self._ticks.get(symbol)
            return dict(snap) if snap is not None else None

    def updated_at(self, symbol: str) -> Optional[float]:
        """Epoch seconds of the last update for ``symbol`` (or None)."""
        with self._lock:
            return self._updated_at.get(symbol)

    def age_seconds(
        self, symbol: str, *, now_s: Optional[float] = None
    ) -> Optional[float]:
        """Seconds since ``symbol`` was last updated, or None if never."""
        ts = self.updated_at(symbol)
        if ts is None:
            return None
        now = time.time() if now_s is None else now_s
        return max(now - ts, 0.0)

    def is_fresh(
        self,
        symbol: str,
        max_age_s: float,
        *,
        now_s: Optional[float] = None,
    ) -> bool:
        """True if ``symbol`` updated within ``max_age_s`` (fail-closed)."""
        age = self.age_seconds(symbol, now_s=now_s)
        if age is None:
            return False
        return age <= max_age_s

    def symbols(self) -> list[str]:
        """All symbols currently held."""
        with self._lock:
            return list(self._ticks)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """A deep-ish copy of the whole cache (per-symbol dicts copied)."""
        with self._lock:
            return {sym: dict(fields) for sym, fields in self._ticks.items()}


class StreamManager:
    """Thin wrapper over ``schwab-py``'s ``StreamClient`` feeding a TickCache.

    Reuses an already-authenticated schwab-py client (from
    :mod:`scalper.auth.client`); it never starts its own OAuth flow. ``schwab-py``
    is imported lazily so this module stays importable without the dependency.
    """

    def __init__(self, client: Any, cache: Optional[TickCache] = None) -> None:
        self._client = client
        self.cache = cache or TickCache()
        self._stream: Any = None

    def _ensure_stream(self) -> Any:
        if self._stream is None:
            from schwab.streaming import StreamClient  # lazy import

            self._stream = StreamClient(self._client)
        return self._stream

    async def login(self) -> None:
        """Authenticate the streaming session (delegates to schwab-py)."""
        await self._ensure_stream().login()

    def _equity_handler(self, message: Mapping[str, Any]) -> None:
        self.cache.apply_message(message, EQUITY_FIELDS)

    def _option_handler(self, message: Mapping[str, Any]) -> None:
        self.cache.apply_message(message, OPTION_FIELDS)

    async def subscribe_equities(self, symbols: Iterable[str]) -> None:
        """Subscribe to LEVELONE_EQUITIES for ``symbols`` and route to cache."""
        stream = self._ensure_stream()
        stream.add_level_one_equity_handler(self._equity_handler)
        await stream.level_one_equity_subs(list(symbols))

    async def subscribe_options(self, option_symbols: Iterable[str]) -> None:
        """Subscribe to LEVELONE_OPTIONS for ``option_symbols`` (live greeks)."""
        stream = self._ensure_stream()
        stream.add_level_one_option_handler(self._option_handler)
        await stream.level_one_option_subs(list(option_symbols))

    async def handle_message(self) -> None:
        """Pump one batch of messages through the registered handlers."""
        await self._ensure_stream().handle_message()


__all__ = [
    "EQUITY_FIELDS",
    "OPTION_FIELDS",
    "GREEK_FIELDS",
    "TickCache",
    "StreamManager",
    "parse_content_item",
    "parse_message",
]
