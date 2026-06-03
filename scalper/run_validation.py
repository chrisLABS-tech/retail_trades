"""Phase 0 + Phase 1 runner: authenticate, pull data, print the validation report.

Usage::

    # Live: requires a completed OAuth token (see scalper/.env.example)
    python -m scalper.run_validation

    # From a previously-saved local sqlite store
    python -m scalper.run_validation --store data.sqlite

    # Offline smoke test of the whole pipeline on synthetic data
    python -m scalper.run_validation --demo

The runner is intentionally read-only: it never places orders. Phase 1 is a
GO/NO-GO gate — pass it before building live monitoring or execution.
"""

from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from scalper.backtest.engine import run_backtest, signal_fire_count
from scalper.backtest.report import render_report
from scalper.config import DEFAULT_SETTINGS, Settings


def _load_live(settings: Settings) -> pd.DataFrame:
    from scalper.auth.client import build_client
    from scalper.data.history import confirm_realtime, fetch_aligned_closes

    client = build_client()

    # Phase 0 gate: confirm the feed is real-time, not 15-min delayed.
    probe = settings.basket[0]
    fresh, age = confirm_realtime(client, probe)
    age_str = "unknown" if age is None else f"{age:.1f}s"
    status = "REAL-TIME" if fresh else "DELAYED/STALE"
    print(f"[phase0] {probe} quote age={age_str} -> {status}")
    if not fresh:
        print(
            "[phase0] WARNING: quote is not confirmed real-time. A delayed "
            "feed makes the 10-minute strategy impossible; resolve entitlement "
            "before relying on live results.",
            file=sys.stderr,
        )

    return fetch_aligned_closes(
        client,
        settings.basket + settings.targets,
        days=settings.data.history_days,
        minute=settings.data.bar_minutes,
    )


def _load_store(settings: Settings, path: str) -> pd.DataFrame:
    from scalper.data.store import connect, load_closes

    conn = connect(path)
    try:
        return load_closes(conn, settings.basket + settings.targets)
    finally:
        conn.close()


def _demo_data(settings: Settings, n: int = 2_000, seed: int = 7) -> pd.DataFrame:
    """Synthetic aligned closes so the pipeline runs without credentials."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("2024-01-02 14:30", periods=n, freq="1min", tz="UTC")
    symbols = settings.basket + settings.targets
    data = {}
    for sym in symbols:
        steps = rng.normal(0, 0.0008, size=n)
        data[sym] = 100.0 * np.exp(np.cumsum(steps))
    return pd.DataFrame(data, index=index)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--store", metavar="PATH", help="load bars from a local sqlite store")
    source.add_argument("--demo", action="store_true", help="run on synthetic data (no auth)")
    parser.add_argument("--boot", type=int, default=10_000, help="bootstrap resamples")
    parser.add_argument("--seed", type=int, default=12345, help="RNG seed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    settings = DEFAULT_SETTINGS

    if args.demo:
        closes = _demo_data(settings)
    elif args.store:
        closes = _load_store(settings, args.store)
    else:
        closes = _load_live(settings)

    if closes.empty:
        print("No aligned bars available; cannot run validation.", file=sys.stderr)
        return 1

    fires = signal_fire_count(closes, settings.basket, settings.signal)
    print(
        f"[phase1] {len(closes)} aligned bars; signal fired {fires} times "
        f"({settings.signal.min_red_count}/{len(settings.basket)} basket red "
        f"over {settings.signal.lookback_minutes}m)."
    )

    results = run_backtest(closes, settings.basket, settings.targets, settings.signal)
    report = render_report(
        results,
        settings.signal,
        move_threshold=settings.data.move_threshold,
        assumed_spread_pct=settings.data.assumed_spread_pct,
        n_boot=args.boot,
        seed=args.seed,
    )
    print(report)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
