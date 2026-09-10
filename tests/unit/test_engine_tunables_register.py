"""Parity guard for docs/6_runbooks/ENGINE_TUNABLES.md (the tunables register).

The register exists because the $50 MIN_ORDER incident (#2784 reverting #2721,
which had unknowingly overridden the deliberate #2539 small-account floor) showed
that a default's RATIONALE lived only in commit messages. The register records
rationale + deciding PR per critical knob; THIS test is what keeps it honest:

  1. every row's Default column must equal the default in the SOURCE of both
     config.py and config.oss.py (BORA) — change a default without updating the
     register (or vice versa) and this test goes red;
  2. the critical knobs must be PRESENT in the register — deleting a row cannot
     buy a green test.

Source text is parsed (not imported) so local env overrides — e.g. an operator
box with MIN_ORDER_VALUE_USD set — can never skew the comparison.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_BOT_ROOT = _HERE.parents[2]  # ai_trading_bot/
_REPO_ROOT = _HERE.parents[3]
REGISTER = _REPO_ROOT / "docs" / "6_runbooks" / "ENGINE_TUNABLES.md"
CONFIGS = {
    "config.py": _BOT_ROOT / "config.py",
    "config.oss.py": _BOT_ROOT / "config.oss.py",
}

# Deleting a register row must fail CI: these knobs are the ones whose silent
# drift already caused (or nearly caused) live incidents.
REQUIRED = {
    "MIN_ORDER_VALUE_USD",
    # #2789: the relative exit-dust floor. Registered because it is the corrective to the
    # very incident this register exists for — its thresholds must never drift silently.
    "DUST_EXIT_FLOOR_ENABLED",
    "DUST_EXIT_MIN_POSITION_PCT",
    "DUST_EXIT_MIN_EQUITY_PCT",
    "FULL_UNIVERSE_MAX_POSITIONS",
    "ROTATION_MIN_HOLD_HARD_GATE",
    "DISPLACEMENT_BUY_FIRST",
    "ROTATION_EXIT_ENABLED",
    "DECONCENTRATION_TRIM_ENABLED",
    "ROTATION_MAX_EXITS_PER_SESSION",
    "TRIM_MAX_EXITS_PER_SESSION",
    "STOPOUT_REENTRY_COOLDOWN_MIN",
    "REBALANCE_COOLDOWN_HOURS",
    "SMART_EXIT_MIN_HOLD_DAYS",
    "SMART_EXIT_EXIT_RANK_HYSTERESIS",
    "GATEKEEPER_SYMBOL_CHURN_LIMIT",
    "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
    "HITL_EXPIRY_SECONDS",
    # #2815: consensus knobs — they move money the moment they are set, so their
    # defaults must never drift silently.
    "DRAWDOWN_GUARD_WEIGHT",
    "REGIME_DETECTION_WEIGHT",
    "MOMENTUM_AGENT_WEIGHT",
    "VIX_RISK_WEIGHT",
    "LSTM_SIGNAL_WEIGHT",
    "SIGNAL_BUY_THRESHOLD",
    "SIGNAL_SELL_THRESHOLD",
}


def _register_rows() -> dict[str, str]:
    """Parse `| `NAME` | default | ... |` table rows out of the register."""
    rows: dict[str, str] = {}
    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*`([A-Z][A-Z0-9_]+)`\s*\|\s*`([^`]+)`\s*\|", line)
        if m:
            rows[m.group(1)] = m.group(2).strip()
    return rows


def _source_default(source: str, name: str) -> str:
    """Extract NAME's default from config source text (never from a live import).

    Handles the assignment shapes used across both editions (the OSS edition
    assigns module-level WITHOUT type annotations):
      NAME: float = 2.0                                     (literal)
      NAME = int(os.getenv("NAME", "3") or "3")             (getenv, any wrapping)
      NAME: bool = (os.getenv("NAME", "True").lower() == "true")
    """
    flat = re.sub(r"\s+", " ", source)
    m = re.search(
        rf"{name}\s*(?::\s*\w+)?\s*=\s*\(?\s*(?:float|int|str)?\s*\(?\s*"
        rf'os\.getenv\(\s*"{name}"\s*,\s*"([^"]*)"',
        flat,
    )
    if m:
        return m.group(1)
    m = re.search(rf"{name}\s*(?::\s*\w+)?\s*=\s*(-?[0-9][0-9_.]*|True|False)\b", flat)
    if m:
        return m.group(1)
    raise AssertionError(f"{name}: no assignment found in config source")


def _equivalent(doc: str, src: str) -> bool:
    try:
        return float(doc) == float(src)
    except ValueError:
        return doc.strip().lower() == src.strip().lower()


def test_register_exists() -> None:
    assert REGISTER.is_file(), f"missing tunables register: {REGISTER}"


def test_required_knobs_are_listed() -> None:
    missing = REQUIRED - set(_register_rows())
    assert not missing, f"register rows deleted or never written: {sorted(missing)}"


@pytest.mark.parametrize("edition", sorted(CONFIGS))
def test_register_defaults_match_config_source(edition: str) -> None:
    source = CONFIGS[edition].read_text(encoding="utf-8")
    drift = []
    for name, doc_default in _register_rows().items():
        src_default = _source_default(source, name)
        if not _equivalent(doc_default, src_default):
            drift.append(
                f"{name}: register says {doc_default!r}, {edition} says {src_default!r}"
            )
    assert (
        not drift
    ), "register/config drift — update BOTH sides in the same PR:\n" + "\n".join(drift)
