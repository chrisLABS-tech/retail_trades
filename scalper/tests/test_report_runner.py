"""Tests for the report renderer and the end-to-end demo runner."""

from __future__ import annotations

import numpy as np
import pandas as pd

from scalper.backtest.engine import run_backtest
from scalper.backtest.report import render_report
from scalper.config import SignalParams
from scalper.run_validation import _demo_data, main
from scalper.config import DEFAULT_SETTINGS


def _closes(index, **series):
    return pd.DataFrame(series, index=index)


def test_render_report_contains_caveat_and_targets():
    idx = pd.date_range("2024-01-02 14:30", periods=40, freq="1min", tz="UTC")
    falling = np.linspace(100, 90, 40)
    closes = _closes(idx, A=falling, B=falling, C=falling, T=np.linspace(50, 47, 40))
    params = SignalParams(
        lookback_minutes=3, red_threshold=0.0, min_red_count=3, hold_minutes=5
    )
    results = run_backtest(closes, ["A", "B", "C"], ["T"], params)
    report = render_report(
        results, params, move_threshold=-0.002, assumed_spread_pct=8.0, n_boot=500
    )
    assert "PHASE 1 SIGNAL VALIDATION REPORT" in report
    assert "Target: T" in report
    assert "CAVEAT" in report
    assert "UNDERLYING" in report
    assert "spread" in report.lower()


def test_render_report_handles_no_results():
    report = render_report(
        {}, SignalParams(), move_threshold=-0.002, assumed_spread_pct=8.0
    )
    assert "no targets" in report


def test_demo_data_shape():
    closes = _demo_data(DEFAULT_SETTINGS, n=100, seed=1)
    expected_cols = DEFAULT_SETTINGS.basket + DEFAULT_SETTINGS.targets
    assert list(closes.columns) == expected_cols
    assert len(closes) == 100
    assert not closes.isna().any().any()


def test_main_demo_runs(capsys):
    rc = main(["--demo", "--boot", "200"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PHASE 1 SIGNAL VALIDATION REPORT" in out
    assert "[phase1]" in out
