"""Phase 1 backtest: engine, validation stats, and report."""

from scalper.backtest.engine import (
    TargetResult,
    forward_returns,
    run_backtest,
    signal_fire_count,
)
from scalper.backtest.report import render_report, render_target_report
from scalper.backtest.stats import (
    analyze_target,
    distribution_stats,
    significance,
    spread_adjusted_edge,
)

__all__ = [
    "TargetResult",
    "analyze_target",
    "distribution_stats",
    "forward_returns",
    "render_report",
    "render_target_report",
    "run_backtest",
    "significance",
    "signal_fire_count",
    "spread_adjusted_edge",
]
