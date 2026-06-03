"""Pre-trade risk gate (Phase 5).

Every order must pass :meth:`RiskManager.check` first. The checks are pure
(given the manager's tracked state) and fail-closed: a violated limit blocks the
order. State (trades taken today, realized P&L, kill switch) is updated only as
fills are recorded, keeping the decision logic independent of any broker call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from scalper.config import RiskLimits


@dataclass(frozen=True)
class RiskDecision:
    """Outcome of a pre-trade risk check."""

    allowed: bool
    reason: str = ""


class RiskManager:
    """Enforces contract size, daily trade count, daily loss, and a kill switch.

    Args:
        limits: The configured :class:`~scalper.config.RiskLimits`.
    """

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self._trades_today = 0
        self._realized_pnl = 0.0
        self._killed = False

    @property
    def trades_today(self) -> int:
        return self._trades_today

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def killed(self) -> bool:
        return self._killed

    def kill(self) -> None:
        """Trip the kill switch; all subsequent checks fail until reset."""
        self._killed = True

    def reset_day(self) -> None:
        """Reset per-day counters (call at the start of a session)."""
        self._trades_today = 0
        self._realized_pnl = 0.0

    def check(self, contracts: int) -> RiskDecision:
        """Decide whether an entry of ``contracts`` is permitted right now."""
        if self._killed:
            return RiskDecision(False, "kill switch engaged")
        if contracts < 1:
            return RiskDecision(False, "contracts must be >= 1")
        if contracts > self.limits.max_contracts:
            return RiskDecision(
                False,
                f"contracts {contracts} exceeds max {self.limits.max_contracts}",
            )
        if self._trades_today >= self.limits.max_trades_per_day:
            return RiskDecision(
                False,
                f"daily trade limit reached ({self.limits.max_trades_per_day})",
            )
        if self._realized_pnl <= -abs(self.limits.daily_loss_limit):
            return RiskDecision(
                False,
                f"daily loss limit hit ({self.limits.daily_loss_limit})",
            )
        return RiskDecision(True, "ok")

    def record_entry(self) -> None:
        """Count a new entry against the daily trade budget."""
        self._trades_today += 1

    def record_pnl(self, pnl: float) -> None:
        """Fold a realized P&L amount (USD) into the daily total."""
        self._realized_pnl += float(pnl)
        if self._realized_pnl <= -abs(self.limits.daily_loss_limit):
            self._killed = True


__all__ = ["RiskManager", "RiskDecision"]
