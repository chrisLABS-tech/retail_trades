"""Live put quote viewer: visualize one put's bid/mid/ask — no orders, ever.

Given a single **user-provided put option symbol**, this module streams its
level-one quote from the Schwab API (via the existing
:class:`~scalper.data.stream.StreamManager`), renders a live terminal view of
the **bid / mid / ask**, and records every sample to sqlite so the session can
be **replayed** later without any network or auth.

Strictly read-only: it never imports the execution layer and never builds or
sends an order. Calls (and anything that is not a put) are rejected up front.

Usage::

    # Live: stream, visualize and record a put's bid/mid/ask
    python -m scalper.live.puts_viewer --option "INFQ  250620P00021000" \
        --db scalper/data/scalper.sqlite

    # Replay a previously recorded session (no auth / network needed)
    python -m scalper.live.puts_viewer --option "INFQ  250620P00021000" \
        --db scalper/data/scalper.sqlite --replay --speed 10

All the rendering/parsing logic is pure functions so it is unit-testable; the
network loop is isolated in :func:`run_live`.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from scalper.data.stream import StreamManager, TickCache

# OSI-style option symbol as used by the Schwab streamer: a root padded to six
# characters, yymmdd expiration, C/P flag, then the strike in thousandths.
# Roots may include "." (share classes, e.g. BRK.B), "$" and "^" (index/cash
# conventions) in addition to letters.
_OSI_RE = re.compile(r"^(?P<root>[A-Z.$^]{1,6})\s*(?P<exp>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")

# Sparkline glyphs from low to high.
_SPARK = "▁▂▃▄▅▆▇█"


def parse_option_symbol(symbol: str) -> dict[str, Any]:
    """Parse an OSI-style option symbol into its parts.

    Args:
        symbol: e.g. ``"INFQ  250620P00021000"`` (padding optional).

    Returns:
        Dict with ``root``, ``expiration`` (yymmdd string), ``contract_type``
        (``"PUT"``/``"CALL"``) and ``strike`` (float).

    Raises:
        ValueError: If the symbol does not look like an option symbol.
    """
    match = _OSI_RE.match(symbol.strip().upper())
    if match is None:
        raise ValueError(
            f"{symbol!r} is not a recognizable option symbol "
            "(expected e.g. 'INFQ  250620P00021000')."
        )
    return {
        "root": match.group("root"),
        "expiration": match.group("exp"),
        "contract_type": "PUT" if match.group("cp") == "P" else "CALL",
        "strike": int(match.group("strike")) / 1000.0,
    }


def require_put(symbol: str) -> str:
    """Validate ``symbol`` is a put and return it normalized (uppercased).

    Raises:
        ValueError: If the symbol is a call or unparseable. Puts only — this
            viewer deliberately refuses anything else.
    """
    normalized = symbol.strip().upper()
    parsed = parse_option_symbol(normalized)
    if parsed["contract_type"] != "PUT":
        raise ValueError(f"{symbol!r} is a {parsed['contract_type']}; only puts are supported.")
    return normalized


def mid_price(bid: Optional[float], ask: Optional[float]) -> Optional[float]:
    """Midpoint of bid/ask, or None when either side is missing/invalid.

    A zero bid is legitimate for a near-worthless put (no buyers), so only the
    ask must be strictly positive.
    """
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid >= 0 and ask > 0:
        return (float(bid) + float(ask)) / 2.0
    return None


@dataclass(frozen=True)
class QuoteSample:
    """One observed bid/mid/ask sample for the tracked put."""

    ts_ms: int
    bid: Optional[float]
    ask: Optional[float]
    mid: Optional[float]
    last: Optional[float] = None

    @classmethod
    def from_snapshot(
        cls, snapshot: Mapping[str, Any], *, now_ms: Optional[int] = None
    ) -> "QuoteSample":
        """Build a sample from a :class:`TickCache` option snapshot."""
        ts = int(time.time() * 1000) if now_ms is None else int(now_ms)
        bid = _num(snapshot.get("bid_price"))
        ask = _num(snapshot.get("ask_price"))
        return cls(
            ts_ms=ts,
            bid=bid,
            ask=ask,
            mid=mid_price(bid, ask),
            last=_num(snapshot.get("last_price")),
        )

    def as_store_values(self) -> dict[str, Any]:
        """Flatten to the columns :func:`scalper.data.store.save_quote` wants."""
        return {"bid": self.bid, "ask": self.ask, "mid": self.mid, "last": self.last}


def _num(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) else None


def spread_gauge(
    bid: Optional[float], mid: Optional[float], ask: Optional[float], width: int = 21
) -> str:
    """A fixed-width gauge placing B / M / A across the bid->ask range.

    E.g. ``[B---------M---------A]``. When the spread is zero (or data is
    missing) a flat/empty gauge is returned.
    """
    if bid is None or ask is None or mid is None or ask < bid:
        return "[" + " " * width + "]"
    cells = [" "] * width
    span = ask - bid
    if span <= 0:
        cells[width // 2] = "M"
    else:
        cells = ["-"] * width
        cells[0] = "B"
        cells[-1] = "A"
        pos = round((mid - bid) / span * (width - 1))
        cells[min(max(pos, 0), width - 1)] = "M"
    return "[" + "".join(cells) + "]"


def sparkline(values: list[Optional[float]], width: int = 30) -> str:
    """Unicode sparkline of the most recent ``width`` values (gaps -> space)."""
    window = values[-width:]
    usable = [v for v in window if v is not None]
    if not usable:
        return ""
    lo, hi = min(usable), max(usable)
    span = hi - lo
    out = []
    for v in window:
        if v is None:
            out.append(" ")
        elif span <= 0:
            out.append(_SPARK[0])
        else:
            idx = int((v - lo) / span * (len(_SPARK) - 1))
            out.append(_SPARK[idx])
    return "".join(out)


def format_quote_line(symbol: str, sample: QuoteSample, mids: list[Optional[float]]) -> str:
    """One full display line: time, bid/mid/ask, spread gauge, mid sparkline."""

    def f(x: Optional[float]) -> str:
        return "  n/a" if x is None else f"{x:7.2f}"

    clock = time.strftime("%H:%M:%S", time.localtime(sample.ts_ms / 1000))
    gauge = spread_gauge(sample.bid, sample.mid, sample.ask)
    spark = sparkline(mids)
    return (
        f"{clock} {symbol:<21} bid={f(sample.bid)} mid={f(sample.mid)} "
        f"ask={f(sample.ask)} {gauge} {spark}"
    )


class PutQuoteTracker:
    """Tracks one put's bid/mid/ask: samples a cache, renders, persists.

    Read-only by construction — it holds no order/execution objects at all.

    Args:
        symbol: The put option symbol to track (validated via :func:`require_put`).
        cache: Live tick cache fed by a stream (or by a replay/test harness).
        conn: Optional open sqlite connection; when given, each sample is
            stored to the ``option_quotes`` table for later replay.
    """

    def __init__(self, symbol: str, cache: TickCache, *, conn: Any = None) -> None:
        self.symbol = require_put(symbol)
        self.cache = cache
        self.conn = conn
        self.mids: list[Optional[float]] = []

    def sample(self, *, now_ms: Optional[int] = None) -> Optional[QuoteSample]:
        """Take one sample from the cache; persist and remember the mid.

        Returns None when the cache has not seen the symbol yet.
        """
        snapshot = self.cache.get(self.symbol)
        if snapshot is None:
            return None
        quote = QuoteSample.from_snapshot(snapshot, now_ms=now_ms)
        self.mids.append(quote.mid)
        if self.conn is not None:
            from scalper.data.store import save_quote

            save_quote(self.conn, quote.ts_ms, self.symbol, quote.as_store_values())
        return quote

    def render(self, quote: Optional[QuoteSample]) -> str:
        """Render the latest sample (or a waiting message) as a display line."""
        if quote is None:
            return f"{self.symbol:<21} waiting for first quote..."
        return format_quote_line(self.symbol, quote, self.mids)


async def run_live(
    client: Any,
    symbol: str,
    *,
    conn: Any = None,
    interval_s: float = 1.0,
    max_samples: Optional[int] = None,
    log_fn: Any = print,
) -> int:
    """Stream the put's quotes live, render each sample, and record it.

    Args:
        client: An authenticated schwab-py client (see :mod:`scalper.auth.client`).
        symbol: The put option symbol to track.
        conn: Optional sqlite connection for recording (see :func:`replay`).
        interval_s: How often to sample/render.
        max_samples: Stop after this many samples (None == run until interrupted).
        log_fn: Where rendered lines go.

    Returns:
        The number of samples rendered.
    """
    tracker = PutQuoteTracker(symbol, TickCache(), conn=conn)
    manager = StreamManager(client, tracker.cache)
    await manager.login()
    await manager.subscribe_options([tracker.symbol])

    async def pump() -> None:
        while True:
            await manager.handle_message()

    pump_task = asyncio.create_task(pump())
    taken = 0
    try:
        while max_samples is None or taken < max_samples:
            await asyncio.sleep(interval_s)
            log_fn(tracker.render(tracker.sample()))
            taken += 1
    finally:
        pump_task.cancel()
    return taken


def replay(
    conn: Any,
    symbol: str,
    *,
    speed: float = 1.0,
    sleep_fn: Any = time.sleep,
    log_fn: Any = print,
) -> int:
    """Replay a recorded session from sqlite at ``speed``x real time.

    Args:
        conn: Open sqlite connection holding ``option_quotes`` rows.
        symbol: The put option symbol to replay.
        speed: Time multiplier (10 == ten times faster than recorded).
        sleep_fn: Injectable sleep (tests pass a no-op).
        log_fn: Where rendered lines go.

    Returns:
        The number of quotes replayed.
    """
    from scalper.data.store import load_quotes

    put = require_put(symbol)
    if speed <= 0:
        raise ValueError("speed must be > 0")
    frame = load_quotes(conn, put)
    if frame.empty:
        log_fn(f"No recorded quotes for {put}.")
        return 0

    mids: list[Optional[float]] = []
    prev_ts: Optional[int] = None
    count = 0
    for ts, row in frame.iterrows():
        ts_ms = int(ts.value // 1_000_000)  # pandas .value is ns -> ms
        if prev_ts is not None:
            sleep_fn(max(ts_ms - prev_ts, 0) / 1000.0 / speed)
        prev_ts = ts_ms
        quote = QuoteSample(
            ts_ms=ts_ms,
            bid=_num(row["bid"]),
            ask=_num(row["ask"]),
            mid=_num(row["mid"]),
            last=_num(row["last"]),
        )
        mids.append(quote.mid)
        log_fn(format_quote_line(put, quote, mids))
        count += 1
    return count


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--option",
        required=True,
        metavar="SYMBOL",
        help="put option symbol to track, e.g. 'INFQ  250620P00021000'",
    )
    parser.add_argument(
        "--db",
        default="scalper/data/scalper.sqlite",
        metavar="PATH",
        help="sqlite store for recording / replaying quotes",
    )
    parser.add_argument("--replay", action="store_true", help="replay a recorded session (no auth)")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    parser.add_argument(
        "--interval", type=float, default=1.0, help="live sampling interval in seconds"
    )
    parser.add_argument(
        "--max-samples", type=int, default=None, help="stop after N samples (live mode)"
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    symbol = require_put(args.option)  # fail fast before any auth/network

    from scalper.data.store import connect

    conn = connect(args.db)
    try:
        if args.replay:
            replay(conn, symbol, speed=args.speed)
            return 0

        from scalper.auth.client import build_client

        client = build_client()
        asyncio.run(
            run_live(
                client,
                symbol,
                conn=conn,
                interval_s=args.interval,
                max_samples=args.max_samples,
            )
        )
        return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PutQuoteTracker",
    "QuoteSample",
    "parse_option_symbol",
    "require_put",
    "mid_price",
    "spread_gauge",
    "sparkline",
    "format_quote_line",
    "run_live",
    "replay",
    "main",
]
