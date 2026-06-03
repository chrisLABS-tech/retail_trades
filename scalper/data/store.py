"""Local persistence for aligned bars (sqlite3, stdlib only).

Optional but recommended: caching history locally avoids re-pulling from the
Schwab API on every backtest run and lets you accumulate more history than a
single API call returns.

Stored in *long* form (one row per symbol/timestamp) so multiple symbols share
a table cleanly; :func:`load_closes` pivots back to the wide close-price frame
the signal/backtest expects.
"""

from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol   TEXT    NOT NULL,
    ts_ms    INTEGER NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    PRIMARY KEY (symbol, ts_ms)
);
"""

_SAMPLES_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts_ms          INTEGER NOT NULL,
    symbol         TEXT    NOT NULL,
    last_price     REAL,
    trailing_ret   REAL,
    realized_vol   REAL,
    is_red         INTEGER,
    red_count      INTEGER,
    delta          REAL,
    gamma          REAL,
    theta          REAL,
    vega           REAL,
    rho            REAL,
    volatility     REAL,
    d_delta        REAL,
    d_gamma        REAL,
    d_theta        REAL,
    d_vega         REAL,
    d_rho          REAL,
    d_volatility   REAL,
    PRIMARY KEY (symbol, ts_ms)
);
"""

# Numeric columns written/read for each 15-second sample, in schema order.
_SAMPLE_COLUMNS: tuple[str, ...] = (
    "last_price",
    "trailing_ret",
    "realized_vol",
    "is_red",
    "red_count",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "volatility",
    "d_delta",
    "d_gamma",
    "d_theta",
    "d_vega",
    "d_rho",
    "d_volatility",
)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the sqlite store and ensure the schema."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(_SCHEMA)
    conn.execute(_SAMPLES_SCHEMA)
    conn.commit()
    return conn


def save_bars(conn: sqlite3.Connection, symbol: str, frame: pd.DataFrame) -> int:
    """Upsert one symbol's OHLCV frame. Returns the number of rows written.

    Args:
        conn: Open connection from :func:`connect`.
        symbol: Ticker the frame belongs to.
        frame: OHLCV DataFrame indexed by tz-aware UTC timestamps.
    """
    if frame.empty:
        return 0
    idx = pd.DatetimeIndex(frame.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    ts_ms = idx.as_unit("ms").view("int64").tolist()
    records = [
        (
            symbol,
            int(ts),
            _opt(row.get("open")),
            _opt(row.get("high")),
            _opt(row.get("low")),
            _opt(row.get("close")),
            _opt(row.get("volume")),
        )
        for ts, (_, row) in zip(ts_ms, frame.iterrows())
    ]
    with closing(conn.cursor()) as cur:
        cur.executemany(
            "INSERT OR REPLACE INTO bars "
            "(symbol, ts_ms, open, high, low, close, volume) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            records,
        )
    conn.commit()
    return len(records)


def load_closes(
    conn: sqlite3.Connection,
    symbols: Iterable[str],
) -> pd.DataFrame:
    """Load aligned closes for ``symbols`` as a wide, NaN-free DataFrame.

    Columns are symbols, the index is tz-aware UTC. Rows missing any requested
    symbol's close are dropped so the basket is fully observed every bar.
    """
    symbols = list(symbols)
    if not symbols:
        return pd.DataFrame()
    placeholders = ",".join("?" for _ in symbols)
    query = (
        "SELECT symbol, ts_ms, close FROM bars "
        f"WHERE symbol IN ({placeholders}) ORDER BY ts_ms"
    )
    rows = conn.execute(query, symbols).fetchall()
    if not rows:
        return pd.DataFrame()
    long_df = pd.DataFrame(rows, columns=["symbol", "ts_ms", "close"])
    long_df["datetime"] = pd.to_datetime(long_df["ts_ms"], unit="ms", utc=True)
    wide = long_df.pivot(index="datetime", columns="symbol", values="close")
    wide.columns.name = None
    # Preserve requested order; only keep columns that exist.
    ordered = [s for s in symbols if s in wide.columns]
    return wide[ordered].dropna(how="any").sort_index()


def save_sample(
    conn: sqlite3.Connection,
    ts_ms: int,
    symbol: str,
    values: Mapping[str, object],
) -> None:
    """Upsert one 15-second monitor sample for ``symbol``.

    Args:
        conn: Open connection from :func:`connect`.
        ts_ms: Sample time in epoch milliseconds.
        symbol: The comparison/target symbol the sample describes.
        values: Mapping of column name -> value; recognised keys are those in
            :data:`_SAMPLE_COLUMNS`. Missing keys are stored as NULL.
    """
    row = [int(ts_ms), symbol]
    row += [_opt(values.get(col)) for col in _SAMPLE_COLUMNS]
    placeholders = ",".join("?" for _ in row)
    columns = "ts_ms, symbol, " + ", ".join(_SAMPLE_COLUMNS)
    with closing(conn.cursor()) as cur:
        cur.execute(
            f"INSERT OR REPLACE INTO samples ({columns}) VALUES ({placeholders})",
            row,
        )
    conn.commit()


def load_samples(
    conn: sqlite3.Connection,
    symbol: str,
) -> pd.DataFrame:
    """Load all stored 15-second samples for ``symbol`` as a time-indexed frame.

    Returns a DataFrame indexed by tz-aware UTC timestamp with one column per
    entry in :data:`_SAMPLE_COLUMNS`, sorted ascending. Empty if none stored.
    """
    cols = list(_SAMPLE_COLUMNS)
    query = (
        "SELECT ts_ms, " + ", ".join(cols) + " FROM samples "
        "WHERE symbol = ? ORDER BY ts_ms"
    )
    rows = conn.execute(query, (symbol,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=cols)
    frame = pd.DataFrame(rows, columns=["ts_ms", *cols])
    index = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True)
    frame = frame.drop(columns=["ts_ms"])
    frame.index = pd.DatetimeIndex(index, name="datetime")
    return frame


def _opt(value: object) -> float | None:
    """Coerce a possibly-NaN/None cell to float or None for sqlite."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return f
