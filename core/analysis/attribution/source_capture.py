"""
source_capture.py — DORMANT #2113 online cross-check adapter (RTR-0 Inc 2).

Option C of the #1947 plan: once #2113 (decision-outcome capture) lands a
durable ``decision_outcomes`` table (``votes_json`` + realised forward
returns per decision), this adapter maps those rows onto the SAME
``votes_df`` + ``fwd_df`` schema the replay produces, so ``metrics.py`` runs
unchanged on real captured decisions and the offline baseline gets its
online convergence/divergence cross-check.

Until #2113 is merged there is NOTHING to read — and this module says so
loudly instead of fabricating a frame (plan Gherkin §7: fails cleanly,
without touching the replay path).
"""

from __future__ import annotations

from typing import Any


class CaptureSourceUnavailable(NotImplementedError):
    """Raised while the #2113 decision-outcome capture does not exist yet."""


def load_capture_votes(*_: Any, **__: Any):
    """Load captured decision outcomes as (votes_df, fwd_df) — DORMANT.

    Raises
    ------
    CaptureSourceUnavailable
        Always, until #2113 merges a durable ``decision_outcomes`` source.
    """
    raise CaptureSourceUnavailable(
        "--source capture requires the #2113 decision-outcome capture "
        "(durable decision_outcomes with votes_json + realised returns), "
        "which is not merged yet. Use --source replay; this adapter goes "
        "live in RTR-0 Increment 2 (plan #1947 §11)."
    )
