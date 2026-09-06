# core/data_integrity/guard.py
# RPAR Epic #1262, Task T6a (#1268, Closes #1270) - pure data-integrity guard.
"""Pure, deterministic assessment of the gathered raw-data quality.

``assess(gathered, *, config=DEFAULT_THRESHOLDS, now=None) -> DataIntegrityResult``

Design constraints (see TASK_T6a_data_integrity_implementation_plan.md):

* **Pure & deterministic** - no network, LLM, GPU, or implicit clock. ``now`` is
  injectable; it is only consulted when the (today-never-set) optional
  ``gathered["_source_timestamps"]`` recency hook is present, so the default path
  needs no clock at all.
* **Read-only** - never mutates ``gathered``; ``missing_sources`` is always a fresh
  list.
* **Decision-neutral** - produces only the two display fields' inputs
  (``data_quality`` / ``degraded``) plus a ``skip_llm`` signal. It never returns or
  touches a score / recommendation / reasons.

Recency note (V0): the bundle's fetchers do not (yet) attach per-element
timestamps to ``gathered`` (``recent_headlines`` are bare strings). Computing real
staleness windows would require changing the fetchers and the ``gathered`` shape -
the very signals that drive ``_build_report``'s scoring - which is out of T6a scope
(it would risk a decision delta). So V0 measures quality via a **presence/count
heuristic** (which sources returned data), with ``now`` + the ``_source_timestamps``
hook kept as a forward-compatible seam for a later fetcher-recency epic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class DataIntegrityThresholds:
    """Heuristic thresholds for the presence-based quality score.

    These are display-only UX heuristics (not compliance-critical magic numbers):
    they shade a card as "degraded" or skip a non-decision LLM synthesis. Rationale
    is inline rather than an ADR because no capital / order path reads them.
    """

    # data_quality at or below this -> the card is flagged ``degraded`` (display only).
    # 0.5 = "at least half of the weighted source signal is missing".
    degraded_below: float = 0.5
    # data_quality at or below this -> hard fail: skip the (non-decision) LLM synthesis
    # and fall back to the engine's existing V0-default synthesis. 0.2 ~= "primary
    # intelligence is entirely absent and almost nothing came back".
    hard_fail_below: float = 0.2

    # Primary intelligence sources (EDGAR filings + news). Carry the bulk of the
    # weight: their absence is what makes a card untrustworthy.
    primary_sources: Tuple[str, ...] = (
        "insider_trades",
        "material_events",
        "activist_stakes",
        "political_trades",
        "recent_headlines",
    )
    # Secondary / sentiment sources. Nice-to-have; their absence lowers quality only
    # marginally and never on its own degrades a card.
    secondary_sources: Tuple[str, ...] = (
        "wiki_spike",
        "reddit_mentions_24h",
        "short_interest_pct",
        "google_trend_score",
    )
    # Combined weight of all primaries vs all secondaries (sum to 1.0).
    primary_weight: float = 0.7
    secondary_weight: float = 0.3


DEFAULT_THRESHOLDS = DataIntegrityThresholds()


@dataclass(frozen=True)
class DataIntegrityResult:
    """Outcome of :func:`assess`. ``data_quality``  in  [0, 1]; ``skip_llm`` asks the
    caller to skip the (non-decision) LLM synthesis and reuse the V0 default."""

    data_quality: float
    degraded: bool
    skip_llm: bool
    missing_sources: List[str] = field(default_factory=list)


def _has_data(value: Any) -> bool:
    """A source 'has data' iff the fetcher returned a real value.

    A source is "missing" ONLY when the fetcher produced nothing: ``None`` or an
    empty collection/string (``[]`` / ``()`` / ``{}`` / ``""``). A boolean
    ``False`` (e.g. ``wiki_spike`` with no spike, the normal case) and a numeric
    ``0`` / ``0.0`` (e.g. ``short_interest_pct`` of a stock with 0% short
    interest, or ``google_trend_score`` of 0) are **valid fetched values**, not
    missing data.

    P0-1 (RPAR T6a review #1313 F-01): ``0.0`` / ``False`` are legitimate values
    and must never be treated as absent - otherwise ``data_quality`` could never
    reach ``1.0`` under normal conditions (``wiki_spike`` is almost always
    ``False``) and valid zero-like reads would be falsely flagged degraded."""
    if value is None:
        return False
    if isinstance(value, (list, tuple, dict, str)):
        return len(value) > 0
    # Any non-None scalar - bool (incl. False), int/float (incl. 0/0.0), or any
    # other object - is a real fetched value. Only None / empty collections miss.
    return True


def assess(
    gathered: Dict[str, Any],
    *,
    config: DataIntegrityThresholds = DEFAULT_THRESHOLDS,
    now: Optional[datetime] = None,
) -> DataIntegrityResult:
    """Assess the freshness/completeness of ``gathered`` (read-only, deterministic).

    Returns a :class:`DataIntegrityResult` whose ``data_quality``  in  [0, 1] is a
    weighted fraction of the sources that returned data. ``degraded`` /
    ``skip_llm`` are derived from the configured thresholds. ``gathered`` is never
    mutated; ``missing_sources`` is always a new list.

    ``now`` is accepted for determinism and forward-compatibility: it is consulted
    only when the optional ``gathered["_source_timestamps"]`` recency hook is
    present (never set today), so the default path uses no clock.
    """
    # `now` is a forward-compat seam for real per-source recency; today the hook is
    # never populated, so we read it defensively without mutating `gathered`.
    _source_timestamps = gathered.get("_source_timestamps")  # noqa: F841 (reserved)
    _ = now  # explicitly unused on the V0 presence-only path (kept for the seam)

    missing_sources: List[str] = []

    def _score_group(names: Tuple[str, ...]) -> float:
        if not names:
            return 0.0
        present = 0
        for name in names:
            if _has_data(gathered.get(name)):
                present += 1
            else:
                missing_sources.append(name)
        return present / len(names)

    primary_fraction = _score_group(config.primary_sources)
    secondary_fraction = _score_group(config.secondary_sources)

    data_quality = (
        config.primary_weight * primary_fraction
        + config.secondary_weight * secondary_fraction
    )
    # Clamp into [0, 1] against any float drift (weights already sum to 1.0).
    data_quality = max(0.0, min(1.0, data_quality))

    degraded = data_quality <= config.degraded_below
    skip_llm = data_quality <= config.hard_fail_below

    return DataIntegrityResult(
        data_quality=data_quality,
        degraded=degraded,
        skip_llm=skip_llm,
        missing_sources=missing_sources,
    )
