# tests/unit/test_config_fallback_parity.py
"""#2784 — an in-code ``getattr`` fallback must never contradict the config default.

Restoring the small-account order floor to $1 surfaced two silent divergences:
``trading_loop.py`` fell back to ``50.0`` for MIN_ORDER_VALUE_USD while ``config.py``
says ``1.0`` (and ``risk_manager.py``'s own fail-safe already said ``1.0``), and
``risk_manager.py`` fell back to ``0.02`` for MIN_POSITION_PERCENT while ``config.py``
says ``0.05``.

A fallback fires exactly when the config object is a test double or a partial namespace
— i.e. silently, and at a value nobody chose. For the order floor that is the whole
product decision inverted: a $50 fallback re-blocks every small account (#2714 rev.).
"""

import re
from pathlib import Path

import pytest

from config import get_config

# Knobs whose fallback value is a product decision, not an implementation detail.
GUARDED_KNOBS = (
    "MIN_ORDER_VALUE_USD",
    "MIN_POSITION_PERCENT",
    # #2789: the relative dust-floor thresholds. Their fallbacks live in the executor
    # adapter, i.e. exactly the "second copy of a product decision" shape this guards.
    "DUST_EXIT_MIN_POSITION_PCT",
    "DUST_EXIT_MIN_EQUITY_PCT",
)

_SRC_ROOT = Path(__file__).resolve().parents[2] / "core"


def _getattr_fallbacks(knob: str) -> list[tuple[str, int, str]]:
    """Every ``getattr(<obj>, "<knob>", <literal>)`` under core/, as (file, line, literal)."""
    pattern = re.compile(
        r"""getattr\(\s*[\w_.]+\s*,\s*["']"""
        + re.escape(knob)
        + r"""["']\s*,\s*([^,)]+)\)"""
    )
    hits: list[tuple[str, int, str]] = []
    for path in _SRC_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in pattern.finditer(line):
                hits.append(
                    (
                        str(path.relative_to(_SRC_ROOT.parent)),
                        lineno,
                        match.group(1).strip(),
                    )
                )
    return hits


@pytest.mark.parametrize("knob", GUARDED_KNOBS)
def test_getattr_fallback_matches_config_default(knob):
    expected = float(getattr(get_config(), knob))
    found = _getattr_fallbacks(knob)

    # ANTI-VACUITY: a regex that matches nothing would make this test unfailable.
    assert found, f"scan found no getattr fallback for {knob} — the pattern is broken"

    divergent = [
        f"{file}:{line} falls back to {literal} (config default {expected})"
        for file, line, literal in found
        if float(literal) != expected
    ]
    assert not divergent, "config fallback divergence:\n  " + "\n  ".join(divergent)
