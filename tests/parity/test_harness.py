# tests/parity/test_harness.py
# RPAR Epic #1262, Task V0 (#1263) — parity comparator (the grading oracle).
"""TDD for the report-parity comparator.

`compare_reports(golden, actual)` is the objective oracle every later port-PR
(T1..T6) is graded against. The two behaviours pinned here:

  1. It detects an injected numeric/categorical mismatch.
  2. It honours the P0-1 finance rule: a ``sentiment_score`` of ``0.0`` is a
     real, maximally-bearish value and must NEVER be treated as equal to the
     ``50.0`` neutral default.

`harness.py` lives in this directory and is imported via pytest's prepend
import mode (this directory intentionally has no __init__.py, matching the
repo's tests/unit layout, so the sibling module resolves on sys.path).
"""

import json
from pathlib import Path

from harness import ParityDiff, compare_reports


def _base_dto(**overrides):
    """A minimal valid serialized SpecialistReport DTO."""
    dto = {
        "symbol": "AAPL",
        "sentiment_score": 62.0,
        "recommendation": "buy",
        "confidence": 0.8,
        "escalate": False,
        "about": "Apple Inc. — consumer hardware.",
        "investment_thesis": "Strong services growth.",
        "bull_case": "Margins expanding.",
        "bear_case": "China exposure.",
        "edge_signals": ["insider_buy"],
        "headlines": [{"t": "x"}],
        "reasons": ["a", "b"],
        "signal_quality": "llm_only",
        "walkforward_ic": None,
        "insider_trades_count": 2,
    }
    dto.update(overrides)
    return dto


def test_identical_reports_are_parity():
    diff = compare_reports(_base_dto(), _base_dto())
    assert isinstance(diff, ParityDiff)
    assert diff.is_parity, diff.summary()
    assert diff.divergences == []


def test_injected_numeric_mismatch_is_detected():
    golden = _base_dto(sentiment_score=62.0)
    actual = _base_dto(sentiment_score=41.0)
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    fields = {d.field for d in diff.divergences}
    assert "sentiment_score" in fields


def test_categorical_mismatch_is_detected():
    diff = compare_reports(
        _base_dto(recommendation="buy"), _base_dto(recommendation="sell")
    )
    assert not diff.is_parity
    assert "recommendation" in {d.field for d in diff.divergences}


def test_p0_1_zero_sentiment_is_not_neutral():
    """0.0 (maximally bearish) vs 50.0 (neutral) MUST be flagged as a divergence
    — the comparator must not use any `or`/falsy logic that masks 0.0."""
    golden = _base_dto(sentiment_score=0.0)
    actual = _base_dto(sentiment_score=50.0)
    diff = compare_reports(golden, actual)
    assert (
        not diff.is_parity
    ), "0.0 was silently treated as equal to 50.0 (P0-1 violation)"
    assert "sentiment_score" in {d.field for d in diff.divergences}


def test_p0_1_zero_equals_zero_is_parity():
    """The flip side: 0.0 vs 0.0 IS parity (no false positive from the rule)."""
    diff = compare_reports(
        _base_dto(sentiment_score=0.0), _base_dto(sentiment_score=0.0)
    )
    assert diff.is_parity, diff.summary()


def test_prose_compared_structurally_not_verbatim():
    """Free-text prose differs across engines (non-deterministic LLM); only
    presence/emptiness parity is required, not identical wording."""
    golden = _base_dto(bull_case="Margins expanding into FY27.")
    actual = _base_dto(bull_case="Gross margin tailwind through services mix.")
    diff = compare_reports(golden, actual)
    assert diff.is_parity, diff.summary()


def test_prose_emptiness_divergence_is_detected():
    golden = _base_dto(bull_case="Margins expanding.")
    actual = _base_dto(bull_case="")
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    assert "bull_case" in {d.field for d in diff.divergences}


def test_list_length_divergence_is_detected():
    golden = _base_dto(edge_signals=["insider_buy", "short_squeeze"])
    actual = _base_dto(edge_signals=["insider_buy"])
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    assert "edge_signals" in {d.field for d in diff.divergences}


def test_field_present_in_one_dto_only_is_a_divergence():
    """Schema drift: an exact-field present in golden but absent from actual
    (or vice-versa) is a real 'missing' divergence — NOT silent agreement.
    Only both-absent counts as agreement (covered implicitly elsewhere)."""
    golden = _base_dto()
    actual = _base_dto()
    del actual["walkforward_ic"]  # golden carries it (None); actual drops it
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    by_field = {d.field: d.kind for d in diff.divergences}
    assert by_field.get("walkforward_ic") == "missing"


def test_both_absent_field_is_agreement_not_divergence():
    """The flip side of the 'missing' rule: a field absent from BOTH DTOs must
    NOT be flagged (the two engines agree it is not emitted)."""
    golden = _base_dto()
    actual = _base_dto()
    # short_interest_pct is an exact-field neither base DTO carries.
    assert "short_interest_pct" not in golden
    diff = compare_reports(golden, actual)
    assert "short_interest_pct" not in {d.field for d in diff.divergences}


def test_non_list_value_in_list_field_is_type_divergence_preserving_value():
    """M-1: bundle schema-drift where a list-field holds a bare scalar must be a
    'type' divergence that SURFACES the real offending value — never a
    len()-masked None (this is the grading oracle's diagnostic)."""
    golden = _base_dto(edge_signals="insider_buy")  # drifted to a bare string
    actual = _base_dto(edge_signals=["insider_buy"])
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    d = next(x for x in diff.divergences if x.field == "edge_signals")
    assert d.kind == "type"
    assert d.golden == "insider_buy"  # REAL value preserved, not None
    assert d.actual == ["insider_buy"]


def test_list_field_present_in_one_dto_only_is_missing_divergence():
    golden = _base_dto(edge_signals=["x"])
    actual = _base_dto()
    del actual["edge_signals"]
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    by_field = {d.field: d.kind for d in diff.divergences}
    assert by_field.get("edge_signals") == "missing"


def test_prose_field_present_in_one_dto_only_is_a_divergence():
    """Mi-1: prose-field missing-asymmetry — the complement of the exact-field
    test. A prose field in golden but absent from actual is a 'missing'
    divergence, not silent agreement."""
    golden = _base_dto()
    actual = _base_dto()
    del actual["about"]  # golden carries it; actual drops it
    diff = compare_reports(golden, actual)
    assert not diff.is_parity
    by_field = {d.field: d.kind for d in diff.divergences}
    assert by_field.get("about") == "missing"


def test_example_fixture_loads_and_detects_a_real_mutation():
    """The shipped example golden fixture is well-formed: it round-trips to
    parity with itself AND a mutated copy is correctly flagged (exercises the
    loader against the comparator, not just a tautological self-compare)."""
    fixture = Path(__file__).parent / "fixtures" / "example_AAPL.golden.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))
    dto = data["report"]
    assert compare_reports(dto, dict(dto)).is_parity

    mutated = dict(dto)
    mutated["recommendation"] = "sell" if dto["recommendation"] != "sell" else "buy"
    mutated["sentiment_score"] = 0.0  # flip a real bull read to maximally bearish
    diff = compare_reports(dto, mutated)
    assert not diff.is_parity
    fields = {d.field for d in diff.divergences}
    assert "recommendation" in fields
    assert "sentiment_score" in fields
