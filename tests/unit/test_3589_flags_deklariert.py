"""#3589 (ARC-E1.6b) — Waechter: die beiden Kapitalpfad-Flags bleiben deklariert.

Beide Schalter entscheiden ueber den Schutz offener Positionen:

* ``BROKER_STOPS_ENABLED`` (Default False) schaltet die broker-seitigen Stops (#3382).
  Ohne sie ist eine Position ungeschuetzt, sobald die Engine steht.
* ``RECONCILIATION_ENABLED`` (Default True) schaltet den gesamten Abgleich mit der
  Broker-Wahrheit ab (#3389).

Beide sind in ``settings.py`` deklariert (seit der Pydantic-Extraktion #662/PR #3553 die
einzige Quelle; ``config.py`` und ``config.oss.py`` sind nur noch Weiterleitungen) und
stehen im erzeugten Register ``FEATURE_FLAGS.md``. Gelesen werden sie im Order-Pfad
weiterhin mit ``getattr(cfg, …, Default)`` — faellt die Deklaration weg, verschwindet das
Flag daher **lautlos** aus Register und Konfiguration, und der Aufrufer nimmt still seinen
eigenen Default. Genau das verhindern diese Tests.

Sie pinnen vier Dinge: die Deklaration als Feld, den heutigen Wirkwert, einen Kommentar
mit Beleg (Issue oder ADR) und die Einquellen-Regel — ein zweiter Eintrag in einer der
beiden Weiterleitungen waere eine Editions-Divergenz (BORA).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc4

FLAGS = {
    # Flag: (Default, Umgebungsvariable)
    "BROKER_STOPS_ENABLED": (True, "BROKER_STOPS_ENABLED"),
    "RECONCILIATION_ENABLED": (True, "RECONCILIATION_ENABLED"),
}

SETTINGS = _AI_BOT / "settings.py"


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_das_flag_ist_deklariert(flag):
    """Deklariert heisst: ein Feld der Laufzeit-Konfiguration, kein getattr-Default."""
    from settings import RuntimeConfigState

    assert flag in RuntimeConfigState.model_fields, (
        f"{flag} ist kein Feld von RuntimeConfigState — es wird nur per getattr mit "
        f"einem Default gelesen und steht damit in keinem Register."
    )


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_der_wirkwert_bleibt_unveraendert(flag):
    """Die Deklaration darf das Verhalten nicht verschieben."""
    import config

    erwartet = FLAGS[flag][0]
    assert getattr(config.get_config(), flag) is erwartet


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_das_flag_traegt_einen_kommentar_mit_beleg(flag):
    """Jede Zahl und jeder Schalter auf dem Kapitalpfad traegt seine Begruendung am Code
    (CLAUDE.md 5.5 / Pfad-Regel Kapitalschutz)."""
    text = SETTINGS.read_text(encoding="utf-8", errors="replace")
    treffer = re.search(rf"((?:^\s*#[^\n]*\n)+)\s*{flag}: bool", text, re.M)
    assert (
        treffer
    ), f"{flag} steht ohne unmittelbar vorangehenden Kommentar in settings.py"
    kommentar = treffer.group(1)
    assert re.search(
        r"#\s*\d{3,4}|ADR-", kommentar
    ), f"Der Kommentar zu {flag} nennt weder ein Issue noch einen ADR: {kommentar!r}"


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_beide_editionen_sehen_dasselbe_flag(flag):
    """BORA: ``config.py`` (Enterprise) und ``config.oss.py`` (Desktop) sind Weiterleitungen
    auf dieselbe Quelle ``settings.py``. Ein Flag dort erreicht beide Editionen — es darf
    deshalb NICHT in einer der beiden Weiterleitungen gesondert gesetzt werden."""
    quelle = SETTINGS.read_text(encoding="utf-8", errors="replace")
    assert f"{flag}: bool" in quelle
    for weiterleitung in ("config.py", "config.oss.py"):
        text = (_AI_BOT / weiterleitung).read_text(encoding="utf-8", errors="replace")
        assert flag not in text, (
            f"{flag} steht in {weiterleitung} — das waere eine zweite Quelle und damit "
            f"eine Editions-Divergenz."
        )


@pytest.mark.parametrize("flag", sorted(FLAGS))
def test_die_umgebungsvariable_schaltet_weiterhin(flag, monkeypatch):
    """Der bisherige Betriebsweg bleibt: die Umgebungsvariable entscheidet."""
    from settings import RuntimeConfigState

    default, envname = FLAGS[flag]
    monkeypatch.setenv(envname, "false" if default else "true")
    frisch = RuntimeConfigState()
    assert getattr(frisch, flag) is (not default)
