# tests/unit/test_telemetry.py
# TDD Red-Phase — OTel Metrics API (get_meter)
#
# Gherkin:
#   Given: telemetry module importiert
#   When:  get_meter() aufgerufen (mit und ohne OTel installiert)
#   Then:  gibt immer ein Objekt zurück das Counter hat + Counter.add() ist sicher

from unittest.mock import patch


# ---------------------------------------------------------------------------
# Tests für get_meter() — muss IMMER sicher sein (no-op wenn kein OTel)
# ---------------------------------------------------------------------------


def test_get_meter_returns_object():
    """get_meter() gibt immer ein Objekt zurück — nie None."""
    from core.telemetry import get_meter

    meter = get_meter("test")
    assert meter is not None


def test_get_meter_counter_add_does_not_raise():
    """Counter aus get_meter().create_counter() — add() darf nie werfen."""
    from core.telemetry import get_meter

    meter = get_meter("test")
    counter = meter.create_counter(
        "agent.fallback",
        description="Agent fallback count",
    )
    # Should not raise, even in no-op mode
    counter.add(1, {"agent": "lstm", "reason": "model_not_loaded"})


def test_get_meter_noop_when_otel_missing():
    """get_meter() gibt No-Op zurück wenn opentelemetry nicht installiert ist."""
    # Temporarily replace OTel flag to simulate missing package
    with patch("core.telemetry._OTEL_PACKAGES_AVAILABLE", False):
        from core import telemetry

        # Re-import to pick up patched flag
        import importlib

        importlib.reload(telemetry)
        meter = telemetry.get_meter("test-noop")
        counter = meter.create_counter("test.counter")
        counter.add(1, {"key": "value"})  # must not raise


def test_get_meter_histogram_record_does_not_raise():
    """Histogram aus get_meter() — record() darf nie werfen."""
    from core.telemetry import get_meter

    meter = get_meter("test")
    histogram = meter.create_histogram(
        "trading.cycle_latency_ms",
        description="Cycle latency in milliseconds",
    )
    histogram.record(7121.9, {"strategy": "RLAgent", "symbols": "10"})
