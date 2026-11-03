"""#4650 (Epic #4638) — Lauf-Protokoll, Rhythmus und Gueltigkeit der LLM-Vorauswahl.

Plan: ``docs/4650-lauf-protokoll-rhythmus-und-gueltigkeit-der-llm/implementation_plan.md``.

Der Rhythmus entscheidet, wann der gespeicherte Prompt laeuft (nie oefter als der
Ranking-Refresh). Jeder Lauf landet im Lauf-Speicher, erfolgreich oder nicht. Die
gueltige Liste ist die des letzten erfolgreichen Laufs, bis die Hoechstdauer erreicht ist.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from core.engine.llm_ranking import LlmRankingErgebnis
from core.engine.llm_ranking_lauf import (
    LaufSpeicher,
    LlmRankingLauf,
    Rhythmus,
    hoechstdauer,
    ist_lauf_faellig,
)

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

NY = ZoneInfo("America/New_York")


def _ny(jahr, monat, tag, stunde, minute=0):
    return datetime(jahr, monat, tag, stunde, minute, tzinfo=NY).astimezone(
        timezone.utc
    )


def _lauf(zeitpunkt, ok=True, geliefert=None, fehler=None):
    return LlmRankingLauf(
        zeitpunkt_utc=zeitpunkt,
        prompt_hash="abc123",
        modell="llama3",
        anbieter="OllamaProvider",
        ok=ok,
        geliefert=list(geliefert or (["AAPL", "MSFT"] if ok else [])),
        verworfen={"XYZ1": "formfehler"},
        fehler=fehler,
    )


def test_je_refresh_folgt_refresh_flag():
    jetzt = _ny(2026, 10, 21, 9, 5)
    gestern = _ny(2026, 10, 20, 9, 0)
    for letzter in (None, gestern, jetzt):
        assert ist_lauf_faellig(Rhythmus.JE_REFRESH, letzter, jetzt, True) is True
        assert ist_lauf_faellig(Rhythmus.JE_REFRESH, letzter, jetzt, False) is False
    assert Rhythmus.default() is Rhythmus.JE_REFRESH


def test_taeglich_einmal_je_handelstag_ny():
    # Gherkin: letzter Erfolg gestern 09:00 NY, heute 09:05 faellig, 10:00 nicht mehr.
    gestern = _ny(2026, 10, 20, 9, 0)  # Dienstag
    heute_0905 = _ny(2026, 10, 21, 9, 5)
    heute_1000 = _ny(2026, 10, 21, 10, 0)
    r = Rhythmus.TAEGLICH_VOR_OEFFNUNG

    assert ist_lauf_faellig(r, gestern, heute_0905, True) is True
    # Lauf um 09:05 erfolgreich → um 10:00 kein weiterer Lauf.
    assert ist_lauf_faellig(r, heute_0905, heute_1000, True) is False
    # Erster Lauf ueberhaupt ist faellig.
    assert ist_lauf_faellig(r, None, heute_0905, True) is True
    # Tagesgrenze in New York, nicht in UTC: 20:00 NY (= 00:00 UTC Folgetag) ist derselbe Handelstag.
    abends = _ny(2026, 10, 21, 20, 0)
    assert abends.date() != heute_0905.date()  # UTC-Datum wechselt …
    assert ist_lauf_faellig(r, heute_0905, abends, True) is False  # … NY-Tag nicht
    # Wochenende ist kein Handelstag.
    freitag = _ny(2026, 10, 23, 9, 5)
    samstag = _ny(2026, 10, 24, 9, 5)
    montag = _ny(2026, 10, 26, 9, 5)
    assert ist_lauf_faellig(r, freitag, samstag, True) is False
    assert ist_lauf_faellig(r, freitag, montag, True) is True


def test_woechentlich_einmal_je_woche():
    r = Rhythmus.WOECHENTLICH
    montag = _ny(2026, 10, 19, 9, 5)
    freitag = _ny(2026, 10, 23, 15, 0)
    naechster_montag = _ny(2026, 10, 26, 9, 5)
    assert ist_lauf_faellig(r, None, montag, True) is True
    assert ist_lauf_faellig(r, montag, freitag, True) is False
    assert ist_lauf_faellig(r, montag, naechster_montag, True) is True
    # Wochenende loest keinen Lauf aus, auch wenn die Woche neu ist (ISO-Woche beginnt Montag).
    sonntag = _ny(2026, 10, 25, 9, 5)
    assert ist_lauf_faellig(r, _ny(2026, 10, 16, 9, 5), sonntag, True) is False


def test_nie_oefter_als_refresh():
    gestern = _ny(2026, 10, 20, 9, 0)
    jetzt = _ny(2026, 10, 21, 9, 5)
    letzte_woche = _ny(2026, 10, 12, 9, 0)
    assert (
        ist_lauf_faellig(Rhythmus.TAEGLICH_VOR_OEFFNUNG, gestern, jetzt, False) is False
    )
    assert ist_lauf_faellig(Rhythmus.WOECHENTLICH, letzte_woche, jetzt, False) is False
    assert ist_lauf_faellig(Rhythmus.TAEGLICH_VOR_OEFFNUNG, None, jetzt, False) is False


def test_hoechstdauer_je_rhythmus():
    assert hoechstdauer(Rhythmus.JE_REFRESH, 120) == timedelta(hours=4)
    assert hoechstdauer(Rhythmus.TAEGLICH_VOR_OEFFNUNG, 120) == timedelta(hours=26)
    assert hoechstdauer(Rhythmus.WOECHENTLICH, 120) == timedelta(days=8)


def test_gueltig_bis_hoechstdauer(tmp_path):
    # Gherkin: letzter Erfolg 3 h alt, Hoechstdauer 4 h, aktueller Lauf schlaegt fehl.
    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    t0 = _ny(2026, 10, 21, 9, 0)
    dauer = hoechstdauer(Rhythmus.JE_REFRESH, 120)
    speicher.protokolliere(_lauf(t0, geliefert=["AAPL", "MSFT"]))

    liste, status, alter = speicher.gueltige_liste(t0 + timedelta(hours=1), dauer)
    assert (liste, status) == (["AAPL", "MSFT"], "frisch")

    jetzt = t0 + timedelta(hours=3)
    speicher.protokolliere(_lauf(jetzt, ok=False, fehler="timeout"))
    liste, status, alter = speicher.gueltige_liste(jetzt, dauer)
    assert liste == ["AAPL", "MSFT"]
    assert status == "veraltet_aber_gueltig"
    assert alter == timedelta(hours=3)


def test_abgelaufen_nach_hoechstdauer(tmp_path, caplog):
    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    dauer = timedelta(hours=4)
    t0 = _ny(2026, 10, 21, 9, 0)

    assert speicher.gueltige_liste(t0, dauer) == (None, "keiner", None)

    speicher.protokolliere(_lauf(t0))
    with caplog.at_level(logging.WARNING):
        liste, status, alter = speicher.gueltige_liste(t0 + timedelta(hours=4), dauer)
    assert (liste, status, alter) == (None, "abgelaufen", timedelta(hours=4))
    assert "abgelaufen" in caplog.text


def test_fehlschlag_ueberschreibt_letzten_erfolg_nicht(tmp_path):
    pfad = tmp_path / "laeufe.jsonl"
    speicher = LaufSpeicher(pfad)
    t0 = _ny(2026, 10, 21, 9, 0)
    erfolg = _lauf(t0, geliefert=["NVDA"])
    speicher.protokolliere(erfolg)
    speicher.protokolliere(_lauf(t0 + timedelta(hours=1), ok=False, fehler="timeout"))

    assert speicher.letzter_erfolg() == erfolg
    # Auch nach einem Neustart (frischer Speicher auf derselben Datei).
    neu = LaufSpeicher(pfad)
    assert neu.letzter_erfolg() == erfolg
    assert len(pfad.read_text(encoding="utf-8").splitlines()) == 2


def test_protokoll_enthaelt_alle_pflichtfelder(tmp_path):
    pfad = tmp_path / "laeufe.jsonl"
    speicher = LaufSpeicher(pfad)
    ergebnis = LlmRankingErgebnis(
        ok=True,
        rangliste=["AAPL", "TSLA"],
        verworfen={"FOO": "unbekannt", "BAR1": "formfehler"},
        modell="llama3",
        anbieter="OllamaProvider",
        prompt_hash="f" * 64,
    )
    t = _ny(2026, 10, 21, 9, 5)
    speicher.protokolliere(LlmRankingLauf.aus_ergebnis(ergebnis, t))
    fehl = LlmRankingErgebnis(ok=False, prompt_hash="f" * 64, fehler="kein_provider")
    speicher.protokolliere(LlmRankingLauf.aus_ergebnis(fehl, t))

    zeilen = [json.loads(z) for z in pfad.read_text(encoding="utf-8").splitlines()]
    pflicht = {
        "zeitpunkt_utc",
        "prompt_hash",
        "modell",
        "anbieter",
        "ok",
        "geliefert",
        "verworfen",
        "fehler",
        "lauf_id",
    }
    for zeile in zeilen:
        assert pflicht <= set(zeile)
    assert zeilen[0]["zeitpunkt_utc"] == t.isoformat()
    assert zeilen[0]["geliefert"] == ["AAPL", "TSLA"]
    assert zeilen[0]["verworfen"] == {"FOO": "unbekannt", "BAR1": "formfehler"}
    assert zeilen[0]["ok"] is True
    assert zeilen[1]["ok"] is False and zeilen[1]["fehler"] == "kein_provider"
    assert zeilen[0]["lauf_id"] != zeilen[1]["lauf_id"]


def test_zeitpunkt_kommt_aus_engine_now():
    sim = datetime(2024, 3, 4, 14, 30, tzinfo=timezone.utc)
    ergebnis = LlmRankingErgebnis(ok=True, rangliste=["AAPL"], prompt_hash="x")
    with patch("core.engine.llm_ranking_lauf.engine_now", return_value=sim) as uhr:
        lauf = LlmRankingLauf.aus_ergebnis(ergebnis)
    uhr.assert_called_once_with(timezone.utc)
    assert lauf.zeitpunkt_utc == sim


def test_schreibfehler_nur_warning(tmp_path, caplog):
    # Ein Verzeichnis als Ablagepfad: open(..., "a") schlaegt fehl.
    speicher = LaufSpeicher(tmp_path)
    t0 = _ny(2026, 10, 21, 9, 0)
    erfolg = _lauf(t0)
    with caplog.at_level(logging.WARNING):
        speicher.protokolliere(erfolg)  # wirft nicht
    assert "LlmRankingLauf" in caplog.text
    # Im Prozess bleibt der Lauf erhalten, damit der Zyklus weiter eine gueltige Liste hat.
    assert speicher.letzter_erfolg() == erfolg


def test_kaputte_zeile_wird_uebersprungen(tmp_path, caplog):
    pfad = tmp_path / "laeufe.jsonl"
    LaufSpeicher(pfad).protokolliere(_lauf(_ny(2026, 10, 21, 9, 0)))
    with pfad.open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    with caplog.at_level(logging.WARNING):
        neu = LaufSpeicher(pfad)
    assert neu.letzter_erfolg() is not None
    assert "kaputt" in caplog.text or "uebersprungen" in caplog.text


def test_lauf_modul_importiert_keinen_orderpfad():
    # Harte NFR des Epics: kein Draht vom LLM zum Broker — auch nicht ueber das Protokoll.
    from tests.unit.test_llm_ranking_kein_orderpfad import PAKET, _verbotene

    modul = PAKET / "core" / "engine" / "llm_ranking_lauf.py"
    assert modul.is_file(), f"fehlt: {modul}"
    assert _verbotene(modul.read_text(encoding="utf-8-sig")) == []


# ── #4960: eigener Rhythmus (stuendlich, alle 4 Stunden), Websuche im Protokoll ─────────

_ALTE_RHYTHMEN = (
    Rhythmus.JE_REFRESH,
    Rhythmus.TAEGLICH_VOR_OEFFNUNG,
    Rhythmus.WOECHENTLICH,
)


def test_takt_minuten_je_rhythmus():
    from core.engine.llm_ranking_lauf import takt_minuten

    assert takt_minuten(Rhythmus("stuendlich"), 120) == 60
    assert takt_minuten(Rhythmus("alle_4_stunden"), 120) == 240
    for alt in _ALTE_RHYTHMEN:
        assert takt_minuten(alt, 120) == 120
        assert takt_minuten(alt, 0) == 0


@pytest.mark.parametrize("wert", ["stuendlich", "alle_4_stunden"])
def test_neue_rhythmen_nur_mo_bis_fr(wert):
    rhythmus = Rhythmus(wert)
    mittwoch = _ny(2026, 10, 21, 10)
    vorher = mittwoch - timedelta(hours=1)
    assert ist_lauf_faellig(rhythmus, vorher, mittwoch, True) is True
    assert ist_lauf_faellig(rhythmus, None, mittwoch, True) is True
    assert ist_lauf_faellig(rhythmus, vorher, mittwoch, False) is False
    # Samstag 01:00 New York ist in UTC schon spaeter, Freitag 23:00 NY aber Samstag UTC.
    assert ist_lauf_faellig(rhythmus, None, _ny(2026, 10, 24, 1), True) is False
    assert ist_lauf_faellig(rhythmus, None, _ny(2026, 10, 25, 10), True) is False
    assert ist_lauf_faellig(rhythmus, None, _ny(2026, 10, 23, 23), True) is True


def test_hoechstdauer_zweifaches_intervall():
    assert hoechstdauer(Rhythmus("stuendlich"), 120) == timedelta(hours=2)
    assert hoechstdauer(Rhythmus("alle_4_stunden"), 120) == timedelta(hours=8)
    assert hoechstdauer(Rhythmus("stuendlich"), 0) == timedelta(hours=2)


def test_lauf_json_mit_websuche_und_quellen():
    urls = ["https://a.example/1", "https://b.example/2"]
    erg = LlmRankingErgebnis(
        ok=True,
        rangliste=["AAPL"],
        anbieter="AnthropicProvider",
        prompt_hash="h",
        kanonischer_anbieter="anthropic",
        websuche=True,
        quellen=urls,
    )
    lauf = LlmRankingLauf.aus_ergebnis(erg, zeitpunkt_utc=_ny(2026, 10, 21, 10))

    daten = json.loads(lauf.als_json())
    assert daten["websuche"] is True
    assert daten["quellen"] == urls
    assert daten["kanonischer_anbieter"] == "anthropic"
    assert daten["anbieter"] == "AnthropicProvider"
    assert LlmRankingLauf.aus_json(lauf.als_json()) == lauf


def test_alte_zeile_ohne_neue_felder_lesbar(tmp_path):
    pfad = tmp_path / "laeufe.jsonl"
    alt = {
        "zeitpunkt_utc": "2026-10-21T14:00:00+00:00",
        "prompt_hash": "h",
        "modell": "llama3",
        "anbieter": "OllamaProvider",
        "ok": True,
        "geliefert": ["AAPL"],
        "lauf_id": "alt-1",
    }
    pfad.write_text(json.dumps(alt) + "\n", encoding="utf-8")

    lauf = LaufSpeicher(pfad).letzter_erfolg()

    assert lauf is not None and lauf.lauf_id == "alt-1"
    assert (lauf.websuche, lauf.quellen, lauf.kanonischer_anbieter) == (False, [], None)


@pytest.mark.parametrize(
    "rhythmus, letzter, jetzt, faellig",
    [
        (Rhythmus.JE_REFRESH, None, _ny(2026, 10, 24, 10), True),
        (
            Rhythmus.TAEGLICH_VOR_OEFFNUNG,
            _ny(2026, 10, 21, 8),
            _ny(2026, 10, 21, 9),
            False,
        ),
        (Rhythmus.TAEGLICH_VOR_OEFFNUNG, None, _ny(2026, 10, 24, 9), False),
        (Rhythmus.WOECHENTLICH, _ny(2026, 10, 19, 9), _ny(2026, 10, 21, 9), False),
    ],
)
def test_alte_rhythmen_unveraendert(rhythmus, letzter, jetzt, faellig):
    from core.engine.llm_ranking_lauf import takt_minuten

    assert ist_lauf_faellig(rhythmus, letzter, jetzt, True) is faellig
    assert takt_minuten(rhythmus, 120) == 120
    assert (
        hoechstdauer(rhythmus, 120)
        == {
            Rhythmus.JE_REFRESH: timedelta(hours=4),
            Rhythmus.TAEGLICH_VOR_OEFFNUNG: timedelta(hours=26),
            Rhythmus.WOECHENTLICH: timedelta(days=8),
        }[rhythmus]
    )
