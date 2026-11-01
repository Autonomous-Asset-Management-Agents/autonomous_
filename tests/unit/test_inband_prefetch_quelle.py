"""#4571 Schritt 3.3.1/3.3.2 — der In-Band-Pfad nimmt SEC, nicht Polygon, und ist laut.

``trigger_in_band_financials_prefetch`` hatte repoweit **keinen Aufrufer**. Wer ihn bloss
angeschlossen haette, bekaeme zwei Fehler mitgeliefert:

1. Er rief ``prefetch_universe`` **ohne** ``fetch_results``, womit dort der
   Polygon-Standard greift — die **kostenpflichtige** Quelle statt der freien SEC aus
   #3145 (Memory ``no_paid_apis``), und eine Drossel von **12 s** statt 0,15 s. Bei ~500
   Symbolen ist das der Unterschied zwischen 75 Sekunden und 100 Minuten.
2. Jede Ausnahme endete auf ``logger.debug``, wo **CLAUDE.md §5.6** WARNING verlangt.

Die CIK-Schliessung dafuer existierte zweimal zeichengleich
(``prefetch_financials_universe.py``, ``build_financials_snapshot.py``). Ein dritter
Nachbau waere eine Dreifachung gewesen — deshalb liegt sie jetzt als
``sec_fundamentals.make_sec_fetcher()`` an einer Stelle, und alle drei nutzen sie.
"""

from __future__ import annotations

import logging

import pytest

from core.report import financials_feed as ff
from core.report import sec_fundamentals as sec


def _fange_prefetch(monkeypatch) -> dict:
    """Ersetzt ``prefetch_universe`` und gibt die gesehenen Argumente zurueck."""
    gesehen: dict = {}

    def _fake(**kwargs):
        gesehen.update(kwargs)
        return {"total": 0}

    import scripts.prefetch_financials_universe as pfu

    monkeypatch.setattr(pfu, "prefetch_universe", _fake)
    return gesehen


@pytest.mark.vc1
def test_inband_nutzt_sec_fetcher_ohne_api_key(monkeypatch):
    """Ohne ``fetch_results`` waere es Polygon — mit Schluessel und 12-s-Drossel."""
    gesehen = _fange_prefetch(monkeypatch)
    monkeypatch.setattr(sec, "ticker_cik_map", lambda *a, **k: {"AAPL": "320193"})

    t = ff.trigger_in_band_financials_prefetch(["AAPL"])
    if t is not None:
        t.join(timeout=10)

    assert callable(gesehen.get("fetch_results")), (
        "ohne fetch_results nimmt prefetch_universe den Polygon-Standard "
        f"(gesehen: {sorted(gesehen)})"
    )
    assert not gesehen.get("api_key"), "die SEC braucht keinen Schluessel"


@pytest.mark.vc1
def test_inband_drossel_ist_sec_wert(monkeypatch):
    """0,15 s statt 12 s — sonst dauert ein Zyklus ueber 500 Symbole 100 Minuten."""
    gesehen = _fange_prefetch(monkeypatch)
    monkeypatch.setattr(sec, "ticker_cik_map", lambda *a, **k: {})

    t = ff.trigger_in_band_financials_prefetch(["AAPL"])
    if t is not None:
        t.join(timeout=10)

    assert gesehen.get("throttle_seconds") == sec.SEC_THROTTLE_SECONDS, (
        f"Drossel {gesehen.get('throttle_seconds')!r} statt "
        f"{sec.SEC_THROTTLE_SECONDS!r}"
    )
    assert gesehen["throttle_seconds"] != 12.0, "der Polygon-Wert ist noch gesetzt"


@pytest.mark.vc1
def test_inband_fehlschlag_loggt_warning(monkeypatch, caplog):
    """CLAUDE.md §5.6: ein verschluckter Fehlschlag ist ein unsichtbarer Defekt."""
    import scripts.prefetch_financials_universe as pfu

    def _wirft(**kwargs):
        raise RuntimeError("data.sec.gov nicht erreichbar")

    monkeypatch.setattr(pfu, "prefetch_universe", _wirft)
    monkeypatch.setattr(sec, "ticker_cik_map", lambda *a, **k: {})

    with caplog.at_level(logging.DEBUG):
        t = ff.trigger_in_band_financials_prefetch(["AAPL", "MSFT"])
        if t is not None:
            t.join(timeout=10)

    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "Fehlschlag nur auf " f"{[r.levelname for r in caplog.records]}"
    )
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "2" in text, f"die Symbolzahl fehlt in der Meldung: {text!r}"


@pytest.mark.vc1
def test_make_sec_fetcher_loest_beide_symbolformen(monkeypatch):
    """Der Live-Abruf liefert ``BRK-B``, die SEC-Zuordnung kennt beide Formen."""
    monkeypatch.setattr(
        sec, "fetch_companyfacts", lambda cik, *a, **k: {"facts": {"us-gaap": {}}}
    )
    monkeypatch.setattr(sec, "rows_from_companyfacts", lambda facts: [{"ok": True}])

    hole = sec.make_sec_fetcher(cik_map={"BRK-B": "1067983"})

    assert hole("brk.b") == [{"ok": True}], "die Punkt-Form wurde nicht aufgeloest"
    assert hole("BRK-B") == [{"ok": True}]
    assert hole("GIBTESNICHT") is None, "ohne CIK gibt es nichts zu holen"


@pytest.mark.vc1
def test_die_cik_aufloesung_liegt_nur_noch_an_einer_stelle():
    """Nachweis gegen die Dreifachung.

    Das Kriterium ist bewusst ``cik_map.get(`` und **nicht** ``def fetch_results``:
    ``prefetch_financials_universe.py`` traegt einen legitimen gleichnamigen
    Polygon-Standard, der bleiben muss. Was dort nicht mehr hingehoert, ist die
    CIK-Aufloesung — die lebt jetzt in ``sec_fundamentals.make_sec_fetcher()``.
    """
    import pathlib

    skripte = pathlib.Path(__file__).resolve().parents[2] / "scripts"
    assert skripte.is_dir(), f"Pfad stimmt nicht: {skripte}"
    assert list(skripte.glob("*.py")), "keine Skripte gefunden — der Test waere leer"

    treffer = [
        f"{p.name}:{nr}"
        for p in sorted(skripte.glob("*.py"))
        for nr, zeile in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if "cik_map.get(" in zeile
    ]
    assert treffer == [], (
        "die CIK-Aufloesung gehoert nach sec_fundamentals.make_sec_fetcher(); "
        f"noch vorhanden: {treffer}"
    )
