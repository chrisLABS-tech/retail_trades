"""Tests for the local sqlite bar store round-trip."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalper.data.history import candles_to_frame
from scalper.data.store import connect, load_closes, save_bars

_T0 = 1_704_205_800_000


def _frame(n=4, base=100.0):
    candles = [
        {
            "open": base + i,
            "high": base + i + 1,
            "low": base + i - 1,
            "close": base + i + 0.5,
            "volume": 10 + i,
            "datetime": _T0 + i * 60_000,
        }
        for i in range(n)
    ]
    return candles_to_frame(candles)


def test_save_and_load_round_trip(tmp_path):
    db = tmp_path / "bars.sqlite"
    conn = connect(db)
    try:
        assert save_bars(conn, "A", _frame(4, 100)) == 4
        assert save_bars(conn, "B", _frame(4, 200)) == 4
        wide = load_closes(conn, ["A", "B"])
    finally:
        conn.close()

    assert list(wide.columns) == ["A", "B"]
    assert len(wide) == 4
    assert wide["A"].iloc[0] == 100.5
    assert wide["B"].iloc[0] == 200.5


def test_upsert_is_idempotent(tmp_path):
    db = tmp_path / "bars.sqlite"
    conn = connect(db)
    try:
        save_bars(conn, "A", _frame(4, 100))
        save_bars(conn, "A", _frame(4, 100))  # same keys -> replace
        wide = load_closes(conn, ["A"])
    finally:
        conn.close()
    assert len(wide) == 4  # not duplicated


def test_load_missing_symbol_returns_empty(tmp_path):
    db = tmp_path / "bars.sqlite"
    conn = connect(db)
    try:
        save_bars(conn, "A", _frame(2, 100))
        wide = load_closes(conn, ["NOPE"])
    finally:
        conn.close()
    assert wide.empty


def test_save_empty_frame_noop(tmp_path):
    db = tmp_path / "bars.sqlite"
    conn = connect(db)
    try:
        empty = candles_to_frame([])
        assert save_bars(conn, "A", empty) == 0
    finally:
        conn.close()
