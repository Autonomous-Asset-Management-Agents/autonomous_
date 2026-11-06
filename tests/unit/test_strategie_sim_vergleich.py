"""#4635 (STW-9) — Drei Strategien per Simulation vergleichen: Eingabe, Ablage, Subprozess.

Plan: ``docs/4635-stw-9-feat-console-drei-strategien-per-simulatio/implementation_plan.md``
§5 Schritte 1 und 2. Der Subprozess ist echt; statt ``core.sim.stimmen_sim`` (#3712) laeuft ein
deterministisches Test-Modul aus ``tmp_path`` mit derselben Kommandozeile.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import textwrap
from datetime import date

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

HEUTE = date(2026, 10, 28)
BEKANNT = ["A", "B", "C", "D"]


def _eingabe(**kw):
    from core.sim import vergleich as v

    werte = {
        "beginn": "2026-01-02",
        "ende": "2026-06-30",
        "strategien": ["A", "B"],
        "startkapital": 100_000,
    }
    werte.update(kw)
    return v.VergleichsEingabe.model_validate(werte)


# ── Schritt 1: Eingabe und Kennzahlen ────────────────────────────────────────


def test_zeitraum_vor_drei_jahren_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(beginn="2023-10-27"), HEUTE, None, BEKANNT)
    v.pruefe_eingabe(_eingabe(beginn="2023-10-28"), HEUTE, None, BEKANNT)


def test_ende_in_zukunft_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(ende="2026-10-29"), HEUTE, None, BEKANNT)
    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(
            _eingabe(beginn="2026-06-30", ende="2026-06-30"), HEUTE, None, BEKANNT
        )


def test_mehr_als_drei_strategien_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(
            _eingabe(strategien=["A", "B", "C", "D"]), HEUTE, None, BEKANNT
        )
    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(strategien=[]), HEUTE, None, BEKANNT)


def test_doppelte_strategie_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(strategien=["A", "A"]), HEUTE, None, BEKANNT)
    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(strategien=["X"]), HEUTE, None, BEKANNT)


def test_editions_grenze_weist_ab_statt_vorzuziehen():
    import dataclasses

    from core.entitlement import tier
    from core.sim import vergleich as v

    # Entitlement ist frozen; der Registry-Wert selbst bleibt None (Plan §6 „Offen").
    junior = dataclasses.replace(
        tier.TIER_REGISTRY[tier.Tier.BASIC], backtest_months=12
    )
    eingabe = _eingabe(beginn="2025-04-28", ende="2026-10-28")  # 18 Monate
    with pytest.raises(v.ZeitraumUeberEditionsGrenze):
        v.pruefe_eingabe(eingabe, HEUTE, junior.backtest_months, BEKANNT)
    # Abgewiesen, nicht vorgezogen: die Eingabe bleibt, wie sie kam.
    assert eingabe.beginn == date(2025, 4, 28)
    v.pruefe_eingabe(
        _eingabe(beginn="2025-10-28", ende="2026-10-28"),
        HEUTE,
        junior.backtest_months,
        BEKANNT,
    )


def test_kanonisch_unabhaengig_von_schluesselreihenfolge():
    from core.sim import vergleich as v

    eingabe = _eingabe()
    a = v.kanonisch(eingabe, {"A": {"X": "1", "Y": "2"}, "B": {}})
    b = v.kanonisch(eingabe, {"B": {}, "A": {"Y": "2", "X": "1"}})
    assert a == b
    # Die Strategien bleiben in der Reihenfolge der Eingabe (Anlagereihenfolge, Regel 2).
    assert [s["name"] for s in json.loads(a)["strategien"]] == ["A", "B"]


def test_lauf_id_stabil():
    from core.sim import vergleich as v

    k = v.kanonisch(_eingabe(), {"A": {"X": "1"}, "B": {}})
    assert v.lauf_id(k) == hashlib.sha256(k).hexdigest()[:16]
    assert v.lauf_id(k) == v.lauf_id(
        v.kanonisch(_eingabe(), {"B": {}, "A": {"X": "1"}})
    )
    anders = v.kanonisch(_eingabe(), {"A": {"X": "2"}, "B": {}})
    assert v.lauf_id(anders) != v.lauf_id(k)


def test_kennzahlen_aus_fester_reihe():
    from core.sim import vergleich as v

    reihe = [
        {"date": "2026-01-02", "equity": 100.0},
        {"date": "2026-01-05", "equity": 120.0},
        {"date": "2026-01-06", "equity": 90.0},
        {"date": "2026-01-07", "equity": 110.0},
    ]
    spy = {
        "2026-01-01": 999.0,  # vor dem Zeitraum — zaehlt nicht
        "2026-01-02": 50.0,
        "2026-01-05": 40.0,
        "2026-01-06": 45.0,
        "2026-01-07": 55.0,
        "2026-01-08": 1.0,  # nach dem Zeitraum — zaehlt nicht
    }
    k = v.kennzahlen(reihe, spy, 100.0)
    assert k["twr_pct"] == pytest.approx(10.0)
    assert k["max_drawdown_pct"] == pytest.approx(25.0)  # 120 → 90
    assert k["spy_twr_pct"] == pytest.approx(10.0)  # 50 → 55
    assert k["spy_max_drawdown_pct"] == pytest.approx(20.0)  # 50 → 40
    assert set(k) == {
        "twr_pct",
        "max_drawdown_pct",
        "spy_twr_pct",
        "spy_max_drawdown_pct",
    }


def test_sim_strategie_uebersetzt_registry_werte():
    from core.sim import vergleich as v

    felder, nicht = v.sim_strategie(
        "A",
        {
            "SIGNAL_BUY_THRESHOLD": "0.6",
            "MOMENTUM_AGENT_WEIGHT": "0.9",
            "TREND_AGENT_ENABLED": "true",
            "MAX_BUYS_PER_HOUR": "4",
            "STOP_LOSS_PCT": "5.0",
        },
        50_000.0,
    )
    assert felder["name"] == "A"
    assert felder["buy_threshold"] == 0.6
    assert felder["sell_threshold"] == 0.35  # Registry-Default
    assert felder["gewichte"] == {
        "MomentumAgent": 0.9,
        "TrendAgent": 0.0,
        "FundamentalsAgent": 0.25,
        "ValuationAgent": 0.0,
        "QualityAgent": 0.0,
    }
    assert felder["max_buys_pro_stunde"] == 4
    assert felder["startkapital"] == 50_000.0
    assert nicht == ["STOP_LOSS_PCT"]

    aus, _ = v.sim_strategie("B", {"MOMENTUM_AGENT_ENABLED": "false"}, 1.0)
    assert aus["gewichte"]["MomentumAgent"] == 0.0

    # Der Waechter hat keinen Gewichts-Key: an bleibt das Korpus-Gewicht, aus heisst 0.0.
    wache, nicht = v.sim_strategie("C", {"DRAWDOWN_GUARD_AGENT_ENABLED": "false"}, 1.0)
    assert wache["gewichte"]["DrawdownGuardAgent"] == 0.0
    assert nicht == []


def test_ablage_ohne_user_data_dir_warnt(monkeypatch, caplog, tmp_path):
    from core.sim import vergleich as v

    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.WARNING, logger="core.sim.vergleich"):
        pfad = v.ablage_verzeichnis()
    assert pfad.name == v.ABLAGE
    assert any(r.levelno == logging.WARNING for r in caplog.records)

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "udd"))
    assert v.ablage_verzeichnis() == tmp_path / "udd" / v.ABLAGE


# ── Schritt 2: Subprozess, Ablage, Ergebnis ──────────────────────────────────

#: Steht fuer ``core.sim.stimmen_sim`` (#3712): dieselbe Kommandozeile, festes Ergebnis.
#: Die PID des Kindes landet in einer eigenen Datei, nie im Ergebnis.
_TEST_SIM = """
import argparse, json, os
ap = argparse.ArgumentParser()
for n in ("--store", "--von", "--bis", "--out"):
    ap.add_argument(n, required=True)
ap.add_argument("--strategien", nargs="+", required=True)
a = ap.parse_args()
assert os.path.isdir(a.store), a.store
ergebnisse = []
for i, pfad in enumerate(a.strategien):
    s = json.load(open(pfad, encoding="utf-8"))
    k = s["startkapital"]
    kurve = [
        {"date": "2026-01-02", "equity": k},
        {"date": "2026-01-05", "equity": k * (1.2 + i / 10)},
        {"date": "2026-01-06", "equity": k * 0.9},
    ]
    ergebnisse.append({"strategie": s["name"], "ergebnis": {"daily_equity": kurve}})
with open(os.path.join(os.path.dirname(a.out), "kind_pid.txt"), "w") as f:
    f.write(str(os.getpid()))
with open(a.out, "w", encoding="utf-8", newline="") as f:
    f.write(json.dumps({"von": a.von, "bis": a.bis, "strategien": ergebnisse},
                       indent=2, sort_keys=True))
"""


@pytest.fixture
def werkstatt(tmp_path, monkeypatch):
    """Datenverzeichnis mit Korpus (#4634) und dem Test-Modul anstelle von ``_SIM_MODUL``."""
    import pandas as pd

    from core.sim import vergleich as v
    from core.sim.stimmen_rechner import stand

    udd = tmp_path / "udd"
    korpus = udd / v.KORPUS
    (korpus / "stimmen").mkdir(parents=True)
    (korpus / "stimmen" / "stand.json").write_text(
        json.dumps(stand()), encoding="utf-8"
    )
    (korpus / "korpus.json").write_text(
        json.dumps(
            {
                "zeitraum": {"von": "2023-10-28", "bis": "2026-10-27"},
                "grenzen": [
                    {"art": "survivorship", "text": "heutige Titel"},
                    {"art": "quelle", "text": "IEX"},
                ],
            }
        ),
        encoding="utf-8",
    )
    tage = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    pd.DataFrame({"close": [50.0, 40.0, 55.0]}, index=tage).to_parquet(
        korpus / "SPY.parquet"
    )
    modul = tmp_path / "modul"
    modul.mkdir()
    (modul / "test_sim_4635.py").write_text(textwrap.dedent(_TEST_SIM), "utf-8")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(udd))
    monkeypatch.setenv("PYTHONPATH", str(modul))
    monkeypatch.setattr(v, "_SIM_MODUL", "test_sim_4635")
    v._LAEUFE.clear()
    return udd


def _starte_und_warte(eingabe, werte):
    from core.sim import vergleich as v

    k = v.kanonisch(eingabe, werte)
    lid = v.lauf_id(k)

    async def lauf():
        proc = await v.starte(lid, k)
        await asyncio.to_thread(proc.wait, 60)
        return proc

    return lid, asyncio.run(lauf())


def test_simulation_laeuft_nicht_im_engine_prozess(werkstatt):
    from core.sim import vergleich as v

    lid, proc = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})
    assert proc.returncode == 0
    kind = int((v.ablage_verzeichnis() / lid / "kind_pid.txt").read_text())
    assert kind == proc.pid
    assert kind != os.getpid()


def test_zwei_laeufe_bitgleich(werkstatt):
    import shutil

    from core.sim import vergleich as v

    lid, _ = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})
    ergebnis = v.ablage_verzeichnis() / lid / v.ERGEBNIS
    erster = hashlib.sha256(ergebnis.read_bytes()).hexdigest()
    shutil.rmtree(v.ablage_verzeichnis() / lid)
    v._LAEUFE.clear()

    lid2, _ = _starte_und_warte(_eingabe(), {"B": {}, "A": {}})
    assert lid2 == lid
    assert hashlib.sha256(ergebnis.read_bytes()).hexdigest() == erster


def test_ergebnis_ohne_zeitstempel_pid_pfad(werkstatt):
    from core.sim import vergleich as v

    lid, proc = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})
    roh = (v.ablage_verzeichnis() / lid / v.ERGEBNIS).read_text(encoding="utf-8")
    antwort = json.dumps(v.lies_ergebnis(lid))
    for text in (roh, antwort):
        assert str(proc.pid) not in text
        assert str(werkstatt) not in text and "udd" not in text
        assert not re.search(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d", text)
        for wort in ("timestamp", "zeitstempel", "erstellt", "pid", "pfad", "path"):
            assert not re.search(rf"\b{wort}\b", text.lower()), wort


def test_ergebnis_traegt_zeitraum_unsicherheit_nicht_abgebildet(werkstatt):
    from core.sim import vergleich as v

    lid, _ = _starte_und_warte(
        _eingabe(startkapital=1000),
        {"A": {"STOP_LOSS_PCT": "5.0"}, "B": {}},
    )
    antwort = v.lies_ergebnis(lid)
    assert antwort["status"] == "fertig"
    e = antwort["ergebnis"]
    assert e["zeitraum"] == {"beginn": "2026-01-02", "ende": "2026-06-30"}
    assert e["unsicherheit"] == {"pfade": 1, "handelstage": 3}
    arten = [n["art"] for n in e["nicht_abgebildet"]]
    assert arten == ["survivorship", "quelle", "einstellungen", "agenten", "agenten"]
    assert e["nicht_abgebildet"][2] == {
        "art": "einstellungen",
        "strategie": "A",
        "keys": ["STOP_LOSS_PCT"],
    }
    assert e["nicht_abgebildet"][4] == {
        "art": "agenten",
        "strategie": "B",
        "agenten": [
            "NewsSentimentAgent",
            "RegimeDetectionAgent",
            "SpecialistAlphaAgent",
            "VIXAwareRiskAgent",
        ],
    }
    # Anlagereihenfolge, je Strategie genau die vier Zahlen (Regel 2).
    assert [s["name"] for s in e["strategien"]] == ["A", "B"]
    a = e["strategien"][0]
    assert a["twr_pct"] == pytest.approx(-10.0)
    assert a["max_drawdown_pct"] == pytest.approx(25.0)
    assert a["spy_twr_pct"] == pytest.approx(10.0)
    assert a["spy_max_drawdown_pct"] == pytest.approx(20.0)
    assert set(a) == {
        "name",
        "twr_pct",
        "max_drawdown_pct",
        "spy_twr_pct",
        "spy_max_drawdown_pct",
    }


def _setze_ohne_lstm(lid, wert):
    from core.sim import vergleich as v

    pfad = v.ablage_verzeichnis() / lid / v.ERGEBNIS
    sim = json.loads(pfad.read_text(encoding="utf-8"))
    for s in sim["strategien"]:
        s["ohne_lstm"] = wert
    pfad.write_text(json.dumps(sim), encoding="utf-8")


def test_ergebnis_nennt_ohne_lstm_einmal(werkstatt):
    from core.sim import vergleich as v

    lid, _ = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})
    _setze_ohne_lstm(lid, True)

    nicht = v.lies_ergebnis(lid)["ergebnis"]["nicht_abgebildet"]

    assert nicht.count({"art": "ohne_lstm"}) == 1


def test_ergebnis_mit_lstm_nennt_nichts(werkstatt):
    from core.sim import vergleich as v

    lid, _ = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})
    _setze_ohne_lstm(lid, False)

    arten = [n["art"] for n in v.lies_ergebnis(lid)["ergebnis"]["nicht_abgebildet"]]

    assert "ohne_lstm" not in arten


def test_ergebnis_nennt_titel_ohne_fundamentaldaten(werkstatt):
    import pandas as pd

    from core.sim import vergleich as v

    tag = "2026-01-05T20:00:00+00:00"
    stimmen = v.korpus_verzeichnis() / "stimmen"
    pd.DataFrame(
        {"did": [0, 1], "symbol": ["AAPL", "XOM"], "ts": [tag, tag]}
    ).to_parquet(stimmen / "decisions.parquet")
    pd.DataFrame(
        {"did": [0, 0, 1], "agent": ["FundamentalsAgent", "QualityAgent", "TrendAgent"]}
    ).to_parquet(stimmen / "votes.parquet")
    lid, _ = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})

    nicht = v.lies_ergebnis(lid)["ergebnis"]["nicht_abgebildet"]

    assert nicht.count({"art": "ohne_fundamentaldaten", "titel": ["XOM"]}) == 1


def test_ergebnis_ohne_luecke_nennt_keine_titel(werkstatt):
    from core.sim import vergleich as v

    lid, _ = _starte_und_warte(_eingabe(), {"A": {}, "B": {}})

    arten = [n["art"] for n in v.lies_ergebnis(lid)["ergebnis"]["nicht_abgebildet"]]

    assert "ohne_fundamentaldaten" not in arten


def test_kanonisch_traegt_rechenstand():
    from core.sim import vergleich as v

    inhalt = json.loads(v.kanonisch(_eingabe(), {"A": {}, "B": {}}))

    assert inhalt["rechenstand"] == 5


def test_lauf_von_vor_dem_umbau_gilt_nicht_als_fertig(werkstatt):
    """#5044: Gleiche Eingabe, aber der alte Lauf rechnete noch mit ``HOLD strict_ml``."""
    from core.sim import vergleich as v

    werte = {"A": {}, "B": {}}
    neu = v.kanonisch(_eingabe(), werte)
    inhalt = json.loads(neu)
    del inhalt["rechenstand"]
    alt = json.dumps(
        inhalt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    alt_id = v.lauf_id(alt)
    asyncio.run(v.starte(alt_id, alt)).wait(60)

    assert v.ist_fertig(alt_id)
    assert v.lauf_id(neu) != alt_id
    assert not v.ist_fertig(v.lauf_id(neu))


def test_lies_ergebnis_unbekannt_und_fehler(werkstatt):
    from core.sim import vergleich as v

    assert v.lies_ergebnis("0123456789abcdef") is None
    assert v.lies_ergebnis("../../etc") is None
    (v.ablage_verzeichnis() / "0123456789abcdef").mkdir(parents=True)
    # Ordner ohne Ergebnis und ohne laufenden Prozess (z. B. nach Engine-Neustart): Fehler.
    assert v.lies_ergebnis("0123456789abcdef")["status"] == "fehler"


def test_pruefe_korpus_deckt_zeitraum_sonst_grund(werkstatt, monkeypatch):
    from core.sim import vergleich as v

    stimmen = werkstatt / v.KORPUS / "stimmen"
    for key in ("APCA_API_KEY_ID", "ALPACA_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # Ohne Stimmen deckt der Korpus nichts ab; ohne Keys ist das der Grund.
    with pytest.raises(v.KeinKorpus) as fehlt:
        v.pruefe_korpus(date(2026, 1, 2), date(2026, 6, 30))
    assert fehlt.value.grund == "keine_keys"

    for name in ("decisions", "votes", "kurse"):
        (stimmen / f"{name}.parquet").write_bytes(b"")
    v.pruefe_korpus(date(2026, 1, 2), date(2026, 6, 30))

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    # Ende hinter dem Korpus (bis 2026-10-27): mit Keys fehlt nur der Korpus.
    with pytest.raises(v.KeinKorpus) as spaet:
        v.pruefe_korpus(date(2026, 1, 2), date(2026, 10, 28))
    assert spaet.value.grund == "kein_korpus"


def test_alter_korpus_keinkorpus(werkstatt, monkeypatch):
    from core.sim import vergleich as v

    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    stimmen = werkstatt / v.KORPUS / "stimmen"
    for name in ("decisions", "votes", "kurse"):
        (stimmen / f"{name}.parquet").write_bytes(b"")
    beginn, ende = date(2026, 1, 2), date(2026, 6, 30)
    v.pruefe_korpus(beginn, ende)

    (stimmen / "stand.json").write_text('{"stimmen_stand": 1}', encoding="utf-8")
    with pytest.raises(v.KeinKorpus) as abweichend:
        v.pruefe_korpus(beginn, ende)
    assert abweichend.value.grund == "kein_korpus"

    # Ohne stand.json: der Korpus von vor #5071.
    (stimmen / "stand.json").unlink()
    with pytest.raises(v.KeinKorpus):
        v.pruefe_korpus(beginn, ende)


# ── #5126 (STW-9d): Lieferzustand als Vergleichslinie (T20, E2) ─────────────────
# Plan: ``docs/5126-*/implementation_plan.md`` §6 Schritt 1.


def test_lieferzustand_zaehlt_nicht_gegen_drei():
    from core.sim import vergleich as v

    v.pruefe_eingabe(
        _eingabe(strategien=["A", "B", "C", v.LIEFERZUSTAND]), HEUTE, None, BEKANNT
    )


def test_vier_eigene_plus_lieferzustand_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(
            _eingabe(strategien=["A", "B", "C", "D", v.LIEFERZUSTAND]),
            HEUTE,
            None,
            BEKANNT,
        )


def test_nur_lieferzustand_abgewiesen():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(_eingabe(strategien=[v.LIEFERZUSTAND]), HEUTE, None, BEKANNT)


def test_lieferzustand_hoechstens_einmal():
    from core.sim import vergleich as v

    with pytest.raises(v.EingabeUngueltig):
        v.pruefe_eingabe(
            _eingabe(strategien=["A", v.LIEFERZUSTAND, v.LIEFERZUSTAND]),
            HEUTE,
            None,
            BEKANNT,
        )


def test_werte_lieferzustand_aus_registry_defaults(monkeypatch):
    from core import strategie_profile as sp
    from core import trading_settings as ts
    from core.sim import vergleich as v

    vorher = v.werte_lieferzustand()
    geaendert = dict(ts.current_settings())
    geaendert["SIGNAL_BUY_THRESHOLD"] = "0.99"

    def _verboten(*a, **kw):
        raise AssertionError("Lieferzustand liest keine Nutzerwerte")

    monkeypatch.setattr(ts, "current_settings", lambda *a, **kw: geaendert)
    monkeypatch.setattr(sp, "lade_strategien", _verboten)

    werte = v.werte_lieferzustand()
    assert werte == vorher
    assert werte["SIGNAL_BUY_THRESHOLD"] == ts.canonical_text(
        "SIGNAL_BUY_THRESHOLD", ts.REGISTRY["SIGNAL_BUY_THRESHOLD"].default
    )


def test_werte_lieferzustand_nur_abgebildete_keys():
    from core.sim import vergleich as v

    werte = v.werte_lieferzustand()
    assert list(werte) == sorted(v._ABGEBILDET)
    _, keys = v.sim_strategie(v.LIEFERZUSTAND, werte, 1000)
    assert keys == []  # kein Eintrag „einstellungen" fuer den Lieferzustand


def test_kanonisch_ohne_lieferzustand_bytegleich():
    """Vor dem Umbau (main 527dd5fdf) erzeugt; Vergleiche ohne Lieferzustand bleiben gleich."""
    from core.sim import vergleich as v

    eingabe = _eingabe()
    werte = v.werte_fuer(["A", "B"], {"A": {"STOP_LOSS_PCT": "5.0"}, "B": {}})
    assert v.kanonisch(eingabe, werte) == (
        b'{"beginn":"2026-01-02","ende":"2026-06-30","rechenstand":5,'
        b'"startkapital":100000.0,"strategien":[{"name":"A","werte":'
        b'{"STOP_LOSS_PCT":"5.0"}},{"name":"B","werte":{}}]}'
    )


def test_zwei_laeufe_bitgleich_mit_lieferzustand(werkstatt):
    import shutil

    from core.sim import vergleich as v

    namen = ["A", v.LIEFERZUSTAND]
    eingabe = _eingabe(strategien=namen)
    werte = v.werte_fuer(namen, {"A": {}})
    k1 = v.kanonisch(eingabe, werte)
    lid, _ = _starte_und_warte(eingabe, werte)
    ergebnis = v.ablage_verzeichnis() / lid / v.ERGEBNIS
    erster = hashlib.sha256(ergebnis.read_bytes()).hexdigest()
    antwort = v.lies_ergebnis(lid)
    shutil.rmtree(v.ablage_verzeichnis() / lid)
    v._LAEUFE.clear()

    werte2 = v.werte_fuer(namen, {"A": {}})
    assert v.kanonisch(eingabe, werte2) == k1
    lid2, _ = _starte_und_warte(eingabe, werte2)
    assert lid2 == lid
    assert hashlib.sha256(ergebnis.read_bytes()).hexdigest() == erster
    assert v.lies_ergebnis(lid2) == antwort
    e = antwort["ergebnis"]
    assert [s["name"] for s in e["strategien"]] == ["A", v.LIEFERZUSTAND]
    assert not any(
        n["art"] == "einstellungen" and n["strategie"] == v.LIEFERZUSTAND
        for n in e["nicht_abgebildet"]
    )


def test_lieferzustand_in_bekannten_warnt(caplog):
    from core.sim import vergleich as v

    with caplog.at_level(logging.WARNING, logger="core.sim.vergleich"):
        v.pruefe_eingabe(
            _eingabe(strategien=["A", v.LIEFERZUSTAND]),
            HEUTE,
            None,
            [*BEKANNT, v.LIEFERZUSTAND],
        )
    treffer = [r for r in caplog.records if v.LIEFERZUSTAND in r.getMessage()]
    assert treffer and all(r.levelno == logging.WARNING for r in treffer)
