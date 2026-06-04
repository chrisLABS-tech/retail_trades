"""Cross-Asset Signal Options Scalper.

Phase 0 (setup & data gate) and Phase 1 (signal validation) form the GO/NO-GO
gate that must be passed before trading live. Phase 3 (live streaming monitor)
and Phase 5 (gated put execution) are also implemented: the monitor is
read-only, and execution defaults to ``ExecutionMode.DRY_RUN`` (logs only) so no
orders are ever sent until explicitly armed.
"""

__all__ = ["config"]
