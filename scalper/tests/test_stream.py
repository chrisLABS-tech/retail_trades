"""Tests for the streaming tick cache and message parsers (Phase 3)."""

from __future__ import annotations

from scalper.data.stream import (
    EQUITY_FIELDS,
    OPTION_FIELDS,
    TickCache,
    parse_content_item,
    parse_message,
)


def test_parse_content_item_maps_numeric_fields():
    symbol, fields = parse_content_item(
        {"key": "AAPL", "1": 149.9, "2": 150.1, "3": 150.0, "34": 111}, EQUITY_FIELDS
    )
    assert symbol == "AAPL"
    assert fields == {
        "bid_price": 149.9,
        "ask_price": 150.1,
        "last_price": 150.0,
        "quote_time_ms": 111,
    }


def test_parse_content_item_ignores_unmapped_keys():
    _symbol, fields = parse_content_item({"key": "X", "999": 1}, EQUITY_FIELDS)
    assert fields == {}


def test_parse_content_item_passthrough_named_keys():
    # If a caller pre-labels with the friendly name, it survives.
    _symbol, fields = parse_content_item({"key": "X", "delta": -0.4}, OPTION_FIELDS)
    assert fields == {"delta": -0.4}


def test_parse_message_options_greeks():
    msg = {
        "content": [
            {"key": "AAPL_PUT", "28": -0.45, "29": 0.02, "30": -0.6, "31": 0.1, "10": 0.33}
        ]
    }
    parsed = parse_message(msg, OPTION_FIELDS)
    assert parsed["AAPL_PUT"]["delta"] == -0.45
    assert parsed["AAPL_PUT"]["gamma"] == 0.02
    assert parsed["AAPL_PUT"]["volatility"] == 0.33


def test_tick_cache_merges_partial_updates():
    tc = TickCache()
    tc.update("A", {"last_price": 100.0, "bid_price": 99.9}, now_s=1.0)
    tc.update("A", {"last_price": 101.0}, now_s=2.0)  # partial update merges
    snap = tc.get("A")
    assert snap == {"last_price": 101.0, "bid_price": 99.9}
    assert tc.updated_at("A") == 2.0


def test_tick_cache_freshness_fail_closed():
    tc = TickCache()
    assert tc.get("missing") is None
    assert tc.age_seconds("missing") is None
    assert tc.is_fresh("missing", 30.0) is False  # never seen -> not fresh

    tc.update("A", {"last_price": 1.0}, now_s=100.0)
    assert tc.is_fresh("A", 30.0, now_s=120.0) is True
    assert tc.is_fresh("A", 30.0, now_s=200.0) is False


def test_tick_cache_apply_message_and_snapshot():
    tc = TickCache()
    tc.apply_message(
        {"content": [{"key": "A", "3": 10.0}, {"key": "B", "3": 20.0}]},
        EQUITY_FIELDS,
        now_s=5.0,
    )
    assert set(tc.symbols()) == {"A", "B"}
    snap = tc.snapshot()
    assert snap["A"]["last_price"] == 10.0
    # snapshot is a copy: mutating it must not corrupt the cache
    snap["A"]["last_price"] = 999.0
    assert tc.get("A")["last_price"] == 10.0
