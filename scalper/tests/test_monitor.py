"""Tests for the 15-second statistics & greek monitor (Phase 3)."""

from __future__ import annotations

from scalper.config import MonitorParams, SignalParams
from scalper.data.stream import TickCache
from scalper.live.monitor import (
    Monitor,
    greek_deltas,
    lookback_samples,
    realized_vol,
    trailing_return,
)


def test_lookback_samples_minutes_to_samples():
    # 5 minutes at 15s cadence == 20 samples.
    assert lookback_samples(5, 15.0) == 20
    assert lookback_samples(1, 15.0) == 4
    assert lookback_samples(0, 15.0) == 1  # floored to >= 1


def test_trailing_return_and_insufficient_history():
    prices = [100.0, 101.0, 102.0]
    assert round(trailing_return(prices, 2), 6) == 0.02  # 102/100 - 1
    assert trailing_return(prices, 3) is None  # not enough history


def test_realized_vol_basic():
    assert realized_vol([100.0]) is None
    v = realized_vol([100.0, 101.0, 100.0, 101.0])
    assert v is not None and v > 0


def test_greek_deltas_subtracts_previous():
    cur = {"delta": -0.40, "gamma": 0.03}
    prev = {"delta": -0.30, "gamma": 0.05}
    d = greek_deltas(cur, prev)
    assert round(d["delta"], 4) == -0.10
    assert round(d["gamma"], 4) == -0.02
    # missing previous -> None
    assert greek_deltas(cur, None)["delta"] is None


def _seed_prices(monitor: Monitor, series: dict[str, list[float]], start_s: float = 0.0):
    """Drive the monitor through samples by feeding the cache step by step."""
    n = len(next(iter(series.values())))
    samples = []
    for i in range(n):
        for sym, prices in series.items():
            monitor.cache.update(sym, {"last_price": prices[i]}, now_s=start_s + i)
        samples.append(monitor.sample(now_s=start_s + i))
    return samples


def test_monitor_red_count_uses_signal_definition():
    cache = TickCache()
    basket = ["A", "B"]
    signal = SignalParams(lookback_minutes=1, red_threshold=0.0, min_red_count=2)
    # cadence so that lookback (1 min) == 1 sample for a short test
    params = MonitorParams(sample_seconds=60.0, roll_window=5)
    mon = Monitor(cache, basket, [], signal, params)

    # Two samples: both symbols decline on the second -> both red.
    series = {"A": [100.0, 99.0], "B": [100.0, 98.0]}
    samples = _seed_prices(mon, series)
    last = samples[-1]
    assert last["A"].is_red is True
    assert last["B"].is_red is True
    assert last["A"].red_count == 2


def test_monitor_tracks_option_greek_deltas_and_persists(tmp_path):
    from scalper.data.store import connect, load_samples

    cache = TickCache()
    signal = SignalParams(lookback_minutes=1, red_threshold=0.0, min_red_count=1)
    params = MonitorParams(sample_seconds=60.0, roll_window=5)
    db = tmp_path / "samples.sqlite"
    conn = connect(db)
    try:
        mon = Monitor(
            cache,
            ["A"],
            [],
            signal,
            params,
            option_for={"A": "A_PUT"},
            conn=conn,
        )
        # Sample 1: greeks present.
        cache.update("A", {"last_price": 100.0}, now_s=0.0)
        cache.update("A_PUT", {"delta": -0.50, "gamma": 0.02}, now_s=0.0)
        mon.sample(now_s=0.0)
        # Sample 2: greeks moved -> deltas computed vs previous sample.
        cache.update("A", {"last_price": 99.0}, now_s=60.0)
        cache.update("A_PUT", {"delta": -0.60, "gamma": 0.03}, now_s=60.0)
        s2 = mon.sample(now_s=60.0)
        assert round(s2["A"].greek_deltas["delta"], 4) == -0.10
        assert s2["A"].greeks["delta"] == -0.60

        stored = load_samples(conn, "A")
    finally:
        conn.close()

    assert len(stored) == 2
    assert stored["delta"].iloc[-1] == -0.60
    assert round(stored["d_delta"].iloc[-1], 4) == -0.10


def test_monitor_marks_stale_symbol():
    cache = TickCache()
    signal = SignalParams(lookback_minutes=1, red_threshold=0.0, min_red_count=1)
    params = MonitorParams(sample_seconds=15.0, roll_window=5, max_tick_age_s=10.0)
    mon = Monitor(cache, ["A"], [], signal, params)
    cache.update("A", {"last_price": 100.0}, now_s=0.0)
    # sample far in the future: the tick is older than max_tick_age_s.
    s = mon.sample(now_s=100.0)
    assert s["A"].stale is True


def test_monitor_run_loop_bounded():
    cache = TickCache()
    signal = SignalParams(lookback_minutes=1, red_threshold=0.0, min_red_count=1)
    params = MonitorParams(sample_seconds=15.0, roll_window=5)
    mon = Monitor(cache, ["A"], [], signal, params)
    cache.update("A", {"last_price": 100.0}, now_s=0.0)
    lines: list[str] = []
    taken = mon.run(max_samples=3, sleep_fn=lambda _s: None, log_fn=lines.append)
    assert taken == 3
    assert len(lines) == 3
