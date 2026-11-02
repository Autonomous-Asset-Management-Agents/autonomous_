"""Abnahme auf Epic-Ebene für #4625 (Strategie-Werkstatt) — scharf geschaltet in #5026, #5044.

Angelegt in #4626 nach dem Maker-Muster (`test_epic_4638.py`). Jede Probe fährt den Code
ihres Sub-Issues; ersetzt sind nur die Grenzen nach außen: Alpaca-Client, Schlüsselbund und
die Engine-Auth. Die Kette, die Ablage, die Routen, die Pipeline, die Simulation und die
Engine-Kindprozesse sind echt.

**Sammeln statt Abbrechen.** Ein Schritt wirft nicht sofort, sondern trägt seine Lücke
in `offen` ein; der letzte Schritt eines Szenarios wirft alle zusammen
(`_abschluss`). Sonst liefe nur die erste Probe jedes Szenarios — die Probe auf
`/set-strategy` in „Neutrale Darstellung" etwa nie. Andere Ausnahmen als
`FehlendeFunktion` werden nicht gesammelt, sie brechen sofort.

**Brücke ins Frontend.** Die Szenarien 5, 6 (Ansichten) und 7 prüft vitest in
`src/test/strategieWerkstattAbnahme.test.tsx`. Trägt dort ein Test `it.fails(`, meldet der
Schritt hier `FehlendeFunktion`. Die Brücke liest nur, ob der Test als `it(` steht. Ob er
grün ist, sagt der Vitest-Lauf; der BDD-Container hat kein Node.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import pathlib
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_bdd import given, scenarios, then, when

from tests.integration.test_zwei_engines_pfade import (
    _beende,
    _ergebnis,
    _starte,
    _umgebung,
)

WURZEL = pathlib.Path(__file__).resolve().parents[4]
VITEST_ABNAHME = WURZEL / "src" / "test" / "strategieWerkstattAbnahme.test.tsx"
UX_KONZEPT = WURZEL / "docs" / "4628-ux-konzept-strategie-werkstatt" / "ux_konzept.md"
SETUP_MANAGER = WURZEL / "desktop" / "electron" / "setup-manager.cjs"
ENGINE_FLOTTE = WURZEL / "desktop" / "electron" / "__tests__" / "engine-fleet.test.mjs"
PAPER_KONTEN = ("PA1", "PA2", "PA3")

scenarios("../epic_4625.feature")


class FehlendeFunktion(NotImplementedError):
    """Die Funktion, die ein Schritt prüft, gibt es noch nicht (Verweis auf das Sub-Issue)."""


pytestmark = [pytest.mark.vc0]


@pytest.fixture
def offen() -> list[str]:
    """Die Lücken eines Szenarios, gesammelt bis zum letzten Schritt."""
    return []


@pytest.fixture
def werkstatt(tmp_path, monkeypatch):
    """Flag und HITL an, echte Kette und Ablage in ``tmp_path``, Auth aus."""
    from fastapi.testclient import TestClient

    import config
    from core import hitl_gate
    from core.engine import api_routes
    from core.round_table.senate_log import LocalJSONAuditLogger

    log_dir = tmp_path / "senate"
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "udd"))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    yield SimpleNamespace(client=TestClient(api_routes.app), log_dir=log_dir)
    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)


def _anlegen(werkstatt, name: str, **werte) -> None:
    antwort = werkstatt.client.post(
        "/api/strategies",
        json={
            "name": name,
            "werte": werte,
            "acknowledgment": "bestaetigt",
            "nonce": f"anlegen-{name}",
        },
    )
    assert antwort.status_code == 201, antwort.text


def _kette(log_dir: pathlib.Path) -> list[dict]:
    return [
        json.loads(zeile)
        for datei in sorted(log_dir.glob("audit_log_*.jsonl"))
        for zeile in datei.read_text(encoding="utf-8").splitlines()
        if zeile.strip()
    ]


def _pruefe_kette(zeilen: list[dict]) -> None:
    """Rechnet ``hash`` je Zeile nach und folgt ``prev_hash``.

    Einen Python-Prüfer gibt es nicht; dieselbe Rechnung steht in
    ``desktop/electron/audit-chain.cjs`` und ``test_hash_kette_mehrere_loops_4126.py``.
    """
    for vorher, jetzt in zip(zeilen, zeilen[1:]):
        assert jetzt["prev_hash"] == vorher["hash"], "Kette gebrochen"
    for zeile in zeilen:
        ohne_hash = {k: v for k, v in zeile.items() if k != "hash"}
        neu = hashlib.sha256(json.dumps(ohne_hash, sort_keys=True).encode()).hexdigest()
        assert neu == zeile["hash"], f"Hash stimmt nicht: {zeile['event_type']}"


def _braucht(modul: str, symbol: str, sub_issue: str):
    """Liefert ``modul.symbol`` oder wirft ``FehlendeFunktion`` mit Verweis."""
    obj = getattr(importlib.import_module(modul), symbol, None)
    if obj is None:
        raise FehlendeFunktion(f"{modul}.{symbol} fehlt — #{sub_issue}")
    return obj


def _probe(offen: list[str], pruefung) -> None:
    """Führt ``pruefung`` aus; eine ``FehlendeFunktion`` wird gesammelt, alles andere bricht."""
    try:
        pruefung()
    except FehlendeFunktion as exc:
        offen.append(str(exc))


def _routen() -> set[tuple]:
    from core.engine import api_routes

    return {
        (m, getattr(r, "path", None))
        for r in api_routes.app.routes
        for m in getattr(r, "methods", ())
    }


def _abschluss(offen: list[str]) -> None:
    if offen:
        raise FehlendeFunktion(" | ".join(offen))


def _bruecke(testname: str, sub_issue: str):
    """Liest den vitest-Teil: ``it.fails`` heißt offen, ``it`` heißt eingelöst."""

    def pruefung():
        quelle = VITEST_ABNAHME.read_text(encoding="utf-8")
        if re.search(r"\bit\.fails\(\s*[\"']" + re.escape(testname), quelle):
            raise FehlendeFunktion(
                f"{VITEST_ABNAHME.name}: {testname!r} steht noch als it.fails — {sub_issue}"
            )
        assert re.search(
            r"\bit\(\s*[\"']" + re.escape(testname), quelle
        ), f"Brücke gebrochen: {testname!r} fehlt in {VITEST_ABNAHME.name}"

    return pruefung


# ── Szenario 1: Strategie anlegen wird versiegelt (#4629) ────────────────────


@given('ein Nutzer legt die Strategie "A" mit eigenen Settings an')
def strategie_a_anlegen(werkstatt):
    _anlegen(werkstatt, "A", STOP_LOSS_PCT=5.0)


@then("steht ein StrategyDefinitionEvent auf der Audit-Kette")
def definition_auf_kette(werkstatt):
    from core import strategie_profile as sp

    (eintrag,) = [
        z for z in _kette(werkstatt.log_dir) if z["event_type"] == "strategy_definition"
    ]
    assert (eintrag["aktion"], eintrag["name"]) == ("create", "A")
    assert eintrag["werte"] == sp.werte_fuer_kette({"STOP_LOSS_PCT": "5.0"})


@then("die Kette verifiziert")
def kette_verifiziert(werkstatt, offen):
    zeilen = _kette(werkstatt.log_dir)
    assert zeilen, "die Kette ist leer"
    _pruefe_kette(zeilen)
    with pytest.raises(AssertionError, match="Hash stimmt nicht"):
        _pruefe_kette([{**zeilen[0], "hash": "0" * 64}, *zeilen[1:]])
    _abschluss(offen)


# ── Szenario 2: Drei Strategien im Sim vergleichen (#4634, #3712, #4635) ─────


#: Der Stichtag der Pipeline und der Zeitraum darin; so bleibt der Korpus über die Jahre gleich.
HEUTE, BEGINN, ENDE = date(2026, 3, 23), date(2026, 1, 5), date(2026, 3, 20)
#: Hier trennen Schwelle und Kauf-Tempo die Kurven; dass ein Gewicht allein sie trennt, prueft
#: das letzte Szenario dieser Datei (#5083). Der Fake-Client liefert je Symbol dieselbe Rendite,
#: die Endwerte sagen also nichts ueber eine Strategie.
SIM_STRATEGIEN = {
    "A": {"SIGNAL_BUY_THRESHOLD": 0.55},
    "B": {
        "SIGNAL_BUY_THRESHOLD": 0.55,
        "GLOBAL_BUY_COOLDOWN_MINUTES": 0,
        "MAX_BUYS_PER_HOUR": 3,
    },
    "C": {"SIGNAL_BUY_THRESHOLD": 0.75},
}


def _sim_lauf(lid: str, eingabe_bytes: bytes) -> bytes:
    """Ein Lauf über den echten Subprozess; liefert die rohe ``ergebnis.json``."""
    from core.sim import vergleich

    prozess = asyncio.run(vergleich.starte(lid, eingabe_bytes))
    ordner = vergleich.ablage_verzeichnis() / lid
    assert prozess.wait(timeout=300) == 0, (ordner / "protokoll.log").read_text(
        encoding="utf-8", errors="replace"
    )
    return (ordner / vergleich.ERGEBNIS).read_bytes()


def _handelt(roh: dict):
    """Eine Abnahme ohne Order wäre eine reine Formprobe (#5044)."""

    def pruefung():
        strategien = roh["strategien"]
        if not any(
            o["status"] == "filled" for s in strategien for o in s["ergebnis"]["orders"]
        ):
            gruende = Counter(
                (e["aktion"], e["grund"])
                for s in strategien
                for e in s["entscheidungen"]
            )
            raise FehlendeFunktion(
                f"Die Simulation handelt auf dem Korpus nicht: {dict(gruende)} — #5044"
            )
        kurven = {json.dumps(s["ergebnis"]["daily_equity"]) for s in strategien}
        if len(kurven) != len(strategien):
            raise FehlendeFunktion(
                "Strategien mit verschiedenen Settings rechnen dasselbe Ergebnis — #5044"
            )

    return pruefung


@given("drei Strategien und ein Zeitraum innerhalb der letzten drei Jahre")
def drei_strategien_und_zeitraum(werkstatt, monkeypatch):
    from core.sim import vergleich, vorbereitung
    from core.sim.stimmen_rechner import OfflineStimmenRechner
    from tests.unit.test_4634_datenvorbereitung import FakeClient

    # Ersetzt ist nur die Broker-Grenze: Schlüsselbund und Alpaca-Client.
    monkeypatch.setattr(vorbereitung, "lade_keys", lambda: ("key", "secret"))
    monkeypatch.setattr(vergleich, "_LAEUFE", {})
    rc = vorbereitung.main(
        ["--korpus", str(vergleich.korpus_verzeichnis()), "--symbols", "AAPL,MSFT,SPY"],
        client=FakeClient(),
        rechner=OfflineStimmenRechner(),
        heute=HEUTE,
    )
    assert rc == vorbereitung.EXIT_OK
    for name, werte in SIM_STRATEGIEN.items():
        _anlegen(werkstatt, name, **werte)


@when("der Nutzer die Simulation startet")
def simulation_startet(werkstatt):
    from core import strategie_profile as sp
    from core.sim import vergleich

    bestand = sp.lade_strategien()
    eingabe = vergleich.VergleichsEingabe(
        beginn=BEGINN, ende=ENDE, strategien=list(SIM_STRATEGIEN), startkapital=100_000
    )
    vergleich.pruefe_eingabe(eingabe, HEUTE, None, list(bestand))
    vergleich.pruefe_korpus(BEGINN, ENDE)
    werkstatt.eingabe_bytes = vergleich.kanonisch(eingabe, bestand)
    werkstatt.lauf_id = vergleich.lauf_id(werkstatt.eingabe_bytes)
    werkstatt.roh = _sim_lauf(werkstatt.lauf_id, werkstatt.eingabe_bytes)


@then(
    "erhält jede Strategie Rendite (TWR), maximalen Drawdown und SPY-Vergleich "
    "für genau diesen Zeitraum"
)
def kennzahlen_je_strategie(werkstatt, offen):
    from core.sim import vergleich

    antwort = vergleich.lies_ergebnis(werkstatt.lauf_id)
    assert antwort["status"] == "fertig", antwort
    ergebnis = antwort["ergebnis"]
    assert ergebnis["zeitraum"] == {
        "beginn": BEGINN.isoformat(),
        "ende": ENDE.isoformat(),
    }
    assert [z["name"] for z in ergebnis["strategien"]] == list(SIM_STRATEGIEN)
    for zeile in ergebnis["strategien"]:
        for feld in ("twr_pct", "max_drawdown_pct", "spy_twr_pct"):
            assert isinstance(zeile[feld], float), (zeile["name"], feld, zeile[feld])

    roh = json.loads(werkstatt.roh)
    for strategie in roh["strategien"]:
        tage = [p["date"] for p in strategie["ergebnis"]["daily_equity"]]
        assert tage, strategie["strategie"]
        assert BEGINN.isoformat() <= tage[0] and tage[-1] <= ENDE.isoformat()
    _probe(offen, _handelt(roh))


@then("zwei Läufe mit gleicher Eingabe sind bitgleich")
def laeufe_bitgleich(werkstatt):
    zweiter = _sim_lauf(werkstatt.lauf_id, werkstatt.eingabe_bytes)
    assert hashlib.sha256(zweiter).digest() == hashlib.sha256(werkstatt.roh).digest()


@then(
    "jedes Ergebnis zeigt Pflichthinweis, Zeitraum, Unsicherheit und nicht "
    "abgebildete Teile"
)
def ergebnis_mit_pflichtangaben(werkstatt, offen):
    from core.sim import vergleich

    ergebnis = vergleich.lies_ergebnis(werkstatt.lauf_id)["ergebnis"]
    assert ergebnis["unsicherheit"]["handelstage"] > 0
    assert {"art": "ohne_lstm"} in ergebnis["nicht_abgebildet"]

    def pruefung():
        # #4918: Start- und Ergebnisroute (#4917) sind eingebunden; das Ergebnis trägt
        # Zeitraum, Unsicherheit und nicht abgebildete Teile (``vergleich.lies_ergebnis``,
        # #4635). Den Pflichthinweis zeigt die Oberfläche
        # (strategySimulationCompare.test.tsx, A5-P1).
        _braucht("core.sim.vergleich", "lies_ergebnis", "4635")
        routen = _routen()
        for route in (
            ("POST", "/api/strategy-sim/runs"),
            ("GET", "/api/strategy-sim/runs/{run_id}"),
        ):
            if route not in routen:
                raise FehlendeFunktion(f"{route} fehlt — #4917")

    _probe(offen, pruefung)
    _abschluss(offen)


# ── Szenario 3: Live-Strategie zuweisen (#4629, #4630) ───────────────────────


@when('der Nutzer in den Settings Strategie "B" dem Live-Konto zuweist')
def strategie_b_zuweisen(werkstatt):
    from core import trading_settings as ts

    _anlegen(werkstatt, "B", MOMENTUM_AGENT_WEIGHT=0.50)
    werkstatt.vorher = ts.current_settings()
    antwort = _zuweisen(werkstatt, "B")
    assert antwort.status_code == 201, antwort.text
    werkstatt.changes = antwort.json()["changes"]


def _zuweisen(werkstatt, name: str):
    return werkstatt.client.post(
        f"/api/strategies/{name}/assign-live",
        json={"acknowledgment": "bestaetigt", "nonce": f"zuweisen-{name}"},
    )


def _zuweisungen(log_dir: pathlib.Path) -> list[dict]:
    return [z for z in _kette(log_dir) if z["event_type"] == "strategy_assignment"]


@then("steht ein StrategyAssignmentEvent vor dem Wirksamwerden auf der Kette")
def zuweisung_vor_wirksamwerden(werkstatt, monkeypatch):
    from core import hitl_gate
    from core import strategie_profile as sp
    from core import trading_settings as ts

    (eintrag,) = _zuweisungen(werkstatt.log_dir)
    assert (eintrag["strategie"], eintrag["konto"]) == ("B", "live")
    _pruefe_kette(_kette(werkstatt.log_dir))
    # Wirksam wird erst der Neustart: Die Route ändert die Engine-Werte nicht.
    assert ts.current_settings() == werkstatt.vorher
    assert sp.lade_zuweisungen()["live"]["strategie"] == "B"

    # Gegenprobe: Ohne Eintrag auf der Kette gibt es keine Zuweisung und keinen Spiegel.
    _anlegen(werkstatt, "C", MOMENTUM_AGENT_WEIGHT=0.60)
    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_assignment_event",
        AsyncMock(side_effect=OSError("Kette nicht schreibbar")),
    )
    antwort = _zuweisen(werkstatt, "C")
    assert antwort.status_code == 503, antwort.text
    assert antwort.json()["code"] == "audit_write_failed"
    assert sp.lade_zuweisungen()["live"]["strategie"] == "B"


#: Kindprozess wie der Engine-Start: ``import config`` liest die Umgebung, die die Shell setzt.
ENGINE_KIND = r"""
try:
    import torch
except ImportError:
    pass
import json

import config
from core import trading_settings as ts

print("ERGEBNIS " + json.dumps(
    {
        "hitl": config.HITL_ENABLED is True,
        "paper": config.PAPER_TRADING,
        "werte": ts.current_settings(),
    }
), flush=True)
"""


@then('die Engine startet mit den Settings von "B" und aktivem HITL')
def engine_mit_b_und_hitl(werkstatt, tmp_path, offen):
    changes = {c["key"]: c["to"] for c in werkstatt.changes}
    assert changes.get("MOMENTUM_AGENT_WEIGHT") == "0.50", changes

    durchreichung = SETUP_MANAGER.read_text(encoding="utf-8")
    fehlend = [k for k in changes if f'"{k}"' not in durchreichung]
    assert not fehlend, f"{SETUP_MANAGER.name} reicht nicht durch: {fehlend}"

    env = {**_umgebung(tmp_path, 1, "PA-LIVE"), **changes}
    env.update(PAPER_TRADING="False", HITL_ENABLED="true")
    lauf = subprocess.run(
        [sys.executable, "-c", ENGINE_KIND],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr
    zeile = next(z for z in lauf.stdout.splitlines() if z.startswith("ERGEBNIS "))
    start = json.loads(zeile[len("ERGEBNIS ") :])
    assert (start["hitl"], start["paper"]) == (True, False)
    assert {k: start["werte"][k] for k in changes} == changes
    _abschluss(offen)


# ── Szenario 4: Paper-Konten laufen parallel (#4631, #4632) ──────────────────


def _konto_id_im_schluessel():
    from core.lease import konto_schluessel

    parameter = inspect.signature(konto_schluessel).parameters
    if not {"konto_id", "account_id"} & set(parameter):
        raise FehlendeFunktion(
            "core.lease.konto_schluessel kennt keine Alpaca-Konto-ID "
            f"(nur {', '.join(parameter)}) — #4631"
        )


@given("drei Paper-Profile mit je eigenem Alpaca-Konto")
def drei_paper_profile(offen):
    _probe(offen, _konto_id_im_schluessel)


@pytest.fixture
def flotte(tmp_path):
    """Die Engine-Kindprozesse eines Szenarios; sie halten ihre Berechtigung bis zum Ende."""
    arbeit = tmp_path / "arbeit"
    arbeit.mkdir()
    prozesse: list = []

    def starte(nr: int, konto_id: str) -> dict:
        prozess = _starte(tmp_path, arbeit, nr, konto_id)
        prozesse.append(prozess)
        return _ergebnis(prozess)

    yield starte
    _beende(*prozesse)


@then("laufen drei Engines nebeneinander")
def drei_engines(flotte):
    for nr, konto in enumerate(PAPER_KONTEN, start=1):
        ergebnis = flotte(nr, konto)
        assert (ergebnis["schluessel"], ergebnis["berechtigt"]) == (
            f"alpaca:{konto}",
            True,
        )
    # Die Flotte selbst (Ports, Verzeichnisse, Slots) prüft „Desktop Shell Tests"; der
    # BDD-Container hat kein Node, deshalb hier nur die Text-Brücke.
    assert 'test("planProfiles vergibt Ports 8001–8004' in ENGINE_FLOTTE.read_text(
        encoding="utf-8"
    )


@then("ein weiteres Profil mit derselben Konto-ID wird abgewiesen")
def doppelte_konto_id_abgewiesen(flotte, offen):
    ergebnis = flotte(len(PAPER_KONTEN) + 1, PAPER_KONTEN[0])
    assert (ergebnis["schluessel"], ergebnis["berechtigt"]) == (
        f"alpaca:{PAPER_KONTEN[0]}",
        False,
    )
    _abschluss(offen)


# ── Szenario 5: Vergleich im Betrieb (#4633) ─────────────────────────────────


@then(
    "zeigt die Vergleichsansicht die Rendite (TWR) je Konto für Live und Paper "
    "nebeneinander"
)
def vergleich_im_betrieb(offen):
    # Die Zahl rechnet das Frontend (compareAccounts.ts) aus dieser Route; dass sie zur
    # Engine passt, prüft T3 in compareAccounts4633.test.ts.
    _probe(offen, _bruecke("A6-P1:", "#4633"))
    _probe(offen, _bruecke("A6-P2:", "#4633"))
    assert ("GET", "/benchmark-equity") in _routen()
    _abschluss(offen)


# ── Szenario 6: Neutrale Darstellung (#4629; Ansichten #4635/#4636) ──────────


@then(
    'enthält keine Ansicht der Werkstatt eine Rangfolge, "best", "recommended" '
    "oder einen Übernehmen-Knopf"
)
def ansichten_neutral(offen):
    _probe(offen, _bruecke("STW-N:", "#4635/#4636"))


@then("/set-strategy schaltet nicht mehr unauditiert um")
def set_strategy_auditiert(offen, monkeypatch, tmp_path):
    from core import hitl_gate
    from core.engine import api_routes as ar
    from core.engine.routes import steuerung
    from core.round_table.senate_log import LocalJSONAuditLogger

    log_dir = tmp_path / "senate"
    log_dir.mkdir()
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    monkeypatch.setattr(ar, "config", SimpleNamespace(ACTIVE_STRATEGY="RLAgent"))

    antwort = asyncio.run(steuerung.set_strategy({"strategy": "LSTMDynamic"}, None))
    assert antwort == {"status": "success", "strategy": "LSTMDynamic"}, antwort
    assert ar.config.ACTIVE_STRATEGY == "LSTMDynamic"

    def pruefung():
        if not any(p.is_file() for p in log_dir.rglob("*")):
            raise FehlendeFunktion("/set-strategy schreibt kein Audit-Event — #4629")

    _probe(offen, pruefung)
    _abschluss(offen)


# ── Szenario 7: Bedienfluss folgt dem UX-Konzept (#4636) ─────────────────────


@given("das abgenommene UX-Konzept")
def ux_konzept(offen):
    text = UX_KONZEPT.read_text(encoding="utf-8")
    for abschnitt in ("## Abläufe", "## Zustände", "## Abweichungen"):
        assert f"\n{abschnitt}\n" in text, f"{UX_KONZEPT.name}: {abschnitt} fehlt"


@then(
    "sind die Abläufe Anlegen, Simulieren, Vergleichen und Zuweisen im "
    "Frontend-Integrationstest durchgängig bedienbar"
)
def ablaeufe_bedienbar(offen):
    _probe(offen, _bruecke("STW-B:", "#4636"))


@then(
    "jeder im Konzept genannte Zustand (leer, lädt, Fehler, keine Keys, Konto "
    "doppelt) ist abgebildet"
)
def zustaende_abgebildet(offen):
    # Die Zustände gehören zu STW-B; ein zweiter vitest-Name wäre eine zweite Wahrheit.
    _probe(offen, _bruecke("STW-B:", "#4636"))
    _abschluss(offen)


# ── Szenario 8: Die Simulation rechnet den Round Table wie live (#5083) ──────

#: Gemessen auf dem Fake-Korpus: MSFT stimmt in Valuation an jedem Tag des Zeitraums mit.
GREMIUM_SYMBOLE = "AAPL,MSFT,JPM,KO,SPY"
#: Zwei Strategien, die sich nur im Gewicht eines Agenten ausser Momentum und Trend trennen.
GREMIUM_STRATEGIEN = {
    "Q1": {
        "SIGNAL_BUY_THRESHOLD": 0.55,
        "QUALITY_AGENT_ENABLED": True,
        "QUALITY_AGENT_WEIGHT": 0.1,
    },
    "Q3": {
        "SIGNAL_BUY_THRESHOLD": 0.55,
        "QUALITY_AGENT_ENABLED": True,
        "QUALITY_AGENT_WEIGHT": 0.3,
    },
}


@given("ein Korpus aus der Datenpipeline")
def korpus_aus_pipeline(werkstatt, monkeypatch):
    from core.sim import vergleich, vorbereitung
    from tests.unit.test_4634_datenvorbereitung import FakeClient

    monkeypatch.setattr(vorbereitung, "lade_keys", lambda: ("key", "secret"))
    monkeypatch.setattr(vergleich, "_LAEUFE", {})
    rc = vorbereitung.main(
        ["--korpus", str(vergleich.korpus_verzeichnis()), "--symbols", GREMIUM_SYMBOLE],
        client=FakeClient(),
        heute=HEUTE,
    )
    assert rc == vorbereitung.EXIT_OK
    for name, werte in GREMIUM_STRATEGIEN.items():
        _anlegen(werkstatt, name, **werte)


@when(
    "die Strategie-Simulation zwei Strategien vergleicht, die sich nur im Gewicht "
    "eines abgebildeten Agenten außer Momentum und Trend unterscheiden"
)
def zwei_strategien_vergleichen(werkstatt):
    from core import strategie_profile as sp
    from core.sim import vergleich

    bestand = sp.lade_strategien()
    eingabe = vergleich.VergleichsEingabe(
        beginn=BEGINN,
        ende=ENDE,
        strategien=list(GREMIUM_STRATEGIEN),
        startkapital=100_000,
    )
    vergleich.pruefe_korpus(BEGINN, ENDE)
    eingabe_bytes = vergleich.kanonisch(eingabe, bestand)
    lid = vergleich.lauf_id(eingabe_bytes)
    werkstatt.gremium = json.loads(_sim_lauf(lid, eingabe_bytes))


@then("trägt jeder Handelstag die Stimmen aller abgebildeten Agenten")
def jeder_tag_alle_agenten():
    import pandas as pd

    from core.sim import vergleich
    from core.sim.stimmen_rechner import GEWICHTE

    stimmen = vergleich.korpus_verzeichnis() / "stimmen"
    lang = pd.read_parquet(stimmen / "votes.parquet").merge(
        pd.read_parquet(stimmen / "decisions.parquet"), on="did"
    )
    tage = lang["ts"].str[:10]
    zeitraum = lang[(tage >= BEGINN.isoformat()) & (tage <= ENDE.isoformat())]
    je_tag = zeitraum.groupby(zeitraum["ts"].str[:10])["agent"].agg(set)
    assert len(je_tag) > 0
    for tag, agenten in je_tag.items():
        assert agenten == set(GEWICHTE), (tag, sorted(set(GEWICHTE) - agenten))


@then("die Simulation erzeugt Orders")
def simulation_erzeugt_orders(werkstatt):
    for strategie in werkstatt.gremium["strategien"]:
        orders = strategie["ergebnis"]["orders"]
        assert any(o["status"] == "filled" for o in orders), strategie["strategie"]


@then("die beiden Ergebniskurven unterscheiden sich")
def ergebniskurven_unterscheiden_sich(werkstatt, offen):
    a, b = [s["ergebnis"]["daily_equity"] for s in werkstatt.gremium["strategien"]]
    assert a != b
    _abschluss(offen)
