"""#3447 (ARC-E1.8) — der CH-2-Erkenner zaehlt Aufrufe, nicht Namen.

Der Zaehler aus ``tests/features/step_defs/test_ch2_iron_dome.py`` ist das Mass, an dem
Epic #3366 abgenommen wird. Er sucht heute per regulaerem Ausdruck ueber den rohen Text
und ueberspringt dabei nur Zeilen, die mit ``def``, ``async def`` oder ``#`` beginnen —
**Docstring-Ruempfe nicht**.

Warum das mehr ist als ein Schoenheitsfehler: Ein Mass, das Umgehungen finden soll, irrt
hier in **beide** Richtungen. Es zaehlt eine Erwaehnung im Fliesstext mit, und es hat
umgekehrt die Form ``await asyncio.to_thread(client.submit_order, req)`` urspruenglich
gar nicht gefunden — sieben echte Broker-Aufrufe waren unsichtbar, bis jemand das Muster
von Hand erweiterte. Solange der Zaehler am Text haengt, ist jede weitere Form eine
Frage der Aufmerksamkeit.

Dieselbe Umstellung hat sich in #3418 schon einmal bewaehrt: Dort fand die Textsuche den
eigenen Kommentar des Tests.

**Dieser Test faehrt gegen die heutige API und ist heute rot** — ausdruecklich kein
Import-Fehler gegen eine noch nicht gebaute Schnittstelle. Der Plan zu #3377 haelt fest,
dass ein ``ModuleNotFoundError`` als NICHT erfuellt gilt, weil er nichts ueber das System
aussagt.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _erkenner():
    from tests.features.step_defs.test_ch2_iron_dome import _broker_call_sites

    return _broker_call_sites


# ---------------------------------------------------------------------------
# Der fuehrende rote Fall
# ---------------------------------------------------------------------------


def test_ein_docstring_ist_keine_aufrufstelle() -> None:
    """Szenario: Ein Docstring ist keine Aufrufstelle.

    ``core/strategies/lstm_strategy.py:76`` lautet::

        Nutzt BaseStrategy._submit_order_safe (DRY-konsolidiert mit RLStrategy).

    Das ist eine Zeile im Klassen-Docstring von ``LSTMDynamicStrategy``. Sie trifft das
    Muster ``\\.(…|_submit_order_safe|…)\\s*\\(``, weil zwischen Name und Klammer ein
    Leerzeichen steht — und sie wird mitgezaehlt, obwohl dort nichts aufgerufen wird.

    Solange sie zaehlt, kann der Zaehler **nie** null erreichen, gleich wie viel echte
    Arbeit geleistet wird. Das Abnahmekriterium von #3366 waere unerfuellbar.
    """
    stellen = _erkenner()()
    docstring_zeile = "core/strategies/lstm_strategy.py:76"
    assert docstring_zeile not in stellen, (
        f"Der Erkenner zaehlt {docstring_zeile} als Broker-Aufrufstelle. Dort steht "
        "aber eine Docstring-Zeile ('Nutzt BaseStrategy._submit_order_safe (…)'), kein "
        "Aufruf. Der Textfilter ueberspringt nur Zeilen, die mit 'def', 'async def' "
        "oder '#' beginnen — Docstring-Ruempfe nicht (#3447, Gruppe E)."
    )


# ---------------------------------------------------------------------------
# Gegentests: die Umstellung darf den Erkenner nicht schwaechen
# ---------------------------------------------------------------------------


def test_der_direkte_aufruf_wird_weiter_gefunden() -> None:
    """Die haeufigste Form bleibt sichtbar: ``self.client.submit_order(...)``.

    An eigenem Quelltext geprueft, nicht an einer Zeile des Bestands — die erste Fassung
    zeigte auf ``core/strategies/base.py:319`` und riss, sobald sich die Datei darueber
    bewegte (#3447, Schritt 3).
    """
    from tests.features.step_defs.test_ch2_iron_dome import _aufrufstellen_in_quelle

    quelle = "def f(self, req):\n    self.client.submit_order(req)\n"
    assert "core/beispiel.py:2" in _aufrufstellen_in_quelle(quelle, "beispiel.py"), (
        "Der direkte Aufruf wird nicht mehr gefunden. Ein Erkenner, der weniger findet, "
        "ist kein Fortschritt (#3447)."
    )


def test_ein_sim_zweig_wird_ausgewiesen_nicht_verschwiegen() -> None:
    """Plan §8 (#3447 Schritt 3): Sim-Stellen werden getrennt gezaehlt — aber gezaehlt."""
    from tests.features.step_defs.test_ch2_iron_dome import (
        _aufrufstellen_in_quelle,
        _sim_zeilen_in_quelle,
    )

    quelle = (
        "def f(self, is_simulation, req):\n"
        "    if is_simulation:\n"
        "        self.client.submit_order(symbol='A')\n"
        "    else:\n"
        "        self.client.submit_order(req)\n"
    )
    alle = _aufrufstellen_in_quelle(quelle, "beispiel.py")
    sim = _sim_zeilen_in_quelle(quelle, "beispiel.py")

    assert alle == ["core/beispiel.py:3", "core/beispiel.py:5"]
    assert sim == [
        "core/beispiel.py:3"
    ], "Nur der Simulationszweig ist eine Sim-Stelle — der else-Zweig bewegt Kapital."


def test_submit_order_safe_ist_kein_brokeraufruf_mehr() -> None:
    """Er fuehrt durchs Tor. Wird er wieder gezaehlt, stehen seine vier Aufrufer als
    Umgehungen da, obwohl sie nur diese eine Funktion rufen."""
    from tests.features.step_defs.test_ch2_iron_dome import _aufrufstellen_in_quelle

    quelle = "async def f(self):\n    await self._submit_order_safe('A', 1, 'buy')\n"
    assert _aufrufstellen_in_quelle(quelle, "beispiel.py") == []


def test_die_thread_form_wird_weiter_gefunden() -> None:
    """Die Form, die der erste Entwurf uebersah: ``to_thread(client.submit_order, req)``.

    Genau diese Schreibweise fiel durch das urspruengliche Textmuster — sie ist der Grund,
    warum der Zaehler einmal von 16 auf 21 sprang, ohne dass sich der Code geaendert hatte.

    Geprueft wird an **eigenem** Quelltext, nicht an einer Zeile des Bestands. Die erste
    Fassung dieses Tests zeigte auf ``order_executor.py:1162`` — und wurde rot, als genau
    diese Stelle hinter das Tor wanderte (#3447, Schritt 2b). Ein Test, den man editieren
    muss, weil der Code besser wurde, prueft das Falsche.
    """
    from tests.features.step_defs.test_ch2_iron_dome import _aufrufstellen_in_quelle

    quelle = (
        "import asyncio\n"
        "async def f(client, req):\n"
        "    await asyncio.to_thread(client.submit_order, req)\n"
    )
    stellen = list(_aufrufstellen_in_quelle(quelle, "beispiel.py"))
    assert "core/beispiel.py:3" in stellen, (
        "Die to_thread-Form wird nicht mehr gefunden. Diese Form war schon einmal "
        "unsichtbar; sie darf es nicht wieder werden (#3447)."
    )


def test_der_zaehler_nennt_datei_und_zeile() -> None:
    """Jede Fundstelle traegt Datei und Zeile — sonst ist der Befund nicht nachprüfbar.

    Das Akzeptanzkriterium verlangt ausdruecklich, dass eine neue Umgehung „Datei und
    Zeile nennt". Eine blosse Zahl waere ein Alarm ohne Adresse.
    """
    stellen = _erkenner()()
    assert stellen, "Der Erkenner findet gar nichts — dann prueft er auch nichts."
    for stelle in stellen:
        datei, _, zeile = stelle.rpartition(":")
        assert datei.startswith("core/"), f"Fundstelle ohne Pfad: {stelle!r}"
        assert zeile.isdigit(), f"Fundstelle ohne Zeilennummer: {stelle!r}"
