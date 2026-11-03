# tests/parity/harness.py
# RPAR Epic #1262, Task V0 (#1263) — report-parity comparator (grading oracle).
"""Compare a captured *golden* bundle SpecialistReport DTO against a freshly
built Dev-Env DTO.

`compare_reports(golden, actual) -> ParityDiff` is the objective oracle every
later port-PR (T1..T6) is graded against. Comparison rules:

  * Exact fields (numeric / categorical / counts / booleans): must match
    EXACTLY. ``sentiment_score`` is exact BY DESIGN — the P0-1 finance rule
    forbids treating ``0.0`` (maximally bearish) as equal to the ``50.0``
    neutral default, so no falsy/`or` logic is used anywhere here.
  * Prose fields (about / thesis / bull_case / ...): structural only — here
    "fuzzy/structural" means emptiness parity (both empty or both non-empty),
    not token-similarity scoring. LLM wording is non-deterministic across
    engines, so verbatim equality is the wrong bar; a later task can graduate
    this to graded similarity if needed.
  * List fields (reasons / edge_signals / headlines / ...): length parity.

Fields not in any set are ignored (forward-compatible: later DTO keys do not
break an older comparator). This module is test infrastructure, imported by
``test_harness.py`` via pytest's prepend import mode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

# Sentinel distinct from any real value (so `None` vs missing are different).
_MISSING = object()

# Exact-match fields. sentiment_score is here deliberately (P0-1: compared as a
# real number; 0.0 != 50.0 is a genuine divergence, never masked).
_EXACT_FIELDS = frozenset(
    {
        "symbol",
        "sentiment_score",
        "recommendation",
        "confidence",
        "escalate",
        "signal_quality",
        "walkforward_ic",
        "walkforward_sharpe",
        "ml_direction",
        "ml_confidence",
        "ml_base_return_pct",
        "ml_bear_return_pct",
        "ml_bull_return_pct",
        "insider_trades_count",
        "political_trades_count",
        "material_events_count",
        "reddit_mentions",
        "wiki_spike",
        "short_interest_pct",
    }
)

# Free-text fields compared structurally (non-empty parity), not verbatim.
_PROSE_FIELDS = frozenset(
    {
        "about",
        "company_summary",
        "investment_thesis",
        "bull_case",
        "bear_case",
        "news_summary",
        "alternative_signals",
        "escalate_reason",
    }
)

# List fields compared by length parity.
_LIST_FIELDS = frozenset(
    {"reasons", "edge_signals", "headlines", "ml_attention_features"}
)


@dataclass
class FieldDivergence:
    """A single field-level disagreement between golden and actual."""

    field: str
    kind: str  # "missing" | "value" | "emptiness" | "length"
    golden: Any
    actual: Any

    def __str__(self) -> str:
        return (
            f"[{self.kind}] {self.field}: "
            f"golden={self.golden!r} actual={self.actual!r}"
        )


@dataclass
class ParityDiff:
    """Result of `compare_reports`. ``is_parity`` is True iff no divergences."""

    divergences: List[FieldDivergence] = field(default_factory=list)

    @property
    def is_parity(self) -> bool:
        return not self.divergences

    def summary(self) -> str:
        if self.is_parity:
            return "PARITY (no divergences)"
        lines = "\n".join(f"  - {d}" for d in self.divergences)
        return f"{len(self.divergences)} divergence(s):\n{lines}"


def _is_empty(value: Any) -> bool:
    """Empty = absent content. Note 0 / 0.0 / False are NOT empty (they carry
    meaning); this helper is only applied to prose (str) fields."""
    return value is None or value == ""


def compare_reports(golden: Dict[str, Any], actual: Dict[str, Any]) -> ParityDiff:
    """Compare two serialized SpecialistReport DTOs. Pure / deterministic."""
    diff = ParityDiff()

    for name in _EXACT_FIELDS:
        g = golden.get(name, _MISSING)
        a = actual.get(name, _MISSING)
        if g is _MISSING and a is _MISSING:
            continue  # both absent -> the two DTOs agree (no divergence)
        if g is _MISSING or a is _MISSING:
            diff.divergences.append(FieldDivergence(name, "missing", g, a))
        elif g != a:
            # Direct inequality — 0.0 vs 50.0 IS a divergence (P0-1 honoured).
            diff.divergences.append(FieldDivergence(name, "value", g, a))

    for name in _PROSE_FIELDS:
        g = golden.get(name, _MISSING)
        a = actual.get(name, _MISSING)
        if g is _MISSING and a is _MISSING:
            continue  # both absent -> agree
        if g is _MISSING or a is _MISSING:
            diff.divergences.append(FieldDivergence(name, "missing", g, a))
        elif _is_empty(g) != _is_empty(a):
            diff.divergences.append(FieldDivergence(name, "emptiness", g, a))

    for name in _LIST_FIELDS:
        g = golden.get(name, _MISSING)
        a = actual.get(name, _MISSING)
        if g is _MISSING and a is _MISSING:
            continue  # both absent -> the two DTOs agree
        if g is _MISSING or a is _MISSING:
            diff.divergences.append(FieldDivergence(name, "missing", g, a))
            continue
        if not isinstance(g, (list, tuple)) or not isinstance(a, (list, tuple)):
            # A non-list value (e.g. bundle schema drift to a bare string) is
            # itself a divergence. Surface the REAL offending value, never a
            # len()-masked None — this is a grading oracle and the drifted value
            # is exactly what a debugging engineer needs to see (M-1).
            diff.divergences.append(FieldDivergence(name, "type", g, a))
            continue
        if len(g) != len(a):
            diff.divergences.append(FieldDivergence(name, "length", len(g), len(a)))

    return diff


__all__ = ["FieldDivergence", "ParityDiff", "compare_reports"]
