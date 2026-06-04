"""The 15-second statistics & greek tracker (Phase 3, read-only).

Samples the live :class:`~scalper.data.stream.TickCache` on a fixed cadence
(``MonitorParams.sample_seconds``, default 15s) — deliberately *polling* a
snapshot rather than reacting to every tick, which is exactly the "every 15
seconds" cadence requested.

For each comparison (basket) and target symbol, each sample records:

* **Statistics** — trailing return over the lookback, realized vol over the
  rolling sample window, the symbol's red/not-red state, and the basket-wide
  ``red_count`` (computed with the **unchanged** signal definition so the live
  read matches the validated backtest).
* **Greeks** — the symbol's near-ATM option delta/gamma/theta/vega/rho/IV and,
  crucially, the **change since the previous 15-second sample** (Δgreek).

Everything that computes numbers is a pure function; :class:`Monitor` only holds
the rolling state, persists samples, and owns the sleep loop. No orders are ever
placed here.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

import pandas as pd

from scalper.config import MonitorParams, SignalParams
from scalper.data.stream import GREEK_FIELDS, TickCache
from scalper.signal.definition import red_count


def lookback_samples(lookback_minutes: int, sample_seconds: float) -> int:
    """Convert a minute-based lookback into a count of 15s (etc.) samples.

    E.g. a 5-minute lookback sampled every 15s spans ``5 * 60 / 15 = 20``
    samples. Always at least 1.
    """
    if sample_seconds <= 0:
        raise ValueError("sample_seconds must be > 0")
    return max(1, round(lookback_minutes * 60.0 / sample_seconds))


def trailing_return(prices: list[float], lookback: int) -> Optional[float]:
    """Simple return from ``lookback`` samples ago to the latest price.

    Returns None when there is insufficient history or the base price is not
    positive (avoids div-by-zero / nonsensical returns).
    """
    if lookback < 1 or len(prices) <= lookback:
        return None
    base = prices[-(lookback + 1)]
    last = prices[-1]
    if base is None or last is None or base <= 0:
        return None
    return last / base - 1.0


def realized_vol(prices: list[float]) -> Optional[float]:
    """Sample standard deviation of consecutive simple returns.

    Returns None with fewer than three usable prices (need >=2 returns for a
    sample stdev).
    """
    usable = [p for p in prices if p is not None and p > 0]
    if len(usable) < 3:
        return None
    rets = [usable[i] / usable[i - 1] - 1.0 for i in range(1, len(usable))]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def greek_deltas(
    current: Mapping[str, Any],
    previous: Optional[Mapping[str, Any]],
) -> dict[str, Optional[float]]:
    """Per-greek change ``current - previous`` (None when either side absent)."""
    out: dict[str, Optional[float]] = {}
    for name in GREEK_FIELDS:
        cur = current.get(name)
        prev = None if previous is None else previous.get(name)
        if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
            out[name] = float(cur) - float(prev)
        else:
            out[name] = None
    return out


def _coerce_price(snapshot: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Best-effort last/mid price from an equity snapshot."""
    if not snapshot:
        return None
    last = snapshot.get("last_price")
    if isinstance(last, (int, float)) and last > 0:
        return float(last)
    bid, ask = snapshot.get("bid_price"), snapshot.get("ask_price")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0:
        return (float(bid) + float(ask)) / 2.0
    return None


@dataclass
class SymbolSample:
    """One symbol's computed values at a single 15-second tick."""

    symbol: str
    last_price: Optional[float]
    trailing_ret: Optional[float]
    realized_vol: Optional[float]
    is_red: Optional[bool]
    red_count: Optional[int]
    greeks: dict[str, Optional[float]] = field(default_factory=dict)
    greek_deltas: dict[str, Optional[float]] = field(default_factory=dict)
    stale: bool = False

    def as_store_values(self) -> dict[str, Any]:
        """Flatten to the column names :func:`scalper.data.store.save_sample` wants."""
        values: dict[str, Any] = {
            "last_price": self.last_price,
            "trailing_ret": self.trailing_ret,
            "realized_vol": self.realized_vol,
            "is_red": None if self.is_red is None else int(self.is_red),
            "red_count": self.red_count,
        }
        for name in GREEK_FIELDS:
            values[name] = self.greeks.get(name)
            values[f"d_{name}"] = self.greek_deltas.get(name)
        return values


class Monitor:
    """Owns rolling state, computes 15-second samples, and (optionally) persists.

    Args:
        cache: The live tick cache being fed by a stream.
        basket: Comparison symbols (drive the red_count signal).
        targets: Traded symbols (also sampled).
        signal: Signal params (lookback/threshold/min_red_count).
        params: Monitor cadence/window params.
        option_for: Optional mapping ``underlying -> option symbol`` whose greeks
            are tracked for that underlying.
        conn: Optional open sqlite connection; when given, each sample is stored.
    """

    def __init__(
        self,
        cache: TickCache,
        basket: list[str],
        targets: list[str],
        signal: SignalParams,
        params: MonitorParams,
        *,
        option_for: Optional[Mapping[str, str]] = None,
        conn: Any = None,
    ) -> None:
        self.cache = cache
        self.basket = list(basket)
        self.targets = list(targets)
        self.signal = signal
        self.params = params
        self.option_for = dict(option_for or {})
        self.conn = conn

        self._symbols = list(dict.fromkeys([*self.basket, *self.targets]))
        self._price_history: dict[str, list[float]] = {s: [] for s in self._symbols}
        self._prev_greeks: dict[str, dict[str, Any]] = {}

    # -- internal helpers --------------------------------------------------- #
    def _append_prices(self, now_s: float) -> dict[str, Optional[float]]:
        """Record the latest price for every symbol; trim to the roll window."""
        latest: dict[str, Optional[float]] = {}
        keep = max(self.params.roll_window, 1)
        for sym in self._symbols:
            price = _coerce_price(self.cache.get(sym))
            latest[sym] = price
            if price is not None:
                hist = self._price_history[sym]
                hist.append(price)
                if len(hist) > keep + 1:
                    del hist[: len(hist) - (keep + 1)]
        return latest

    def _basket_red_count(self) -> Optional[int]:
        """Basket-wide red count from rolling history, via the signal definition."""
        look = lookback_samples(self.signal.lookback_minutes, self.params.sample_seconds)
        needed = look + 1
        if any(len(self._price_history[s]) < needed for s in self.basket):
            return None
        data = {s: self._price_history[s][-needed:] for s in self.basket}
        frame = pd.DataFrame(data)
        counts = red_count(frame, self.basket, look, self.signal.red_threshold)
        return int(counts.iloc[-1])

    # -- public API --------------------------------------------------------- #
    def sample(self, *, now_s: Optional[float] = None) -> dict[str, SymbolSample]:
        """Compute (and persist, if configured) one sample for every symbol."""
        now = time.time() if now_s is None else now_s
        self._append_prices(now)
        look = lookback_samples(self.signal.lookback_minutes, self.params.sample_seconds)
        basket_red = self._basket_red_count()

        results: dict[str, SymbolSample] = {}
        for sym in self._symbols:
            hist = self._price_history[sym]
            last = hist[-1] if hist else None
            ret = trailing_return(hist, look)
            vol = realized_vol(hist)
            is_red = None if ret is None else bool(ret < self.signal.red_threshold)

            opt_symbol = self.option_for.get(sym)
            greeks: dict[str, Optional[float]] = {}
            deltas: dict[str, Optional[float]] = {}
            if opt_symbol is not None:
                opt_snap = self.cache.get(opt_symbol) or {}
                greeks = {
                    name: (float(opt_snap[name]) if isinstance(opt_snap.get(name), (int, float)) else None)
                    for name in GREEK_FIELDS
                }
                deltas = greek_deltas(opt_snap, self._prev_greeks.get(sym))
                self._prev_greeks[sym] = dict(opt_snap)

            stale = not self.cache.is_fresh(sym, self.params.max_tick_age_s, now_s=now)
            results[sym] = SymbolSample(
                symbol=sym,
                last_price=last,
                trailing_ret=ret,
                realized_vol=vol,
                is_red=is_red,
                red_count=basket_red,
                greeks=greeks,
                greek_deltas=deltas,
                stale=stale,
            )

        if self.conn is not None:
            self._persist(now, results)
        return results

    def _persist(self, now_s: float, results: Mapping[str, SymbolSample]) -> None:
        from scalper.data.store import save_sample

        ts_ms = int(now_s * 1000)
        for sym, sample in results.items():
            save_sample(self.conn, ts_ms, sym, sample.as_store_values())

    def format_line(self, sample: SymbolSample) -> str:
        """One compact, log-friendly line for a symbol sample."""
        def f(x: Optional[float], nd: int = 4) -> str:
            return "  n/a" if x is None else f"{x:.{nd}f}"

        red = "?" if sample.is_red is None else ("RED" if sample.is_red else "—")
        greek_str = " ".join(
            f"{g[0]}={f(sample.greeks.get(g), 3)}(Δ{f(sample.greek_deltas.get(g), 3)})"
            for g in GREEK_FIELDS
        )
        flag = " STALE" if sample.stale else ""
        return (
            f"{sample.symbol:<6} px={f(sample.last_price, 2)} "
            f"ret={f(sample.trailing_ret)} vol={f(sample.realized_vol)} "
            f"{red} redN={sample.red_count} | {greek_str}{flag}"
        )

    def run(
        self,
        *,
        max_samples: Optional[int] = None,
        sleep_fn: Any = time.sleep,
        log_fn: Any = print,
    ) -> int:
        """Sampling loop: sample, log, sleep ``sample_seconds``; repeat.

        Args:
            max_samples: Stop after this many samples (None == run forever).
            sleep_fn: Injectable sleep (tests pass a no-op).
            log_fn: Where formatted lines go (defaults to ``print``).

        Returns:
            The number of samples taken.
        """
        taken = 0
        while max_samples is None or taken < max_samples:
            samples = self.sample()
            for sym in self._symbols:
                log_fn(self.format_line(samples[sym]))
            taken += 1
            if max_samples is not None and taken >= max_samples:
                break
            sleep_fn(self.params.sample_seconds)
        return taken


__all__ = [
    "Monitor",
    "SymbolSample",
    "lookback_samples",
    "trailing_return",
    "realized_vol",
    "greek_deltas",
]
