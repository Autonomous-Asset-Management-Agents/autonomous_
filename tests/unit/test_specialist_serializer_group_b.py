# tests/unit/test_specialist_serializer_group_b.py
# RPAR Epic #1262, Task T-SER (#1266) - emit the 7 Group-B keys in the
# /specialist-reports DTO: pros, cons, summary, data_quality, degraded,
# rsi_14, macd_signal. Plus the confidence 0.0-fallback hardening (P0-1).
#
# Additive contract change: the DTO grows by exactly 7 keys; all other keys stay
# byte-identical. data_quality/confidence 0.0 must NOT be masked to a default
# (0.0 is a legitimate value - financial edge-case governance, P0-1).

from core.engine.api_routes import _serialize_specialist_report
from core.specialist.report import SpecialistReport

GROUP_B_KEYS = (
    "pros",
    "cons",
    "summary",
    "data_quality",
    "degraded",
    "rsi_14",
    "macd_signal",
)


def test_group_b_keys_present_with_exact_values():
    r = SpecialistReport(
        symbol="AAPL",
        pros=["x"],
        cons=["y"],
        summary="s",
        data_quality=0.5,
        degraded=True,
        rsi_14=55.5,
        macd_signal="bullish",
    )
    dto = _serialize_specialist_report("AAPL", r)
    for key in GROUP_B_KEYS:
        assert key in dto, f"Group-B key {key} missing from DTO"
    assert dto["pros"] == ["x"]
    assert dto["cons"] == ["y"]
    assert dto["summary"] == "s"
    assert dto["data_quality"] == 0.5
    assert dto["degraded"] is True
    assert dto["rsi_14"] == 55.5
    assert dto["macd_signal"] == "bullish"


def test_data_quality_zero_not_masked():
    """P0-1: data_quality 0.0 is legitimate (maximally-low integrity) and must
    NOT be `or`-masked to the 1.0 default."""
    r = SpecialistReport(symbol="AAPL", data_quality=0.0)
    dto = _serialize_specialist_report("AAPL", r)
    assert dto["data_quality"] == 0.0


def test_confidence_zero_not_masked():
    """P0-1: confidence 0.0 must survive as 0.0, not be `or`-masked away."""
    r = SpecialistReport(symbol="AAPL", confidence=0.0)
    dto = _serialize_specialist_report("AAPL", r)
    assert dto["confidence"] == 0.0


def test_confidence_absent_falls_back_to_zero():
    """When confidence is absent on the object, the DTO preserves the historical
    0.0 fallback (NOT None) so the default report stays byte-identical."""

    class _Bare:
        symbol = "AAPL"

    dto = _serialize_specialist_report("AAPL", _Bare())
    assert dto["confidence"] == 0.0


def test_summary_capped_at_1500():
    long_summary = "z" * 5000
    r = SpecialistReport(symbol="AAPL", summary=long_summary)
    dto = _serialize_specialist_report("AAPL", r)
    assert len(dto["summary"]) == 1500


def test_rsi_14_rounded_and_macd_passthrough():
    r = SpecialistReport(symbol="AAPL", rsi_14=55.555, macd_signal="bearish")
    dto = _serialize_specialist_report("AAPL", r)
    assert dto["rsi_14"] == 55.6  # _round_or_none(..., 1)
    assert dto["macd_signal"] == "bearish"


def test_group_b_defaults_in_dto():
    """A default report now emits the 7 Group-B keys at their V0 defaults."""
    r = SpecialistReport(symbol="AAPL")
    dto = _serialize_specialist_report("AAPL", r)
    assert dto["pros"] == []
    assert dto["cons"] == []
    assert dto["summary"] == ""
    assert dto["data_quality"] == 1.0
    assert dto["degraded"] is False
    assert dto["rsi_14"] is None
    assert dto["macd_signal"] is None
