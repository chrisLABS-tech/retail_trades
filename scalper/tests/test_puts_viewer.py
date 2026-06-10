"""Tests for the live put quote viewer (scalper/live/puts_viewer.py)."""

from __future__ import annotations

import pytest

from scalper.data.store import connect, load_quotes, save_quote
from scalper.data.stream import TickCache
from scalper.live.puts_viewer import (
    PutQuoteTracker,
    QuoteSample,
    format_quote_line,
    mid_price,
    parse_option_symbol,
    replay,
    require_put,
    sparkline,
    spread_gauge,
)

PUT = "INFQ  250620P00021000"
CALL = "INFQ  250620C00021000"


# --------------------------------------------------------------------------- #
# Symbol parsing / put-only validation
# --------------------------------------------------------------------------- #
def test_parse_option_symbol_put():
    parsed = parse_option_symbol(PUT)
    assert parsed["root"] == "INFQ"
    assert parsed["expiration"] == "250620"
    assert parsed["contract_type"] == "PUT"
    assert parsed["strike"] == pytest.approx(21.0)


def test_parse_option_symbol_unpadded():
    parsed = parse_option_symbol("P250620P00005500")
    assert parsed["root"] == "P"
    assert parsed["strike"] == pytest.approx(5.5)


def test_parse_option_symbol_invalid():
    with pytest.raises(ValueError):
        parse_option_symbol("AAPL")


def test_require_put_accepts_puts_and_normalizes():
    assert require_put("  infq  250620p00021000 ") == PUT


def test_require_put_rejects_calls():
    with pytest.raises(ValueError, match="CALL"):
        require_put(CALL)


# --------------------------------------------------------------------------- #
# Mid / rendering primitives
# --------------------------------------------------------------------------- #
def test_mid_price():
    assert mid_price(1.0, 2.0) == pytest.approx(1.5)
    assert mid_price(None, 2.0) is None
    assert mid_price(1.0, None) is None
    assert mid_price(1.0, 0.0) is None


def test_spread_gauge_places_markers():
    gauge = spread_gauge(1.0, 1.5, 2.0, width=21)
    assert gauge.startswith("[B")
    assert gauge.endswith("A]")
    assert "M" in gauge
    # Mid exactly between bid and ask -> M near the middle.
    assert gauge[1 + 10] == "M"


def test_spread_gauge_handles_missing_and_zero_spread():
    assert "M" not in spread_gauge(None, None, None)
    assert "M" in spread_gauge(1.0, 1.0, 1.0)


def test_sparkline():
    line = sparkline([1.0, 2.0, 3.0, None, 1.0])
    assert len(line) == 5
    assert line[3] == " "
    assert sparkline([]) == ""
    assert sparkline([None, None]) == ""
    assert sparkline([2.0, 2.0]) == "▁▁"


def test_format_quote_line_contains_prices():
    quote = QuoteSample(ts_ms=1_700_000_000_000, bid=1.0, ask=2.0, mid=1.5)
    line = format_quote_line(PUT, quote, [1.4, 1.5])
    assert "bid=" in line and "mid=" in line and "ask=" in line
    assert "1.50" in line


# --------------------------------------------------------------------------- #
# Tracker: sampling, persistence
# --------------------------------------------------------------------------- #
def test_tracker_rejects_calls():
    with pytest.raises(ValueError):
        PutQuoteTracker(CALL, TickCache())


def test_tracker_sample_before_first_tick_returns_none():
    tracker = PutQuoteTracker(PUT, TickCache())
    assert tracker.sample() is None
    assert "waiting" in tracker.render(None)


def test_tracker_samples_and_persists(tmp_path):
    conn = connect(tmp_path / "viewer.sqlite")
    cache = TickCache()
    tracker = PutQuoteTracker(PUT, cache, conn=conn)

    cache.update(PUT, {"bid_price": 1.0, "ask_price": 2.0, "last_price": 1.4})
    quote = tracker.sample(now_ms=1_000)
    assert quote is not None
    assert quote.mid == pytest.approx(1.5)
    assert tracker.mids == [pytest.approx(1.5)]

    cache.update(PUT, {"bid_price": 1.2, "ask_price": 2.2})
    tracker.sample(now_ms=2_000)

    frame = load_quotes(conn, PUT)
    assert len(frame) == 2
    assert frame["mid"].tolist() == [pytest.approx(1.5), pytest.approx(1.7)]
    conn.close()


# --------------------------------------------------------------------------- #
# Store round-trip and replay
# --------------------------------------------------------------------------- #
def test_save_and_load_quotes_roundtrip(tmp_path):
    conn = connect(tmp_path / "q.sqlite")
    save_quote(conn, 1_000, PUT, {"bid": 1.0, "ask": 2.0, "mid": 1.5, "last": None})
    save_quote(conn, 2_000, PUT, {"bid": 1.1, "ask": 2.1, "mid": 1.6, "last": 1.55})
    frame = load_quotes(conn, PUT)
    assert list(frame.columns) == ["bid", "ask", "mid", "last"]
    assert len(frame) == 2
    assert frame.iloc[0]["mid"] == pytest.approx(1.5)
    assert load_quotes(conn, "OTHER 250620P00001000").empty
    conn.close()


def test_replay_renders_recorded_session(tmp_path):
    conn = connect(tmp_path / "r.sqlite")
    save_quote(conn, 0, PUT, {"bid": 1.0, "ask": 2.0, "mid": 1.5})
    save_quote(conn, 1_000, PUT, {"bid": 1.2, "ask": 2.2, "mid": 1.7})

    lines: list[str] = []
    sleeps: list[float] = []
    count = replay(conn, PUT, speed=2.0, sleep_fn=sleeps.append, log_fn=lines.append)

    assert count == 2
    assert len(lines) == 2
    assert "1.50" in lines[0] and "1.70" in lines[1]
    # 1000ms gap replayed at 2x -> 0.5s sleep, only between quotes.
    assert sleeps == [pytest.approx(0.5)]
    conn.close()


def test_replay_empty_store(tmp_path):
    conn = connect(tmp_path / "e.sqlite")
    lines: list[str] = []
    assert replay(conn, PUT, log_fn=lines.append) == 0
    assert "No recorded quotes" in lines[0]
    conn.close()


def test_replay_rejects_bad_speed(tmp_path):
    conn = connect(tmp_path / "s.sqlite")
    with pytest.raises(ValueError):
        replay(conn, PUT, speed=0)
    conn.close()
