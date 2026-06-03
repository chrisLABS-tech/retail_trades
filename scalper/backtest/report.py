"""Printable per-target signal-validation report (Phase 1).

Renders the Phase 1 GO/NO-GO summary as text. The headline caveat — that all
moves are on the *underlying*, not the option, and that spread can erase the
edge — is printed prominently so underlying edge is never mistaken for net P&L.
"""

from __future__ import annotations

import math

from scalper.backtest.engine import TargetResult
from scalper.backtest.stats import (
    TargetStats,
    analyze_target,
    spread_adjusted_edge,
)
from scalper.config import SignalParams


def _pct(x: float) -> str:
    """Format a fraction as a signed percentage, or 'n/a' for NaN."""
    return "n/a" if math.isnan(x) else f"{x * 100:+.3f}%"


def _rate(x: float) -> str:
    """Format a fraction as an unsigned percentage rate, or 'n/a' for NaN."""
    return "n/a" if math.isnan(x) else f"{x * 100:.1f}%"


def _fmt_p(p: float | None) -> str:
    return "n/a" if p is None else f"{p:.4f}"


def render_target_report(
    stats: TargetStats,
    params: SignalParams,
    *,
    assumed_spread_pct: float,
) -> str:
    """Render a single target's validation block as text."""
    cond = stats.conditioned
    base = stats.baseline
    sig = stats.significance

    edge = spread_adjusted_edge(cond.mean, assumed_spread_pct)
    survives = (edge == edge) and edge > 0

    lines = [
        f"── Target: {stats.target} "
        f"(signal: {params.min_red_count}/{params.lookback_minutes}m red, "
        f"hold {params.hold_minutes}m) " + "─" * 8,
        f"  fires (n):        {cond.n}    baseline bars (n): {base.n}",
        f"  hit rate (<= {_pct(stats.move_threshold)}):",
        f"      signal:   {_rate(cond.hit_rate)}",
        f"      baseline: {_rate(base.hit_rate)}",
        "  forward move (underlying):",
        f"      signal   mean {_pct(cond.mean)}  median {_pct(cond.median)}"
        f"  [p25 {_pct(cond.p25)}, p75 {_pct(cond.p75)}]",
        f"      baseline mean {_pct(base.mean)}  median {_pct(base.median)}",
        "  significance (signal mean vs baseline mean):",
        f"      mean diff {_pct(sig.mean_diff)}"
        f"   t-test p={_fmt_p(sig.t_pvalue)}"
        f"   bootstrap p={_fmt_p(sig.bootstrap_pvalue)}",
        f"      bootstrap 95% CI of diff: "
        f"[{_pct(sig.bootstrap_ci_low)}, {_pct(sig.bootstrap_ci_high)}]",
        f"  spread-haircut check (assume {assumed_spread_pct:.0f}% round-trip):",
        f"      favorable move - spread = {_pct(edge)} "
        + ("→ edge MAY survive spread" if survives
           else "→ edge does NOT survive spread (NO-GO signal)"),
    ]
    return "\n".join(lines)


_HEADER = "\n".join(
    [
        "=" * 72,
        "  PHASE 1 SIGNAL VALIDATION REPORT",
        "  Basket red → buy short-dated puts on target; measure forward move.",
        "=" * 72,
    ]
)

_CAVEAT = "\n".join(
    [
        "!" * 72,
        "  CAVEAT: All forward moves above are on the UNDERLYING, used as a",
        "  proxy because intraday option history is unavailable. A positive",
        "  underlying edge is NECESSARY BUT NOT SUFFICIENT. Option bid/ask",
        "  spreads (often 8-12% of premium on thin names) and slippage can",
        "  erase it. Treat any edge as a CEILING, not net P&L. Do not proceed",
        "  to live infrastructure unless the edge clearly survives the spread",
        "  haircut above.",
        "!" * 72,
    ]
)


def render_report(
    results: dict[str, TargetResult],
    params: SignalParams,
    *,
    move_threshold: float,
    assumed_spread_pct: float,
    n_boot: int = 10_000,
    seed: int = 12345,
) -> str:
    """Render the full multi-target Phase 1 report as text.

    Args:
        results: Output of :func:`scalper.backtest.engine.run_backtest`.
        params: Signal parameters used.
        move_threshold: Downside threshold for the hit-rate metric.
        assumed_spread_pct: Round-trip option spread for the haircut check.
        n_boot: Bootstrap resamples for the significance test.
        seed: RNG seed for reproducibility.
    """
    blocks = [_HEADER]
    if not results:
        blocks.append("  (no targets / no data)")
    for target, result in results.items():
        stats = analyze_target(
            result, move_threshold, n_boot=n_boot, seed=seed
        )
        blocks.append(
            render_target_report(
                stats, params, assumed_spread_pct=assumed_spread_pct
            )
        )
    blocks.append(_CAVEAT)
    return "\n\n".join(blocks)


__all__ = ["render_report", "render_target_report"]
