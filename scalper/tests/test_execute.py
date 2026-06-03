"""Tests for put selection and BTO/STC execution (Phase 5)."""

from __future__ import annotations

import pytest

from scalper.config import ExecutionMode, ExecutionParams, OptionParams, RiskLimits, SignalParams
from scalper.live.execute import (
    Executor,
    OptionQuote,
    OrderPlan,
    flatten_chain,
    limit_price,
    select_atm_put,
    should_exit,
    spread_pct,
)
from scalper.live.risk import RiskManager


def _chain():
    """A minimal Schwab-shaped put chain: two expiries, a few strikes."""
    return {
        "putExpDateMap": {
            "2024-01-19:3": {
                "95.0": [
                    {"symbol": "T_95P", "strikePrice": 95.0, "bid": 0.90, "ask": 1.00,
                     "daysToExpiration": 3, "delta": -0.30}
                ],
                "100.0": [
                    {"symbol": "T_100P", "strikePrice": 100.0, "bid": 1.95, "ask": 2.05,
                     "daysToExpiration": 3, "delta": -0.50}
                ],
                "120.0": [  # too far from spot -> excluded by moneyness
                    {"symbol": "T_120P", "strikePrice": 120.0, "bid": 19.0, "ask": 19.2,
                     "daysToExpiration": 3, "delta": -0.95}
                ],
            },
            "2024-01-26:10": {
                "100.0": [  # nearer-ATM but later expiry -> not preferred
                    {"symbol": "T_100P_W2", "strikePrice": 100.0, "bid": 2.50, "ask": 2.60,
                     "daysToExpiration": 10, "delta": -0.50}
                ],
            },
        }
    }


def test_spread_pct():
    assert spread_pct(0.0, 0.0) is None
    assert round(spread_pct(1.95, 2.05), 2) == 5.0


def test_flatten_chain_skips_no_quote():
    chain = {"putExpDateMap": {"e": {"100": [
        {"symbol": "X", "strikePrice": 100.0, "bid": 0, "ask": 0, "daysToExpiration": 1},
        {"symbol": "Y", "strikePrice": 100.0, "bid": 1.0, "ask": 1.1, "daysToExpiration": 1},
    ]}}}
    quotes = flatten_chain(chain, "PUT")
    assert [q.symbol for q in quotes] == ["Y"]


def test_select_atm_put_prefers_near_expiry_and_atm():
    params = OptionParams(contract_type="PUT", min_dte=0, max_dte=7, moneyness_tolerance=0.05)
    pick = select_atm_put(_chain(), spot=100.0, params=params, max_spread_pct=8.0)
    assert pick is not None
    assert pick.symbol == "T_100P"  # 3 DTE, strike == spot


def test_select_atm_put_spread_filter_excludes_wide():
    params = OptionParams(contract_type="PUT", min_dte=0, max_dte=7, moneyness_tolerance=0.05)
    # max_spread 4% excludes the 5%-spread 100P; falls back to the 95P (~10.5%? no)
    pick = select_atm_put(_chain(), spot=100.0, params=params, max_spread_pct=4.0)
    # 95P spread = 0.10/0.95 ~ 10.5% (excluded); 100P = 5% (excluded) -> none qualify
    assert pick is None


def test_select_atm_put_none_when_dte_out_of_window():
    params = OptionParams(contract_type="PUT", min_dte=0, max_dte=2, moneyness_tolerance=0.05)
    assert select_atm_put(_chain(), spot=100.0, params=params, max_spread_pct=8.0) is None


def test_limit_price_crosses_correctly():
    # mid=2.00, half=0.05; buy at offset 0.5 -> 2.025 -> rounded 2.02 or 2.03
    buy = limit_price(1.95, 2.05, "BUY", 0.5)
    sell = limit_price(1.95, 2.05, "SELL", 0.5)
    assert 2.0 <= buy <= 2.05
    assert 1.95 <= sell <= 2.0
    # offset 0 == mid for both sides
    assert limit_price(1.95, 2.05, "BUY", 0.0) == 2.0
    # offset 1 == far touch
    assert limit_price(1.95, 2.05, "BUY", 1.0) == 2.05
    assert limit_price(1.95, 2.05, "SELL", 1.0) == 1.95


def test_should_exit_time_stop():
    fire, reason = should_exit(entry_time_s=0.0, now_s=600.0, hold_minutes=10)
    assert fire and reason == "time-stop"
    assert not should_exit(0.0, 599.0, 10)[0]


def test_should_exit_take_profit_and_stop():
    tp, r = should_exit(0.0, 10.0, 10, entry_mark=2.0, current_mark=3.0, take_profit_pct=0.5)
    assert tp and r == "take-profit"
    sl, r2 = should_exit(0.0, 10.0, 10, entry_mark=2.0, current_mark=1.0, stop_loss_pct=0.5)
    assert sl and r2 == "stop-loss"


class _FakeClient:
    def __init__(self):
        self.orders = []

    def place_order(self, account_hash, order_spec):
        self.orders.append((account_hash, order_spec))
        return {"status": "ok"}


def _quote():
    return OptionQuote(symbol="T_100P", strike=100.0, bid=1.95, ask=2.05, days_to_expiration=3, delta=-0.5)


def test_executor_dry_run_does_not_place():
    lines = []
    ex = Executor(
        execution=ExecutionParams(mode=ExecutionMode.DRY_RUN),
        risk=RiskManager(RiskLimits(max_contracts=1, max_trades_per_day=3, daily_loss_limit=250.0)),
        signal=SignalParams(),
        log_fn=lines.append,
    )
    result = ex.enter_put(_quote(), 1)
    assert result is not None
    assert result.placed is False
    assert ex.risk.trades_today == 1  # dry-run still counts toward the budget
    assert any("not sent" in line for line in lines)


def test_executor_paper_places_order():
    client = _FakeClient()
    ex = Executor(
        client=client,
        account_hash="HASH",
        execution=ExecutionParams(mode=ExecutionMode.PAPER),
        risk=RiskManager(RiskLimits(max_contracts=1, max_trades_per_day=3, daily_loss_limit=250.0)),
        signal=SignalParams(),
        log_fn=lambda _l: None,
    )
    result = ex.enter_put(_quote(), 1)
    assert result.placed is True
    assert len(client.orders) == 1
    assert client.orders[0][0] == "HASH"


def test_executor_risk_blocks_entry():
    ex = Executor(
        execution=ExecutionParams(mode=ExecutionMode.DRY_RUN),
        risk=RiskManager(RiskLimits(max_contracts=1, max_trades_per_day=0, daily_loss_limit=250.0)),
        signal=SignalParams(),
        log_fn=lambda _l: None,
    )
    assert ex.enter_put(_quote(), 1) is None  # daily trade limit == 0


def test_executor_exit_builds_stc():
    client = _FakeClient()
    ex = Executor(
        client=client,
        account_hash="HASH",
        execution=ExecutionParams(mode=ExecutionMode.PAPER),
        signal=SignalParams(),
        log_fn=lambda _l: None,
    )
    result = ex.exit_put(_quote(), 1)
    assert result.plan.instruction == "SELL_TO_CLOSE"
    assert result.placed is True


def test_order_plan_to_schwab_order_builds():
    plan = OrderPlan("BUY_TO_OPEN", "T_100P", 1, 2.02)
    spec = plan.to_schwab_order()  # exercises the lazy schwab-py builder
    assert isinstance(spec, dict)
    with pytest.raises(ValueError):
        OrderPlan("BAD", "T_100P", 1, 2.0).to_schwab_order()
