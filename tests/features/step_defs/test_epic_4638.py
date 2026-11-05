"""Abnahme auf Epic-Ebene für #4638 — scharf geschaltet (#4664).

Vom Maker als Platzhalter angelegt (#3785), hier durch echte Prüfungen ersetzt. Der
erwartete Fehlschlag am Modulkopf ist entfallen; ab jetzt ist diese Abnahme ein Tor.

Geprüft wird der Pfad, den die Sub-Issues gebaut haben, im Zusammenspiel (Plan Option A):
gespeicherte Strategie (``core.strategie_profile``, #4654) → LLM-Lauf über die Engine
(``zyklus_kontext._llm_lauf_ausloesen`` → ``llm_ranking.frage_llm_ranking``, #4648/#4652) →
Lauf-Protokoll (``LaufSpeicher``, #4650) → Rang-Trichter (``_kontext_rang_trichter``, #4652)
→ QA-Trichter-Endpunkt (``GET /llm-vorauswahl/trichter``, #4656).

Ersetzt sind nur Dinge außerhalb des geprüften Pfads:

- der LLM-Anbieter durch einen Stub mit ``generate_content_async`` — über die Einspeisung,
  die die Engine dafür hat (``_llm_ranking_provider``; unter SIM_MODE nur Stub oder Ollama),
- das LSTM-Panel (``get_store``/``active_cross_section``) durch feste Werte,
- der Round-Table-Graph und der Order-Pfad: deren Ergebnis wird über die echten Ablagen
  ``record_round_table_decision`` und ``record_execution_outcome`` eingetragen, und zwar nur
  für Symbole, die der Rang-Trichter tatsächlich in die Tiefenbewertung gelassen hat.

Ein Zyklus startet einen fälligen LLM-Lauf außerhalb des Zyklus (Single-Flight-Task, #4652);
der Schritt wartet diesen Task ab und lässt den nächsten Zyklus die Liste lesen — so, wie es
die Engine im Betrieb tut.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import logging
import os
import pathlib
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../epic_4638.feature")

pytestmark = [pytest.mark.vc0]

PAKET = pathlib.Path(__file__).resolve().parents[3]  # ai_trading_bot/
_KEY = "test-engine-key-4638"
PROMPT = "Unternehmen mit wachsendem freien Cashflow und niedriger Verschuldung"

#: 25 gültige LLM-Vorschläge — alle im aktiven Universum.
GUELTIG = (
    "AAPL MSFT NVDA AMZN GOOGL META AVGO COST PEP ADBE "
    "CSCO TXN QCOM INTU AMAT ISRG BKNG ADP VRTX REGN "
    "PANW SNPS CDNS KLAC LRCX"
).split()
#: 3 erfundene Ticker: wohlgeformt, aber es gibt sie nicht.
ERFUNDEN = ["QXZT", "ZZYQ", "XPLRQ"]
#: 2 echte Ticker außerhalb des aktiven Universums.
FREMD = ["SAP", "ASML"]
#: 2 gehaltene Positionen außerhalb der LLM-Liste.
GEHALTEN = ["JNJ", "XOM"]
#: Weitere Symbole des Universums, die weder gehalten noch vorgeschlagen sind.
REST = ["KO", "WMT", "DIS", "NKE", "MCD"]
UNIVERSUM = GUELTIG + GEHALTEN + REST
#: Antwort des Stubs: 30 Ticker, gemischt, in Rangfolge.
ANTWORT = GUELTIG[:10] + ERFUNDEN + GUELTIG[10:20] + FREMD + GUELTIG[20:]

#: Szenario 2 — kleineres Universum, LSTM-Top-5 disjunkt zur LLM-Liste.
U2_LLM = ["AAPL", "MSFT", "NVDA"]
U2_GEHALTEN = ["JNJ", "XOM"]
U2_LSTM = ["KO", "WMT", "DIS", "NKE", "MCD"]
U2_UNIVERSUM = U2_LLM + U2_GEHALTEN + U2_LSTM
U2_XSEC = dict.fromkeys(U2_LLM + U2_GEHALTEN, 0.1) | {
    s: float(len(U2_UNIVERSUM) - i) for i, s in enumerate(U2_LSTM)
}


# ---------------------------------------------------------------- Werkzeuge


@pytest.fixture
def k(tmp_path):
    """Kontext eines Szenarios; leert die Anzeige-Ablagen davor und danach."""
    from core.round_table import execution_outcomes, recent_decisions

    recent_decisions.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()
    yield SimpleNamespace(tmp=tmp_path, reihenfolgen=[])
    recent_decisions.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()


def _strategie_cfg(tmp_path, top_k: int, **extra):
    """Die Strategie wird gespeichert und zurückgelesen; ihre Werte bilden die Konfiguration."""
    from core.strategie_profile import (
        lade_strategien,
        pruefe_strategie,
        speichere_strategien,
    )

    werte = pruefe_strategie(
        "LLM-Vorauswahl",
        {
            "LLM_RANKING_QUELLE": "llm",
            "LLM_RANKING_PROMPT": PROMPT,
            "LLM_RANKING_RHYTHMUS": "je_refresh",
        },
    )
    ablage = tmp_path / "strategien.json"
    speichere_strategien({"LLM-Vorauswahl": werte}, ablage)
    gespeichert = lade_strategien(ablage)["LLM-Vorauswahl"]
    basis = {
        "SIM_MODE": False,
        "LLM_RANKING_ENABLED": True,
        "ROUND_TABLE_TOP_K_EVAL": top_k,
        "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 3,
        "ROUND_TABLE_RANK_REFRESH_MINUTES": 120,
        "LLM_RANKING_LISTE": "",
    }
    basis.update(gespeichert)
    basis.update(extra)
    return SimpleNamespace(**basis)


def _engine(speicher, provider):
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m.api = MagicMock()
    m._rank_panel_producer = None
    m._llm_lauf_speicher = speicher
    m._llm_ranking_provider = provider
    return m


def _zustand(universum, gehalten, jetzt):
    from core.engine.zyklus import ZyklusZustand

    z = ZyklusZustand()
    z.current_time_utc = jetzt
    z.symbols_to_process = list(universum)
    z.symbols_order = list(universum)
    z.positions_by_symbol = {s: {"qty": 1.0} for s in gehalten}
    z.position_confirmed = True
    return z


def _store(latest=None):
    store = MagicMock()
    store.latest_snapshot_date.return_value = latest
    return store


def _zyklus(m, z, cfg, store, xsec=None):
    """Ein Rang-Trichter-Durchlauf; ein dabei gestarteter LLM-Lauf wird abgewartet."""

    async def _lauf():
        m._kontext_rang_trichter(z)
        task = getattr(m, "_llm_ranking_task", None)
        if task is not None:
            await task

    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.report.lstm_panel_store.get_store", return_value=store
    ), patch(
        "core.report.lstm_panel_store.active_cross_section",
        return_value=dict(xsec or {}),
    ):
        asyncio.run(_lauf())
    return list(z.symbols_order)


def _stub(antwort=None, fehler=None):
    """Stub an der LLM-Seam: dieselbe Schnittstelle wie die Provider in core/llm/provider.py."""
    stub = SimpleNamespace(model="stub-modell-4638")
    stub.generate_content_async = AsyncMock(return_value=antwort, side_effect=fehler)
    return stub


# ---------------------------------------------------------------- Szenario 1


@given(
    "eine Strategie mit der Ranking-Quelle 'llm', einem gespeicherten Prompt und dem Rhythmus 'je Ranking-Refresh'"
)
def schritt_1(k):
    from core.engine.llm_ranking_lauf import Rhythmus
    from core.engine.zyklus_kontext import _llm_feld, _llm_ranking_aktiv, _llm_rhythmus

    k.cfg = _strategie_cfg(k.tmp, top_k=60, SIM_MODE=True)
    assert _llm_ranking_aktiv(k.cfg), "die Strategie schaltet die LLM-Quelle nicht ein"
    assert _llm_feld(k.cfg, "LLM_RANKING_PROMPT") == PROMPT
    assert _llm_rhythmus(k.cfg) is Rhythmus.JE_REFRESH


@given(
    "ein Stub-Provider an der LLM-Seam core/llm/provider.py liefert 30 Ticker, davon 3 erfunden und 2 nicht im aktiven Universum"
)
def schritt_2(k):
    assert len(ANTWORT) == 30 and len(set(ANTWORT)) == 30
    assert sum(t in UNIVERSUM for t in ANTWORT) == 25
    k.provider = _stub(antwort=", ".join(ANTWORT))


@given("das Depot hält 2 Positionen außerhalb der LLM-Liste")
def schritt_3(k):
    assert not set(GEHALTEN) & set(ANTWORT)
    k.gehalten = list(GEHALTEN)


@when("ein Simulationszyklus mit AAA_SIM_MODE=true läuft")
def schritt_4(k, monkeypatch):
    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.sim import is_sim_mode
    from core.sim.clock import engine_now

    monkeypatch.setenv("AAA_SIM_MODE", "true")
    assert is_sim_mode()
    k.protokoll = k.tmp / "llm_ranking_laeufe.jsonl"
    k.engine = _engine(LaufSpeicher(k.protokoll), k.provider)
    k.jetzt = engine_now(timezone.utc)

    # Zyklus 1 startet den fälligen Lauf außerhalb des Zyklus; er liest noch keine Liste.
    _zyklus(k.engine, _zustand(UNIVERSUM, k.gehalten, k.jetzt), k.cfg, _store())
    k.provider.generate_content_async.assert_awaited_once()
    # Zyklus 2 liest die Liste des Laufs.
    k.z = _zustand(UNIVERSUM, k.gehalten, k.jetzt)
    k.deep_eval = _zyklus(k.engine, k.z, k.cfg, _store())
    assert k.provider.generate_content_async.await_count == 1, "Lauf doppelt gestartet"


@then(
    "enthält der Deep-Eval-Satz (z.symbols_order nach _kontext_rang_trichter) genau die 2 gehaltenen Positionen und die 25 gültigen LLM-Ticker"
)
def schritt_5(k):
    assert k.z.rang_quelle == "llm"
    assert len(k.deep_eval) == len(set(k.deep_eval)) == 27
    assert set(k.deep_eval) == set(GEHALTEN) | set(GUELTIG)


@then("kein erfundener und kein universumsfremder Ticker steht im Deep-Eval-Satz")
def schritt_6(k):
    assert not set(k.deep_eval) & (set(ERFUNDEN) | set(FREMD))
    assert set(k.deep_eval) <= set(UNIVERSUM)


@then(
    "das Lauf-Protokoll enthält Zeitpunkt, Prompt-Hash, Modell, Anbieter, 25 gelieferte und 5 verworfene Ticker mit Grund"
)
def schritt_7(k):
    from core.engine.llm_ranking_lauf import LaufSpeicher

    assert k.protokoll.is_file(), "der Lauf wurde nicht ins Protokoll geschrieben"
    lauf = LaufSpeicher(k.protokoll).letzter_erfolg()  # frisch von der Platte gelesen
    assert lauf is not None and lauf.ok
    assert k.z.llm_lauf_id == lauf.lauf_id
    assert lauf.zeitpunkt_utc == k.jetzt
    assert lauf.prompt_hash == hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    assert lauf.modell == "stub-modell-4638"
    assert lauf.anbieter == "SimpleNamespace"
    assert lauf.sim is True
    assert lauf.geliefert == [t for t in ANTWORT if t in UNIVERSUM]
    assert set(lauf.verworfen) == set(ERFUNDEN) | set(FREMD)
    gruende = {"formfehler", "ueber_top_k", "unbekannt", "nicht_im_universum"}
    assert all(g in gruende for g in lauf.verworfen.values()), lauf.verworfen


@then(
    "der QA-Trichter-Endpunkt weist alle 30 Vorschläge mit Endstatus aus: verworfen vor dem Funnel, abgelehnt am Round Table, abgelehnt am Iron Dome mit Grund oder zugelassen"
)
def schritt_8(k):
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes
    from core.engine.llm_trichter import (
        ABGELEHNT_IRON_DOME,
        ABGELEHNT_ROUND_TABLE,
        VERWORFEN_VORAB,
        ZUGELASSEN,
    )
    from core.round_table.execution_outcomes import record_execution_outcome
    from core.round_table.recent_decisions import record_round_table_decision

    # Stellvertreter für Round Table und Order-Pfad — nur für den Deep-Eval-Satz.
    t = k.jetzt + timedelta(seconds=1)
    for i, sym in enumerate(k.deep_eval):
        aktion = "HOLD" if i % 3 == 0 else "BUY"
        record_round_table_decision(
            {"symbol": sym, "signal_action": aktion, "timestamp": t.isoformat()}
        )
        if aktion == "BUY":
            code = "blocked:max_position_pct" if i % 3 == 1 else "executed"
            record_execution_outcome(sym, code, "Abnahme #4638", ts=t.timestamp())

    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
        api_routes, "engine", k.engine
    ), patch.object(api_routes, "config", SimpleNamespace(get_config=lambda: k.cfg)):
        r = TestClient(api_routes.app).get(
            "/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY}
        )
    assert r.status_code == 200, r.text
    k.trichter = r.json()
    symbole = {e["symbol"]: e for e in k.trichter["symbole"]}
    assert set(symbole) == set(ANTWORT), "nicht jeder der 30 Vorschläge ist ausgewiesen"
    assert k.trichter["n_vorgeschlagen"] == 30
    endstatus = {
        VERWORFEN_VORAB,
        ABGELEHNT_ROUND_TABLE,
        ABGELEHNT_IRON_DOME,
        ZUGELASSEN,
    }
    for sym, e in symbole.items():
        assert e["status"] in endstatus, (sym, e)
        assert e["grund"], f"{sym}: Endstatus ohne Grund"
    for sym in ERFUNDEN + FREMD:
        assert symbole[sym]["status"] == VERWORFEN_VORAB
    # Jeder Endstatus kommt vor — die Zuordnung ist kein Einheitswert.
    assert {e["status"] for e in symbole.values()} == endstatus
    assert all(
        e["grund"].startswith("blocked:")
        for e in symbole.values()
        if e["status"] == ABGELEHNT_IRON_DOME
    )


@then(
    "die Zweck-Kennzahl 'Anteil gültiger LLM-Vorschläge in Tiefenbewertung und Trichter' ist 100 %"
)
def schritt_9(k):
    from core.engine.llm_trichter import ENDSTATUS, NOCH_NICHT_BEWERTET

    # Nenner unabhängig vom geprüften Code: die Antwort des Stubs ∩ das aktive Universum.
    gueltig = [t for t in ANTWORT if t in UNIVERSUM]
    mit_endstatus = {
        e["symbol"]
        for e in k.trichter["symbole"]
        if e["status"] in ENDSTATUS - {NOCH_NICHT_BEWERTET}
    }
    erreicht = [t for t in gueltig if t in k.deep_eval and t in mit_endstatus]
    anteil = 100.0 * len(erreicht) / len(gueltig)
    erfunden_im_deep_eval = sum(t in k.deep_eval for t in ERFUNDEN)
    print(
        f"Zweck-Kennzahl #4638: {anteil:.0f} % der gültigen LLM-Vorschläge "
        f"({len(erreicht)}/{len(gueltig)}) in Tiefenbewertung und Trichter; "
        f"{erfunden_im_deep_eval} erfundene Ticker in der Tiefenbewertung"
    )
    assert len(gueltig) == 25
    assert anteil == 100.0
    assert erfunden_im_deep_eval == 0


@then(
    "das Modul der LLM-Ranking-Quelle importiert weder order_executor noch SignalCandidate"
)
def schritt_10():
    import core.engine.llm_ranking as quelle
    import core.engine.zyklus_kontext as kontext

    pfad = pathlib.Path(quelle.__file__)
    assert pfad == PAKET / "core" / "engine" / "llm_ranking.py"
    # Es ist das Modul, das die Engine für den Lauf benutzt.
    assert "from core.engine.llm_ranking import frage_llm_ranking" in pathlib.Path(
        kontext.__file__
    ).read_text(encoding="utf-8")
    namen = []
    for knoten in ast.walk(ast.parse(pfad.read_text(encoding="utf-8-sig"))):
        if isinstance(knoten, ast.Import):
            namen += [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            namen += [knoten.module or ""]
            namen += [f"{knoten.module}.{a.name}" for a in knoten.names]
    assert namen, "keine Importe gelesen"
    verboten = [
        n
        for n in namen
        if {"order_executor", "signal_candidate", "SignalCandidate"} & set(n.split("."))
    ]
    assert verboten == []


# ---------------------------------------------------------------- Szenario 2


@given(
    "ein erfolgreicher Lauf liegt 1 Stunde zurück und die Höchstdauer beträgt 4 Stunden"
)
def schritt_11(k, caplog):
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf, hoechstdauer
    from core.engine.zyklus_kontext import _llm_rhythmus
    from core.sim.clock import engine_now

    caplog.set_level(logging.WARNING)
    k.caplog = caplog
    k.cfg = _strategie_cfg(k.tmp, top_k=5)
    dauer = hoechstdauer(
        _llm_rhythmus(k.cfg), float(k.cfg.ROUND_TABLE_RANK_REFRESH_MINUTES)
    )
    assert dauer == timedelta(hours=4)
    k.jetzt = engine_now(timezone.utc)
    k.speicher = LaufSpeicher(k.tmp / "llm_ranking_laeufe.jsonl")
    k.speicher.protokolliere(
        LlmRankingLauf(
            zeitpunkt_utc=k.jetzt - timedelta(hours=1),
            prompt_hash=hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
            modell="stub-modell-4638",
            anbieter="SimpleNamespace",
            ok=True,
            geliefert=list(U2_LLM),
        )
    )
    k.erfolg_id = k.speicher.letzter_erfolg().lauf_id


@when("der nächste Lauf mit Timeout scheitert")
def schritt_12(k):
    k.provider = _stub(fehler=asyncio.TimeoutError())
    k.engine = _engine(k.speicher, k.provider)
    z = _zustand(U2_UNIVERSUM, U2_GEHALTEN, k.jetzt)
    k.reihenfolgen.append(_zyklus(k.engine, z, k.cfg, _store()))
    k.provider.generate_content_async.assert_awaited_once()
    letzter = k.speicher._laeufe[-1]
    assert letzter.ok is False and letzter.fehler == "timeout"


@then("bleibt die Liste des letzten erfolgreichen Laufs die Vorauswahl")
def schritt_13(k):
    from core.engine.llm_ranking_lauf import VERALTET_ABER_GUELTIG

    liste, status, _ = k.speicher.gueltige_liste(k.jetzt, timedelta(hours=4))
    assert (liste, status) == (U2_LLM, VERALTET_ABER_GUELTIG)
    z = _zustand(U2_UNIVERSUM, U2_GEHALTEN, k.jetzt)
    k.reihenfolgen.append(_zyklus(k.engine, z, k.cfg, _store()))
    assert z.rang_quelle == "llm" and z.llm_lauf_id == k.erfolg_id
    assert set(z.symbols_order) == set(U2_LLM) | set(U2_GEHALTEN)


@then(
    "nach Überschreiten der Höchstdauer liest der Trichter das LSTM-Ranking oder das ganze Universum"
)
def schritt_14(k):
    spaeter = k.jetzt + timedelta(hours=3, minutes=1)  # Alter 4 h 1 min
    # Panel frisch → LSTM-Top-5 ∪ Bestand.
    z = _zustand(U2_UNIVERSUM, U2_GEHALTEN, spaeter)
    k.reihenfolgen.append(
        _zyklus(k.engine, z, k.cfg, _store(spaeter.date()), xsec=U2_XSEC)
    )
    assert z.rang_quelle == "lstm" and z.llm_lauf_id is None
    assert set(z.symbols_order) == set(U2_LSTM) | set(U2_GEHALTEN)
    # Kein Panel → ganzes Universum.
    z = _zustand(U2_UNIVERSUM, U2_GEHALTEN, spaeter)
    k.reihenfolgen.append(_zyklus(k.engine, z, k.cfg, _store()))
    assert z.rang_quelle == "universum"
    assert z.symbols_order == U2_UNIVERSUM
    assert k.provider.generate_content_async.await_count == 1


@then("eine WARNING steht im Log und gehaltene Positionen bleiben in der Bewertung")
def schritt_15(k):
    warnungen = [
        r.getMessage() for r in k.caplog.records if r.levelno == logging.WARNING
    ]
    assert any("LlmRanking: Timeout" in w for w in warnungen), warnungen
    assert any("LLM-Liste abgelaufen" in w for w in warnungen), warnungen
    assert len(k.reihenfolgen) == 4
    for reihenfolge in k.reihenfolgen:
        assert set(U2_GEHALTEN) <= set(reihenfolge), reihenfolge
