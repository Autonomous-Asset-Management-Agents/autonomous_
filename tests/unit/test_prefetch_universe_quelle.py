"""#4571 Schritt 3.1 — der Produzent bedient DASSELBE Universum wie die Engine.

Gemessen am 20./21.10.2026: die Engine bewertet aus ``data_provider.get_sp500_symbols()``
(Live-Abruf der Indexliste), der Produzent der Fundamentals-Ablage las die statische
``core.round_table.sp500_universe.SP500_SYMBOLS``, deren eigener Kommentar sie als
*"as of early 2026 (static fallback)"* ausweist.

**Die Korrelation war vollstaendig: alle 15 bewerteten Titel ohne Ablage-Datei sind genau
die 15, die diese statische Liste nicht enthaelt. 15 von 15.** Ein naechtlicher Lauf des
alten Produzenten haette keinen davon erreicht — gestern nicht, heute nicht, nie. Die
Ablage war nicht veraltet, sie war fuer ein anderes Universum gebaut.

Dazu ein zweiter Befund: es gibt im Repo ZWEI Live-Abrufer, und nur einer war brauchbar.
``sp500_universe.get_sp500_symbols_live()`` rief ``pd.read_html(url)`` **ohne
User-Agent** — Wikipedia antwortet darauf mit HTTP 403 — und verschluckte den Fehlschlag
auf ``logger.debug``, wo CLAUDE.md §5.6 WARNING verlangt. Beides ist instand gesetzt; der
Abrufer ist jetzt die gemeinsame Quelle fuer beide Seiten.
"""

from __future__ import annotations

import logging

import pytest

from core.round_table import sp500_universe as su
from scripts import prefetch_financials_universe as pfu


@pytest.mark.vc1
def test_resolve_universe_nimmt_live_liste_vor_statischer(monkeypatch):
    """Ein Titel, den nur die Live-Liste kennt, muss im Universum auftauchen.

    ``AXON`` ist einer der 15 gemessenen Faelle. ``BRK-B`` prueft gleichzeitig den
    Klassen-Ticker in der Bindestrich-Form, die der Live-Abruf liefert.
    """
    monkeypatch.setattr(su, "get_sp500_symbols_live", lambda: ["AAPL", "AXON", "BRK-B"])
    monkeypatch.setattr(su, "get_sp500_symbols", lambda: ["AAPL"])

    symbols = pfu._resolve_universe(None, None)

    assert "AXON" in symbols, "die Live-Liste wurde nicht benutzt"
    assert "BRK-B" in symbols, "die Bindestrich-Form wird unveraendert durchgereicht"


@pytest.mark.vc1
def test_resolve_universe_warnt_bei_fehlgeschlagenem_live_abruf(monkeypatch, caplog):
    """Der Notnagel ist laut: ein stiller Rueckfall hat die Ablage 47 Tage alt werden
    lassen, ohne dass es jemand sah (CLAUDE.md §5.6)."""
    monkeypatch.setattr(su, "get_sp500_symbols_live", lambda: None)
    monkeypatch.setattr(su, "get_sp500_symbols", lambda: ["AAPL", "MSFT"])

    with caplog.at_level(logging.WARNING):
        symbols = pfu._resolve_universe(None, None)

    assert symbols == ["AAPL", "MSFT"], "der Rueckfall liefert die statische Liste"
    meldungen = [r.message % r.args if r.args else r.message for r in caplog.records]
    assert any(
        r.levelno >= logging.WARNING for r in caplog.records
    ), f"kein WARNING beim Rueckfall: {meldungen}"


@pytest.mark.vc1
def test_resolve_universe_nimmt_extra_symbols_auf(monkeypatch):
    """Vorsorge, nicht die Loesung der gemessenen Luecke.

    Gegen die Live-Liste vom 21.10.2026 (503 Symbole) sind **alle 15** fehlenden Titel
    Indexmitglieder, ``HONA`` und ``ECHO`` eingeschlossen — die Live-Liste allein deckt
    sie vollstaendig ab. ``extra_symbols`` faengt nur den Fall eines GEHALTENEN Titels
    ab, der aus dem Index gefallen ist. Keine Datenbankabfrage, damit der Lauf in einer
    sauberen CI ohne lokale ``decision_outcomes`` funktioniert.
    """
    monkeypatch.setattr(su, "get_sp500_symbols_live", lambda: ["AAPL"])
    monkeypatch.setattr(su, "get_sp500_symbols", lambda: ["AAPL"])

    symbols = pfu._resolve_universe(None, None, extra_symbols=["tsla", "AAPL", " "])

    assert symbols.count("AAPL") == 1, "kein Doppeleintrag"
    assert "TSLA" in symbols, "zusaetzliche Titel werden gross geschrieben aufgenommen"


@pytest.mark.vc1
def test_extra_symbols_ueberleben_die_obergrenze(monkeypatch):
    """``--max-symbols`` darf einen gehaltenen Titel nicht abschneiden — sonst waere die
    Vorsorge genau dann wirkungslos, wenn der Lauf begrenzt wird."""
    monkeypatch.setattr(su, "get_sp500_symbols_live", lambda: ["A", "B", "C", "D"])
    monkeypatch.setattr(su, "get_sp500_symbols", lambda: ["A"])

    symbols = pfu._resolve_universe(None, 2, extra_symbols=["ZZZ"])

    assert symbols == ["A", "B", "ZZZ"], f"{symbols!r}"


@pytest.mark.vc1
def test_live_abruf_sendet_user_agent(monkeypatch):
    """Ohne User-Agent antwortet Wikipedia mit 403 — der Grund, warum der Abrufer nie
    etwas lieferte und der stille DEBUG-Zweig nie auffiel."""
    gesehen: dict = {}

    class _Antwort:
        text = (
            "<table><tr><th>Symbol</th></tr>"
            "<tr><td>AAPL</td></tr><tr><td>BRK.B</td></tr></table>"
        )

        def raise_for_status(self):
            return None

    def _fake_get(url, headers=None, timeout=None):
        gesehen["url"] = url
        gesehen["headers"] = headers or {}
        gesehen["timeout"] = timeout
        return _Antwort()

    monkeypatch.setattr(su.requests, "get", _fake_get)
    # Die Schwelle von 400 Symbolen gilt fuer den echten Abruf; im Test wird sie
    # abgesenkt, damit die Attrappe nicht 400 Zeilen HTML braucht.
    monkeypatch.setattr(su, "_LIVE_MIN_SYMBOLS", 1)

    symbols = su.get_sp500_symbols_live()

    assert "User-Agent" in gesehen["headers"], f"kein User-Agent: {gesehen}"
    assert gesehen["headers"]["User-Agent"], "User-Agent ist leer"
    assert gesehen["timeout"], "ohne Zeitgrenze kann der Lauf haengen"
    assert symbols is not None
    assert "BRK-B" in symbols, "Punkt wird zu Bindestrich normalisiert"


@pytest.mark.vc1
def test_live_abruf_meldet_fehlschlag_auf_warning(monkeypatch, caplog):
    """CLAUDE.md §5.6: ein Ersatzwert wird auf WARNING gemeldet, nie auf DEBUG."""

    def _wirft(url, headers=None, timeout=None):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(su.requests, "get", _wirft)

    with caplog.at_level(logging.DEBUG):
        ergebnis = su.get_sp500_symbols_live()

    assert ergebnis is None
    assert any(
        r.levelno >= logging.WARNING for r in caplog.records
    ), f"Fehlschlag nur auf {[r.levelname for r in caplog.records]}"
