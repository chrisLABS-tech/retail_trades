"""Tests for the pre-trade risk gate (Phase 5)."""

from __future__ import annotations

from scalper.config import RiskLimits
from scalper.live.risk import RiskManager


def _limits():
    return RiskLimits(max_contracts=2, max_trades_per_day=3, daily_loss_limit=250.0)


def test_allows_within_limits():
    rm = RiskManager(_limits())
    assert rm.check(1).allowed
    assert rm.check(2).allowed


def test_blocks_oversized_and_nonpositive_contracts():
    rm = RiskManager(_limits())
    assert not rm.check(3).allowed
    assert not rm.check(0).allowed


def test_daily_trade_limit():
    rm = RiskManager(_limits())
    for _ in range(3):
        assert rm.check(1).allowed
        rm.record_entry()
    decision = rm.check(1)
    assert not decision.allowed
    assert "daily trade limit" in decision.reason


def test_daily_loss_limit_trips_and_kills():
    rm = RiskManager(_limits())
    rm.record_pnl(-250.0)  # exactly at the limit
    assert rm.killed
    assert not rm.check(1).allowed


def test_kill_switch_blocks_everything():
    rm = RiskManager(_limits())
    rm.kill()
    assert not rm.check(1).allowed


def test_reset_day_clears_counters():
    rm = RiskManager(_limits())
    rm.record_entry()
    rm.record_pnl(-100.0)
    rm.reset_day()
    assert rm.trades_today == 0
    assert rm.realized_pnl == 0.0
