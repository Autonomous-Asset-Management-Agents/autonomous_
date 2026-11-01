"""#4656 (Epic #4638) — ``GET /llm-vorauswahl/trichter``.

Plan: ``docs/4656-qa-trichter-daten-endstatus-und-grund-je-llm-vor/implementation_plan.md``.

Die Route liest den Lauf-Speicher der Engine, die Round-Table-Entscheidungen und die
Iron-Dome-Ergebnisse und liefert den Trichter des letzten erfolgreichen Laufs. Absicherung
wie ``/round-table-decisions``: ``require_engine_key``.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

_KEY = "test-engine-key-4656"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes
    from core.round_table import execution_outcomes, recent_decisions

    recent_decisions.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}):
        yield TestClient(api_routes.app), api_routes
    recent_decisions.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()


def test_route_braucht_engine_key(client):
    tc, _ = client
    r = tc.get("/llm-vorauswahl/trichter")
    assert r.status_code in (401, 403)


def test_route_form(client, tmp_path):
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf
    from core.round_table.execution_outcomes import record_execution_outcome
    from core.round_table.recent_decisions import record_round_table_decision
    from core.sim.clock import engine_now

    tc, api = client
    jetzt = engine_now(timezone.utc)
    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    speicher.protokolliere(
        LlmRankingLauf(
            zeitpunkt_utc=jetzt - timedelta(minutes=1),
            prompt_hash="h",
            modell="m",
            anbieter="a",
            ok=True,
            geliefert=["AAPL", "MSFT"],
            verworfen={"XYZ1": "unbekannt"},
            lauf_id="lauf-route",
        )
    )
    record_round_table_decision(
        {"symbol": "AAPL", "signal_action": "BUY", "timestamp": jetzt.isoformat()}
    )
    record_execution_outcome(
        "AAPL", "blocked:order_value", "Ordervolumen", ts=jetzt.timestamp()
    )

    with patch.object(api, "engine", SimpleNamespace(_llm_lauf_speicher=speicher)):
        r = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["lauf_id"] == "lauf-route"
    assert body["liste_status"] == "frisch"
    assert body["liste_alter_s"] == pytest.approx(60.0, abs=5.0)
    assert body["n_vorgeschlagen"] == 3
    assert body["n_universum"] == 2
    assert body["n_round_table"] == 1
    assert body["n_iron_dome"] == 0
    assert body["zaehlstufen"]["vorgeschlagen"] == 3
    je_symbol = {e["symbol"]: e for e in body["symbole"]}
    assert je_symbol["AAPL"] == {
        "symbol": "AAPL",
        "status": "abgelehnt_iron_dome",
        "grund": "blocked:order_value",
    }
    assert je_symbol["MSFT"]["status"] == "noch_nicht_bewertet"
    assert je_symbol["XYZ1"]["status"] == "verworfen_vorab"


def test_route_ohne_speicher_leerer_trichter(client):
    tc, api = client
    with patch.object(api, "engine", SimpleNamespace()):
        r = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY})

    assert r.status_code == 200
    body = r.json()
    assert body["liste_status"] == "keiner"
    assert body["symbole"] == []
    assert body["n_vorgeschlagen"] == 0
    assert body["n_universum"] == 0
    assert body["n_round_table"] == 0
    assert body["n_iron_dome"] == 0


def test_route_gherkin_szenario_4656(client, tmp_path):
    """Gherkin End-to-End ueber die Route GET /llm-vorauswahl/trichter (#4656)."""
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf
    from core.round_table.execution_outcomes import record_execution_outcome
    from core.round_table.recent_decisions import record_round_table_decision
    from core.sim.clock import engine_now

    tc, api = client
    jetzt = engine_now(timezone.utc)
    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")

    geliefert = [f"SYM{i}" for i in range(1, 26)]
    verworfen = {f"BAD{i}": "qualitaet" for i in range(1, 6)}
    speicher.protokolliere(
        LlmRankingLauf(
            zeitpunkt_utc=jetzt - timedelta(minutes=1),
            prompt_hash="h",
            modell="m",
            anbieter="a",
            ok=True,
            geliefert=geliefert,
            verworfen=verworfen,
            lauf_id="lauf-gherkin",
        )
    )

    for i in range(1, 21):
        record_round_table_decision(
            {
                "symbol": f"SYM{i}",
                "signal_action": "HOLD",
                "timestamp": jetzt.isoformat(),
            }
        )
    for i in range(21, 26):
        record_round_table_decision(
            {
                "symbol": f"SYM{i}",
                "signal_action": "BUY",
                "timestamp": jetzt.isoformat(),
            }
        )

    for i in range(21, 25):
        record_execution_outcome(
            f"SYM{i}", "executed", "order executed", ts=jetzt.timestamp()
        )
    record_execution_outcome(
        "SYM25", "max_position_pct", "blocked by iron dome", ts=jetzt.timestamp()
    )

    with patch.object(api, "engine", SimpleNamespace(_llm_lauf_speicher=speicher)):
        r = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY})

    assert r.status_code == 200
    body = r.json()
    assert body["n_vorgeschlagen"] == 30
    assert body["n_universum"] == 25
    assert body["n_round_table"] == 5
    assert body["n_iron_dome"] == 4


# ── #4861: Quelle "liste" — der Trichter aus dem Pruefstand des letzten Zyklus ──────────


def _liste_cfg():
    return SimpleNamespace(
        LLM_RANKING_QUELLE="liste",
        LLM_RANKING_LISTE="AAPL, SAP, 12X",
        LLM_RANKING_RHYTHMUS="je_refresh",
        ROUND_TABLE_RANK_REFRESH_MINUTES=120,
    )


def test_route_liste_aus_pruefstand(client):
    from core.sim.clock import engine_now

    tc, api = client
    jetzt = engine_now(timezone.utc) - timedelta(days=30)  # die Liste laeuft nicht ab
    stand = (jetzt, ["AAPL"], {"SAP": "nicht_im_universum", "12X": "formfehler"})
    with patch.object(api, "engine", SimpleNamespace(_liste_stand=stand)), patch.object(
        api.config, "get_config", return_value=_liste_cfg()
    ), patch("core.sim.is_sim_mode", return_value=True):
        r = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY})

    assert r.status_code == 200
    body = r.json()
    assert body["quelle"] == "liste"
    assert body["liste_status"] == "dauerhaft"
    assert body["hinweis"] == "liste_heutiges_wissen"
    assert body["belastbar"] is True
    je_sym = {e["symbol"]: e for e in body["symbole"]}
    assert je_sym["SAP"]["status"] == "verworfen_vorab"
    assert je_sym["12X"]["grund"] == "formfehler"
    assert je_sym["AAPL"]["status"] == "noch_nicht_bewertet"


@pytest.mark.parametrize(
    "engine", [SimpleNamespace(), SimpleNamespace(_liste_stand=None)]
)
def test_route_liste_ohne_stand_leerer_trichter(client, engine):
    tc, api = client
    with patch.object(api, "engine", engine), patch.object(
        api.config, "get_config", return_value=_liste_cfg()
    ):
        r = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY})

    assert r.status_code == 200
    body = r.json()
    assert body["symbole"] == []
    assert body["liste_status"] == "keiner"
    assert body["quelle"] == "liste"


# ── #4960: Websuche und Anbieter des Laufs ──────────────────────────────────────────────


def _lauf_body(client, tmp_path, **felder):
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf
    from core.sim.clock import engine_now

    tc, api = client
    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    speicher.protokolliere(
        LlmRankingLauf(
            zeitpunkt_utc=engine_now(timezone.utc),
            prompt_hash="h",
            modell="m",
            ok=True,
            geliefert=["AAPL"],
            **felder,
        )
    )
    with patch.object(api, "engine", SimpleNamespace(_llm_lauf_speicher=speicher)):
        return tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY}).json()


def test_trichter_meldet_websuche_anbieter_n_quellen(client, tmp_path):
    body = _lauf_body(
        client,
        tmp_path,
        anbieter="AnthropicProvider",
        kanonischer_anbieter="anthropic",
        websuche=True,
        quellen=["https://a.example/1", "https://b.example/2"],
    )
    assert (body["websuche"], body["anbieter"], body["n_quellen"]) == (
        True,
        "anthropic",
        2,
    )
    assert "quellen" not in body, "nur die Anzahl, die URLs bleiben im Protokoll"

    lokal = _lauf_body(
        client, tmp_path, anbieter="OllamaProvider", kanonischer_anbieter="ollama"
    )
    assert (lokal["websuche"], lokal["anbieter"], lokal["n_quellen"]) == (
        False,
        "ollama",
        0,
    )


def test_trichter_ohne_lauf_websuche_null(client):
    tc, api = client
    with patch.object(api, "engine", SimpleNamespace()):
        body = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY}).json()
    assert (body["websuche"], body["anbieter"], body["n_quellen"]) == (None, None, 0)


@pytest.mark.parametrize("mit_stand", [True, False])
def test_trichter_liste_websuche_null(client, mit_stand):
    from core.sim.clock import engine_now

    tc, api = client
    stand = (engine_now(timezone.utc), ["AAPL"], {}) if mit_stand else None
    with patch.object(api, "engine", SimpleNamespace(_liste_stand=stand)), patch.object(
        api.config, "get_config", return_value=_liste_cfg()
    ):
        body = tc.get("/llm-vorauswahl/trichter", headers={"X-Engine-Key": _KEY}).json()
    assert (body["websuche"], body["anbieter"], body["n_quellen"]) == (None, None, 0)
