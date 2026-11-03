"""#4571 Schritt 3.3.3 — der Aufruf existiert wirklich.

Das ist der Test, an dem **PR #2434 gescheitert ist**: Funktion geliefert, Aufruf nie
ergaenzt. ``trigger_in_band_financials_prefetch`` lag danach repoweit ohne Aufrufer im
Code — eine fertige Fassade vor einem Mechanismus, der nie lief.

Deshalb pruefen diese Tests drei Dinge getrennt: dass der Helfer bei abgeschaltetem
Schalter **schweigt**, dass er bei eingeschaltetem die uebergebene Symbolmenge
weiterreicht, und dass die Handelsschleife ihn **nach** dem Anhaengen der offenen
Positionen ruft. Der letzte ist eine Quelltext-Pruefung, weil
``run_strategy_monitor_loop`` 342 Zeilen und 44 Verzweigungen hat und sich nicht ohne
Broker, Scanner und Strategie ausfuehren laesst — die Reihenfolge im Datei-Text
entspricht der Ausfuehrungsreihenfolge innerhalb der Funktion.
"""

from __future__ import annotations

import pathlib

import pytest

from core.report import financials_feed as ff


class _Konfig:
    def __init__(self, aktiv: bool) -> None:
        self.FUNDAMENTALS_INBAND_REFRESH_ENABLED = aktiv


def _setze_schalter(monkeypatch, aktiv: bool) -> None:
    import config

    monkeypatch.setattr(config, "get_config", lambda: _Konfig(aktiv))


@pytest.mark.vc1
def test_helfer_ist_still_wenn_flag_aus(monkeypatch):
    """Standard aus: kein Abruf, kein Thread, keine Netzbenutzung im Zyklus."""
    gerufen: list = []
    monkeypatch.setattr(
        ff,
        "trigger_in_band_financials_prefetch",
        lambda *a, **k: gerufen.append((a, k)),
    )
    _setze_schalter(monkeypatch, False)

    assert ff.auffrischung_fuer_zyklus(["AAPL", "MSFT"]) is None
    assert gerufen == [], "bei abgeschaltetem Schalter darf nichts passieren"


@pytest.mark.vc1
def test_helfer_ruft_trigger_mit_target_stocks(monkeypatch):
    """Die uebergebene Menge wird unveraendert weitergereicht — keine eigene Auflösung."""
    gerufen: list = []
    monkeypatch.setattr(
        ff,
        "trigger_in_band_financials_prefetch",
        lambda symbols, **k: gerufen.append(list(symbols)),
    )
    _setze_schalter(monkeypatch, True)

    ff.auffrischung_fuer_zyklus(["AAPL", "MSFT", "AXON"])

    assert gerufen == [["AAPL", "MSFT", "AXON"]], f"{gerufen!r}"


@pytest.mark.vc1
def test_helfer_schweigt_bei_kaputter_konfiguration(monkeypatch):
    """Fail-safe: ist der Schalter nicht lesbar, bleibt die Auffrischung aus — eine
    Engine darf daran nicht scheitern."""
    gerufen: list = []
    monkeypatch.setattr(
        ff,
        "trigger_in_band_financials_prefetch",
        lambda *a, **k: gerufen.append(a),
    )
    import config

    def _wirft():
        raise RuntimeError("config kaputt")

    monkeypatch.setattr(config, "get_config", _wirft)

    assert ff.auffrischung_fuer_zyklus(["AAPL"]) is None
    assert gerufen == []


@pytest.mark.vc1
def test_monitor_loop_ruft_helfer_nach_positions_append():
    """Der Aufruf existiert, und er steht NACH dem Anhaengen der offenen Positionen.

    Davor waere ``target_stocks`` nur die Scanner-Auswahl — gehaltene Titel, die aus
    den Top-N gefallen sind, bekaemen dann keine Auffrischung, obwohl genau sie
    bewertet werden muessen, damit ein Verkaufssignal entstehen kann.
    """
    quelle = (
        pathlib.Path(__file__).resolve().parents[2]
        / "core"
        / "engine"
        / "monitor_loop.py"
    ).read_text(encoding="utf-8")

    assert "auffrischung_fuer_zyklus(" in quelle, (
        "der Aufruf fehlt — genau der Fehler aus PR #2434: Funktion geliefert, "
        "Aufruf nie ergaenzt"
    )

    anhaengen = quelle.index("Appended {len(_open_symbols)} open positions")
    aufruf = quelle.index("auffrischung_fuer_zyklus(")
    assert anhaengen < aufruf, (
        "die Auffrischung steht VOR dem Anhaengen der offenen Positionen — "
        "gehaltene Titel ausserhalb der Top-N wuerden uebersehen"
    )
