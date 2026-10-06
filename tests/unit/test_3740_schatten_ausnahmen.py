"""#3740 — die Schatten-Import-Ratsche muss ihre eingefrorenen Ausnahmen wiederfinden.

Gemessen am 28.09.2026 auf ``main``: Die Regel meldet 89 Schatten-Importe, und **jeder**
mit dem Zusatz „erlaubt 0" — obwohl der Vertrag die Ausnahme enthaelt::

    [schatten_importe.ausnahmen]
    "core/cloud_logger.py" = { time = 1 }

    → core/cloud_logger.py · 1x Schatten-Import von 'time' in Funktion '_get_secrets',
      erlaubt 0

Ursache: ``Befund.was`` trug den ganzen Satz („Schatten-Import von 'time' in Funktion
'_get_secrets'"), waehrend der Ausnahmeschluessel der blosse Name ist (``time``).
``vergleiche_mit_ausnahmen`` gruppiert nach ``was`` — damit passt kein einziger Eintrag.

Folge: Die **Ratsche** war wirkungslos. Sie konnte weder Wachstum melden (jeder Fund galt
als neu) noch eine Senkung festhalten. Nur der Modus ``warnen`` verdeckte das.

Diese Tests halten beide Richtungen fest: Eine eingetragene Ausnahme wird anerkannt, ein
neuer Fund gemeldet, und eine verschwundene Ausnahme verlangt das Streichen im Vertrag.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc0

_QUELLE = """
import time


def _holt_etwas():
    import time  # Schatten-Import: ueberschattet den Modul-Import

    return time.time()
"""


@pytest.fixture
def wurzel(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "beispiel.py").write_text(_QUELLE, encoding="utf-8")
    return tmp_path


def _vertrag(ausnahmen: dict) -> dict:
    return {"schatten_importe": {"bereiche": ["core"], "ausnahmen": ausnahmen}}


def test_eine_eingetragene_ausnahme_wird_anerkannt(wurzel):
    """Der Kern des Befunds: der Schluessel ist der Name, nicht der Satz."""
    from tests.architecture import regeln

    meldungen = regeln.pruefe_schatten_importe(
        wurzel, _vertrag({"core/beispiel.py": {"time": 1}})
    )
    assert meldungen == [], (
        "Die eingefrorene Ausnahme wurde nicht gefunden — dann ist die Ratsche "
        f"wirkungslos:\n{meldungen}"
    )


def test_ein_neuer_schatten_import_wird_gemeldet(wurzel):
    from tests.architecture import regeln

    meldungen = regeln.pruefe_schatten_importe(wurzel, _vertrag({}))
    assert len(meldungen) == 1, meldungen
    text = meldungen[0]
    assert "core/beispiel.py" in text
    assert "time" in text
    assert "_holt_etwas" in text, (
        "Die Meldung nennt die Funktion nicht mehr — ohne sie ist die Stelle im "
        f"Zweifel nicht auffindbar:\n{text}"
    )


def test_eine_verschwundene_ausnahme_verlangt_das_streichen(wurzel):
    """Die untere Richtung der Ratsche: aufgeraeumt wird eingecheckt."""
    from tests.architecture import regeln

    meldungen = regeln.pruefe_schatten_importe(
        wurzel, _vertrag({"core/beispiel.py": {"time": 2}})
    )
    assert len(meldungen) == 1, meldungen
    assert "nur noch 1" in meldungen[0], meldungen[0]


def test_ein_import_ohne_modul_gegenstueck_ist_kein_schatten(tmp_path):
    """Ein zweig-lokaler Import, der nichts ueberschattet, ist kein Befund."""
    from tests.architecture import regeln

    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "sauber.py").write_text(
        "def f():\n    import json\n    return json\n", encoding="utf-8"
    )
    assert regeln.pruefe_schatten_importe(tmp_path, _vertrag({})) == []
