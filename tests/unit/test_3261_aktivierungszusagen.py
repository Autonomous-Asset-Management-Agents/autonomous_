"""#3261 Phase 2 — Kein Flag-Kommentar verspricht "bleibt aus", wenn der Default an ist.

Fuer die im Plan erhobenen Flags (``docs/3261-flag-kommentar-widersprueche/implementation_plan.md``
§1.3/§1.4) prueft der Test in beiden Editionen: Ist der Default an, darf der Kommentarblock direkt
darueber keine Aussage tragen, die ihn als aus oder gesperrt beschreibt — und er muss die
Entscheidung nennen, die das Einschalten autorisiert hat (oder ausdruecklich, dass sie fehlt).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]

# Flag -> Beleg, der im Kommentar stehen muss (autorisierende Entscheidung oder "open").
FLAGS = {
    "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK": "ADR-018",
    "ROUND_TABLE_DISTINCT_ML_SOURCES": "ADR-018",
    "REGIME_CONDITIONER_ENABLED": "ADR-018",
    "MOMENTUM_SCORE_SMOOTH_ENABLED": "#3035",
}

# Aussagen, die einen ausgeschalteten oder gesperrten Zustand behaupten.
VERBOTEN = re.compile(
    r"default OFF|Default OFF|dormant|\bdark\b|stays OFF|Stays OFF|Activation (?:is )?gated|"
    r"Default 0\b|enable after OOS|before LIVE arming\b(?! ?;)|byte-identical",
)


def _block(text: str, flag: str) -> str:
    zeilen = text.splitlines()
    for i, zeile in enumerate(zeilen):
        if re.match(rf"\s*{flag}(?::[^=]+)?\s*=", zeile):
            j = i - 1
            while j >= 0 and zeilen[j].strip().startswith("#"):
                j -= 1
            return "\n".join(zeilen[j + 1 : i])
    raise AssertionError(f"{flag} nicht gefunden")


@pytest.mark.parametrize("datei", ["settings.py"])
@pytest.mark.parametrize("flag", list(FLAGS))
def test_ein_eingeschaltetes_flag_wird_nicht_als_aus_beschrieben(datei, flag):
    text = (PAKET / datei).read_text(encoding="utf-8")
    block = _block(text, flag)
    funde = [
        m.group(0)
        for m in VERBOTEN.finditer(block)
        # "was originally gated on" beschreibt die Geschichte, nicht den Zustand
        if "originally" not in block[max(0, m.start() - 80) : m.end() + 80]
    ]
    assert not funde, f"{datei}: {flag} ist an, der Kommentar sagt {funde}:\n{block}"
    assert (
        FLAGS[flag] in block
    ), f"{datei}: {flag} nennt die autorisierende Entscheidung ({FLAGS[flag]}) nicht."


@pytest.mark.parametrize("datei", ["settings.py"])
def test_die_rl_pflicht_des_ml_gates_wird_als_default_false_beschrieben(datei):
    block = _block(
        (PAKET / datei).read_text(encoding="utf-8"), "GATEKEEPER_STRICT_ML_REQUIRES_RL"
    )
    assert "Default True" not in block and "default True" not in block, block
    assert "FALSE" in block and "ADR-018" in block, block


def test_der_rotations_sessiondeckel_ist_richtig_beziffert():
    import settings

    assert settings._config_state.ROTATION_MAX_EXITS_PER_SESSION == 2
