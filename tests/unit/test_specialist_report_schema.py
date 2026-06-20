# tests/unit/test_specialist_report_schema.py
# RPAR Epic #1262, Task V0 (#1263) — SpecialistReport schema home + re-export
# + serializer byte-identity. These tests are the dormancy guarantee for V0:
# the +18 additive fields must NOT change the built report's serialized DTO.
"""TDD for RPAR-V0.

The schema is extracted to ``core.specialist.report`` and re-exported from the
legacy path ``core.stock_specialist``. Eighteen additive fields are introduced
in two groups:

  * Group A (11) — the serializer ALREADY reads these via ``getattr(r, name,
    <fallback>)``; each new default equals that fallback, so the emitted DTO is
    unchanged.
  * Group B (7)  — the serializer does NOT read these today; they are byte-
    neutral because unread (visibility is a separate follow-up, Task T-SER).
"""

from core.engine.api_routes import _serialize_specialist_report
from core.specialist.report import SpecialistReport
from core.stock_specialist import SpecialistReport as LegacySpecialistReport

# Group A — serializer-mirrored. Default == the serializer's getattr fallback.
GROUP_A_DEFAULTS = {
    "about": "",
    "edge_signals": [],
    "investment_thesis": "",
    "bull_case": "",
    "bear_case": "",
    "headlines": [],
    "insider_trades_total": None,
    "signal_quality": "llm_only",
    "walkforward_ic": None,
    "walkforward_sharpe": None,
    "ml_attention_features": [],
}

# Group B — bundle-internal, NOT read by today's serializer.
GROUP_B_DEFAULTS = {
    "pros": [],
    "cons": [],
    "summary": "",
    "data_quality": 1.0,
    "degraded": False,
    "rsi_14": None,
    "macd_signal": None,
}


def test_reexport_identity():
    """`core.stock_specialist.SpecialistReport` IS the package class — existing
    importers (`core/specialist_registry.py`) keep working unchanged."""
    assert LegacySpecialistReport is SpecialistReport


def test_all_18_new_fields_exist_with_expected_defaults():
    r = SpecialistReport(symbol="AAPL")
    for name, expected in {**GROUP_A_DEFAULTS, **GROUP_B_DEFAULTS}.items():
        assert hasattr(r, name), f"new field missing from schema: {name}"
        assert (
            getattr(r, name) == expected
        ), f"{name} default {getattr(r, name)!r} != expected {expected!r}"


def test_list_defaults_are_independent_instances():
    """Every mutable list field uses field(default_factory=...) — not a shared
    class-level mutable default."""
    list_fields = (
        "edge_signals",
        "headlines",
        "ml_attention_features",
        "pros",
        "cons",
        "reasons",
    )
    a = SpecialistReport(symbol="A")
    b = SpecialistReport(symbol="B")
    for name in list_fields:
        getattr(a, name).append("x")
    for name in list_fields:
        assert (
            getattr(b, name) == []
        ), f"{name} shares a mutable default with another instance"


def test_group_a_defaults_equal_serializer_getattr_fallback():
    """Each Group-A default must equal what the serializer falls back to today,
    so a default report serializes byte-identically (the V0 dormancy guarantee).
    """
    r = SpecialistReport(symbol="AAPL", insider_trades=[{"x": 1}, {"y": 2}])
    dto = _serialize_specialist_report("AAPL", r)
    # about: default "" -> company_summary "" -> the documented fallback string.
    assert dto["about"] == "AAPL: overview unavailable this cycle."
    assert dto["edge_signals"] == []
    assert dto["investment_thesis"] == ""
    assert dto["bull_case"] == ""
    assert dto["bear_case"] == ""
    assert dto["headlines"] == []
    assert dto["signal_quality"] == "llm_only"
    assert dto["walkforward_ic"] is None
    assert dto["walkforward_sharpe"] is None
    assert dto["ml_attention_features"] == []
    # insider_trades_total default None -> DTO count falls back to len(insider_trades).
    assert dto["insider_trades_count"] == 2


def test_group_b_fields_are_serialized_after_t_ser():
    """Task T-SER (#1266) surfaces the 7 Group-B fields in the DTO with their V0
    defaults (the deliberate, documented +7-additive-keys contract change).
    Was 'not serialized' in V0; T-SER emits them."""
    r = SpecialistReport(symbol="AAPL")
    dto = _serialize_specialist_report("AAPL", r)
    for name, expected in GROUP_B_DEFAULTS.items():
        assert name in dto, f"{name} must be serialized after T-SER"
        assert dto[name] == expected, f"{name} default {dto[name]!r} != {expected!r}"


def test_default_report_serializes_byte_identical_to_pre_v0_baseline():
    """A fully-default report serializes to the pre-V0 DTO PLUS the 7 additive
    Group-B keys from Task T-SER (#1266) at their V0 defaults. This is the
    deliberate, documented +7-key contract change (not byte-identical); every
    other key stays byte-identical to pre-T-SER."""
    r = SpecialistReport(symbol="AAPL")
    dto = _serialize_specialist_report("AAPL", r)
    dto.pop("updated_at")  # time-varying — not part of the parity assertion
    expected = {
        "symbol": "AAPL",
        "sentiment_score": 50.0,
        "recommendation": "hold",
        "confidence": 0.5,
        "escalate": False,
        "escalate_reason": "",
        "reasons": [],
        "about": "AAPL: overview unavailable this cycle.",
        "company_summary": "",
        "edge_signals": [],
        "investment_thesis": "",
        "bull_case": "",
        "bear_case": "",
        "news_summary": "",
        "headlines": [],
        "alternative_signals": "",
        "insider_trades_count": 0,
        "political_trades_count": 0,
        "material_events_count": 0,
        "reddit_mentions": 0,
        "wiki_spike": False,
        "short_interest_pct": None,
        "ml_direction": "unavailable",
        "ml_confidence": None,
        "ml_base_return_pct": None,
        "ml_bear_return_pct": None,
        "ml_bull_return_pct": None,
        "signal_quality": "llm_only",
        "walkforward_ic": None,
        "walkforward_sharpe": None,
        "ml_attention_features": [],
        # +7 additive Group-B keys (Task T-SER #1266) at their V0 defaults.
        "pros": [],
        "cons": [],
        "summary": "",
        "data_quality": 1.0,
        "degraded": False,
        "rsi_14": None,
        "macd_signal": None,
    }
    # Exact key-set contract (no stray/missing DTO keys) made explicit, then
    # full value equality. The set now includes the 7 Group-B keys (T-SER), the
    # deliberate +7-additive contract change; it still guards against any further
    # unexpected key leaking into the serialized DTO.
    assert set(dto) == set(expected)
    assert dto == expected
