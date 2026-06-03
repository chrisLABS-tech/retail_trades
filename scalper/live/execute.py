"""Put execution: ATM selection, BTO entry, STC exit (Phase 5).

The flow the user asked for — *buy puts, buy-to-open, sell-to-close* — built
behind a strict safety ramp:

* :func:`select_atm_put` picks the nearest-expiry, near-ATM put from a Schwab
  option-chain payload, screened by the existing ``MAX_SPREAD_PCT`` filter.
* :class:`Executor` constructs a marketable-limit **BUY_TO_OPEN** order and,
  for the exit, a **SELL_TO_CLOSE** order, deciding the exit on whichever fires
  first: the 10-minute time-stop (``HOLD_MINUTES``) or an optional take-profit /
  stop-loss on the option mark.
* ``ExecutionMode`` gates everything: ``DRY_RUN`` (default) only logs the order,
  ``PAPER``/``LIVE`` actually call ``place_order``. The risk gate
  (:mod:`scalper.live.risk`) is consulted before every entry.

Parsing and price math are pure functions; ``schwab-py`` order builders are
imported lazily so the module imports cleanly in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from scalper.config import (
    EXECUTION,
    OPTION,
    RISK,
    ExecutionMode,
    ExecutionParams,
    OptionParams,
    RiskLimits,
    SignalParams,
)
from scalper.live.risk import RiskManager


# --------------------------------------------------------------------------- #
# Pure parsing & pricing helpers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OptionQuote:
    """A single option contract distilled to what entry/exit needs."""

    symbol: str
    strike: float
    bid: float
    ask: float
    days_to_expiration: int
    delta: Optional[float] = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


def spread_pct(bid: float, ask: float) -> Optional[float]:
    """Bid/ask spread as a percentage of mid; None if mid is non-positive."""
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def flatten_chain(
    chain_json: Mapping[str, Any],
    contract_type: str,
) -> list[OptionQuote]:
    """Flatten a Schwab option-chain payload into :class:`OptionQuote` rows.

    Args:
        chain_json: Parsed ``get_option_chain`` body (has ``putExpDateMap`` /
            ``callExpDateMap``).
        contract_type: ``"PUT"`` or ``"CALL"``.

    Returns:
        All contracts with a usable bid/ask, in payload order.
    """
    key = "putExpDateMap" if contract_type.upper() == "PUT" else "callExpDateMap"
    exp_map = chain_json.get(key) or {}
    quotes: list[OptionQuote] = []
    for _exp, strikes in exp_map.items():
        for _strike, contracts in (strikes or {}).items():
            for c in contracts or []:
                bid = c.get("bid")
                ask = c.get("ask")
                if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float))):
                    continue
                if bid <= 0 or ask <= 0:
                    continue
                quotes.append(
                    OptionQuote(
                        symbol=c.get("symbol", ""),
                        strike=float(c.get("strikePrice", 0.0)),
                        bid=float(bid),
                        ask=float(ask),
                        days_to_expiration=int(c.get("daysToExpiration", 0)),
                        delta=(
                            float(c["delta"])
                            if isinstance(c.get("delta"), (int, float))
                            else None
                        ),
                    )
                )
    return quotes


def select_atm_put(
    chain_json: Mapping[str, Any],
    spot: float,
    *,
    params: OptionParams = OPTION,
    max_spread_pct: float = 8.0,
) -> Optional[OptionQuote]:
    """Pick the nearest-expiry, nearest-ATM put passing the spread filter.

    Selection order:
      1. keep puts within ``[min_dte, max_dte]`` and ``moneyness_tolerance`` of
         spot whose spread is within ``max_spread_pct``;
      2. prefer the soonest expiry, then the strike closest to spot.

    Returns None when nothing qualifies (caller should skip the trade).
    """
    if spot <= 0:
        return None
    candidates: list[OptionQuote] = []
    for q in flatten_chain(chain_json, params.contract_type):
        if not (params.min_dte <= q.days_to_expiration <= params.max_dte):
            continue
        if abs(q.strike - spot) / spot > params.moneyness_tolerance:
            continue
        sp = spread_pct(q.bid, q.ask)
        if sp is None or sp > max_spread_pct:
            continue
        candidates.append(q)
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda q: (q.days_to_expiration, abs(q.strike - spot)),
    )


def limit_price(
    bid: float,
    ask: float,
    side: str,
    offset_pct: float,
) -> float:
    """Marketable-limit price: cross from mid toward the far touch.

    ``offset_pct`` is the fraction of the spread to cross (0 == mid, 1 == far
    touch). Buys cross **up** toward the ask; sells cross **down** toward the
    bid. The result is clamped to the [bid, ask] band and rounded to a cent.
    """
    mid = (bid + ask) / 2.0
    half = (ask - bid) / 2.0
    frac = max(0.0, min(offset_pct, 1.0))
    if side.upper() == "BUY":
        price = mid + half * frac
    else:
        price = mid - half * frac
    price = max(bid, min(price, ask))
    return round(price, 2)


def should_exit(
    entry_time_s: float,
    now_s: float,
    hold_minutes: int,
    *,
    entry_mark: Optional[float] = None,
    current_mark: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
) -> tuple[bool, str]:
    """Decide whether to sell-to-close now.

    Fires on whichever comes first: the time-stop, a take-profit, or a
    stop-loss. Returns ``(exit?, reason)``.
    """
    if take_profit_pct is not None and entry_mark and current_mark is not None and entry_mark > 0:
        if current_mark >= entry_mark * (1.0 + abs(take_profit_pct)):
            return True, "take-profit"
    if stop_loss_pct is not None and entry_mark and current_mark is not None and entry_mark > 0:
        if current_mark <= entry_mark * (1.0 - abs(stop_loss_pct)):
            return True, "stop-loss"
    if now_s - entry_time_s >= hold_minutes * 60.0:
        return True, "time-stop"
    return False, ""


# --------------------------------------------------------------------------- #
# Order plan + executor
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class OrderPlan:
    """A single-leg option order distilled to its essentials."""

    instruction: str  # BUY_TO_OPEN | SELL_TO_CLOSE
    option_symbol: str
    quantity: int
    limit_price: float

    def to_schwab_order(self) -> Any:
        """Build the schwab-py order spec (lazy import; keeps tests offline)."""
        from schwab.orders.options import (  # lazy import
            option_buy_to_open_limit,
            option_sell_to_close_limit,
        )

        price = f"{self.limit_price:.2f}"
        if self.instruction == "BUY_TO_OPEN":
            builder = option_buy_to_open_limit(self.option_symbol, self.quantity, price)
        elif self.instruction == "SELL_TO_CLOSE":
            builder = option_sell_to_close_limit(self.option_symbol, self.quantity, price)
        else:  # pragma: no cover - guarded by construction
            raise ValueError(f"unsupported instruction: {self.instruction}")
        return builder.build()


@dataclass
class OrderResult:
    """What happened when an :class:`OrderPlan` was submitted."""

    plan: OrderPlan
    mode: ExecutionMode
    placed: bool
    detail: str = ""


class Executor:
    """Builds and (mode-permitting) places BTO/STC put orders.

    Args:
        client: Authenticated schwab-py client (or duck-typed stand-in). Only
            used in PAPER/LIVE mode; DRY_RUN never touches it.
        account_hash: Schwab account hash for ``place_order``. Required for
            PAPER/LIVE.
        risk: Pre-trade risk gate.
        execution: Execution params (mode, limit offset, TP/SL).
        option: Option-selection params.
        signal: Signal params (provides ``hold_minutes`` for the time-stop).
        max_spread_pct: Spread filter for selection.
        log_fn: Where order lines are logged (defaults to ``print``).
    """

    def __init__(
        self,
        *,
        client: Any = None,
        account_hash: Optional[str] = None,
        risk: Optional[RiskManager] = None,
        execution: ExecutionParams = EXECUTION,
        option: OptionParams = OPTION,
        signal: Optional[SignalParams] = None,
        limits: RiskLimits = RISK,
        max_spread_pct: float = 8.0,
        log_fn: Any = print,
    ) -> None:
        self.client = client
        self.account_hash = account_hash
        self.risk = risk or RiskManager(limits)
        self.execution = execution
        self.option = option
        self.signal = signal or SignalParams()
        self.max_spread_pct = max_spread_pct
        self.log_fn = log_fn

    def build_entry(self, quote: OptionQuote, contracts: int) -> OrderPlan:
        """Construct a BUY_TO_OPEN limit plan for ``quote``."""
        price = limit_price(quote.bid, quote.ask, "BUY", self.execution.limit_offset_pct)
        return OrderPlan("BUY_TO_OPEN", quote.symbol, contracts, price)

    def build_exit(self, quote: OptionQuote, contracts: int) -> OrderPlan:
        """Construct a SELL_TO_CLOSE limit plan for ``quote``."""
        price = limit_price(quote.bid, quote.ask, "SELL", self.execution.limit_offset_pct)
        return OrderPlan("SELL_TO_CLOSE", quote.symbol, contracts, price)

    def _submit(self, plan: OrderPlan) -> OrderResult:
        """Place (or log) an order according to the execution mode."""
        line = (
            f"[{self.execution.mode.value}] {plan.instruction} "
            f"{plan.quantity}x {plan.option_symbol} @ {plan.limit_price:.2f}"
        )
        if self.execution.mode is ExecutionMode.DRY_RUN:
            self.log_fn(line + " (not sent)")
            return OrderResult(plan, self.execution.mode, placed=False, detail="dry-run")

        if self.client is None or self.account_hash is None:
            raise RuntimeError(
                "PAPER/LIVE execution requires a client and account_hash."
            )
        order_spec = plan.to_schwab_order()
        self.client.place_order(self.account_hash, order_spec)
        self.log_fn(line + " (placed)")
        return OrderResult(plan, self.execution.mode, placed=True, detail="placed")

    def enter_put(self, quote: OptionQuote, contracts: int) -> Optional[OrderResult]:
        """Risk-check, then build and submit a BTO put. None if risk blocks."""
        decision = self.risk.check(contracts)
        if not decision.allowed:
            self.log_fn(f"[risk] entry blocked: {decision.reason}")
            return None
        plan = self.build_entry(quote, contracts)
        result = self._submit(plan)
        if result.placed or self.execution.mode is ExecutionMode.DRY_RUN:
            self.risk.record_entry()
        return result

    def exit_put(self, quote: OptionQuote, contracts: int) -> OrderResult:
        """Build and submit a SELL_TO_CLOSE for an open put."""
        return self._submit(self.build_exit(quote, contracts))


__all__ = [
    "OptionQuote",
    "OrderPlan",
    "OrderResult",
    "Executor",
    "flatten_chain",
    "select_atm_put",
    "spread_pct",
    "limit_price",
    "should_exit",
]
