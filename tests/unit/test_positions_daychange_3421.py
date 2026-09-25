"""#3421 — the /portfolio-summary positions passthrough of today's price move.

Pins the two pure helpers behind the change: `change_today` (Alpaca FRACTION) is
surfaced as a percent, `current_price` as a finite float, and any missing/garbage/
NaN/inf value degrades to None (the console then shows just the price, never a
fabricated move). The passthrough itself is one dict comprehension over the same
Alpaca positions already fetched — no extra broker call.
"""

from core.engine.api_routes import _pct_or_none, _safe_float


def test_safe_float_parses_and_degrades():
    assert _safe_float(2.5) == 2.5
    assert _safe_float("36.05") == 36.05
    assert _safe_float(None) is None
    assert _safe_float("n/a") is None
    assert _safe_float(float("nan")) is None
    assert _safe_float(float("inf")) is None
    assert _safe_float(float("-inf")) is None


def test_change_today_fraction_becomes_percent():
    # Alpaca change_today is a fraction: 0.023 → +2.3 %, -0.011 → -1.1 %.
    assert abs(_pct_or_none(0.023) - 2.3) < 1e-9
    assert abs(_pct_or_none(-0.011) + 1.1) < 1e-9
    assert _pct_or_none(0) == 0.0


def test_change_today_missing_is_none_not_zero():
    # A missing move must be None (unknown), NOT 0.0 (which would render a flat +0.0%).
    assert _pct_or_none(None) is None
    assert _pct_or_none("") is None
    assert _pct_or_none(float("nan")) is None
