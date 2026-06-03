"""Tests for Phase 0 history parsing, quote freshness, and fetch helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalper.data.history import (
    DEFAULT_MAX_QUOTE_AGE_S,
    align_closes,
    candles_to_frame,
    confirm_realtime,
    fetch_aligned_closes,
    is_realtime,
    price_history_to_frame,
    quote_age_seconds,
)

# 2024-01-02 14:30:00 UTC in ms
_T0 = 1_704_205_800_000


def _candles(n=3, start=_T0, step_ms=60_000):
    return [
        {
            "open": 100 + i,
            "high": 101 + i,
            "low": 99 + i,
            "close": 100.5 + i,
            "volume": 1000 + i,
            "datetime": start + i * step_ms,
        }
        for i in range(n)
    ]


def test_candles_to_frame_shape_and_index():
    frame = candles_to_frame(_candles(3))
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]
    assert len(frame) == 3
    assert frame.index.tz is not None
    assert frame.index.is_monotonic_increasing


def test_candles_to_frame_empty():
    frame = candles_to_frame([])
    assert frame.empty
    assert list(frame.columns) == ["open", "high", "low", "close", "volume"]


def test_price_history_to_frame():
    frame = price_history_to_frame({"candles": _candles(2), "symbol": "X"})
    assert len(frame) == 2


def test_align_closes_drops_unobserved_bars():
    a = candles_to_frame(_candles(3))
    # b is missing the middle bar
    b = candles_to_frame([_candles(3)[0], _candles(3)[2]])
    aligned = align_closes({"A": a, "B": b})
    assert list(aligned.columns) == ["A", "B"]
    # only the two timestamps both share remain
    assert len(aligned) == 2
    assert not aligned.isna().any().any()


def test_quote_age_and_realtime():
    now_ms = _T0 + 30_000  # 30s after quote
    resp = {"VGT": {"quote": {"quoteTime": _T0, "lastPrice": 123.4}}}
    age = quote_age_seconds(resp, "VGT", now_s=now_ms / 1000.0)
    assert age == 30.0
    assert is_realtime(resp, "VGT", now_s=now_ms / 1000.0)


def test_delayed_quote_not_realtime():
    now_ms = _T0 + 16 * 60_000  # 16 minutes later
    resp = {"VGT": {"quote": {"quoteTime": _T0}}}
    assert not is_realtime(resp, "VGT", now_s=now_ms / 1000.0)


def test_quote_age_missing_timestamp():
    assert quote_age_seconds({"X": {"quote": {}}}, "X") is None
    # fail-closed: no timestamp => not real-time
    assert not is_realtime({"X": {"quote": {}}}, "X")


def test_quote_age_long_field_fallback():
    now_ms = _T0 + 10_000
    resp = {"X": {"quote": {"tradeTimeInLong": _T0}}}
    assert quote_age_seconds(resp, "X", now_s=now_ms / 1000.0) == 10.0


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, now_ms):
        self._now_ms = now_ms

    def get_price_history_every_minute(self, symbol):
        return _FakeResp({"candles": _candles(4), "symbol": symbol})

    def get_quote(self, symbol):
        return _FakeResp({symbol: {"quote": {"quoteTime": self._now_ms - 5_000}}})


def test_fetch_aligned_closes_with_fake_client():
    client = _FakeClient(now_ms=_T0)
    closes = fetch_aligned_closes(client, ["A", "B"], days=1, minute=1)
    assert list(closes.columns) == ["A", "B"]
    assert len(closes) == 4


def test_confirm_realtime_with_fake_client():
    now_ms = _T0 + 100_000
    client = _FakeClient(now_ms=now_ms)
    fresh, age = confirm_realtime(client, "A", now_s=now_ms / 1000.0)
    assert fresh
    assert age == 5.0
    assert DEFAULT_MAX_QUOTE_AGE_S > 0
