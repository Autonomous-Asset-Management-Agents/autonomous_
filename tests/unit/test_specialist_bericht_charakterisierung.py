"""#4153 (G-8a) — Charakterisierungsnetz fuer den Specialist-Bericht, vor dem Umzug.

Haelt den heutigen Bericht von ``StockSpecialistAgent.research()`` fest — mit allen
``_fetch_*``-Methoden echt, nur die Aussengrenzen ersetzt (``_specialist_bericht.py``).
G-8a2 (#4161) verschiebt die Methoden; dieses Netz muss danach unveraendert gruen sein.

Referenz neu schreiben (nur auf unveraendertem ``core/``):

    SPECIALIST_CHARAKTERISIERUNG_NEU=1 pytest tests/unit/test_specialist_bericht_charakterisierung.py
"""

from __future__ import annotations

import json
import os

import pytest

from tests.unit import _specialist_bericht as netz

pytestmark = pytest.mark.vc1

_NEU = os.environ.get("SPECIALIST_CHARAKTERISIERUNG_NEU") == "1"


@pytest.mark.parametrize("sz", netz.SZENARIEN, ids=lambda s: s.name)
def test_bericht_gleicht_der_referenz(sz):
    ist = netz.fahre(sz)
    if _NEU:
        referenz = netz.lade_referenz() if netz.REFERENZ.exists() else {}
        referenz[sz.name] = ist
        netz.schreibe_referenz(referenz)
        return
    assert netz.REFERENZ.exists(), f"Referenz fehlt: {netz.REFERENZ}"
    referenz = netz.lade_referenz()
    assert sz.name in referenz, f"Referenz fehlt fuer Szenario {sz.name}"
    abweichungen = netz.befunde(referenz[sz.name], ist)
    assert not abweichungen, f"[{sz.name}] erster Befund: " + "\n".join(
        abweichungen[:10]
    )


def test_netz_ist_deterministisch():
    laeufe = [netz.kanonisch(netz.messe_alle()) for _ in range(3)]
    assert laeufe[0] == laeufe[1] == laeufe[2]


def _ohne_ersten_insider_treffer(antworten: dict) -> dict:
    """EDGAR-Form-4-Suche mit einem Treffer weniger (der erste, der den Filter passiert)."""
    url = next(u for u in antworten if "forms=4&" in u)
    geaendert = json.loads(json.dumps(antworten))
    del geaendert[url]["json"]["hits"]["hits"][0]
    return geaendert


def test_netz_ist_nicht_vakuoes():
    antworten = _ohne_ersten_insider_treffer(netz.lade_antworten())
    ist = netz.fahre("vollbild", antworten=antworten)
    abweichungen = netz.befunde(netz.lade_referenz()["vollbild"], ist)
    assert abweichungen, "veraenderte EDGAR-Antwort blieb unbemerkt"
    # Die Meldung zeigt die ersten zehn Befunde. Vor gathered steht hier urls: Mit dem
    # Treffer entfaellt auch der Abruf seines Form-4-Dokuments.
    erster_in_gathered = next(a for a in abweichungen if a.startswith("gathered."))
    assert erster_in_gathered.startswith("gathered.insider_trades"), erster_in_gathered
    assert erster_in_gathered in abweichungen[:10]


def test_unbekannte_url_laesst_das_netz_scheitern():
    antworten = netz.lade_antworten()
    url = next(u for u in antworten if "wikimedia.org" in u and "/NVDA/" in u)
    del antworten[url]
    with pytest.raises(AssertionError, match="URL ohne Fixture") as fehler:
        netz.fahre("flags_aus", antworten=antworten)
    assert url in str(fehler.value)


def test_echter_daten_provider_laesst_das_netz_scheitern():
    with pytest.raises(AssertionError, match="HistoricalDataProvider"):
        netz.fahre("vollbild", provider_ersetzen=False)
