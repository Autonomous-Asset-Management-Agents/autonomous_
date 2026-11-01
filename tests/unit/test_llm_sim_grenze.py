"""#4662 (Epic #4638) — Simulationsgrenze der LLM-Vorauswahl: Look-ahead-Kennzeichnung.

Plan: ``docs/4662-simulationsgrenze-der-llm-vorauswahl-ausweisen-l/implementation_plan.md``.

Ein LLM kennt die Zukunft eines vergangenen Zeitraums. Jeder Lauf unter SIM_MODE traegt deshalb
``sim=True``; Trichter und Sim-Vergleich weisen ein Ergebnis mit LLM-Quelle als nicht belastbar
aus (Grund ``llm_lookahead``). Die Kennzeichnung hat einen einzigen Ursprung: das Lauf-Flag.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.engine.llm_ranking import LlmRankingErgebnis
from core.engine.llm_ranking_lauf import LlmRankingLauf

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

_T = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)


def _ergebnis():
    return LlmRankingErgebnis(
        rangliste=["AAPL", "MSFT"],
        verworfen={},
        prompt_hash="h",
        modell="m",
        anbieter="ollama",
        ok=True,
    )


def _lauf(sim):
    return LlmRankingLauf(
        zeitpunkt_utc=_T,
        prompt_hash="h",
        modell="m",
        anbieter="ollama",
        ok=True,
        geliefert=["AAPL"],
        sim=sim,
    )


def test_lauf_unter_sim_markiert():
    with patch("core.engine.llm_ranking_lauf.is_sim_mode", return_value=True):
        lauf = LlmRankingLauf.aus_ergebnis(_ergebnis(), zeitpunkt_utc=_T)
    assert lauf.sim is True
    # Das Flag ueberlebt das Protokoll.
    assert LlmRankingLauf.aus_json(lauf.als_json()).sim is True

    with patch("core.engine.llm_ranking_lauf.is_sim_mode", return_value=False):
        live = LlmRankingLauf.aus_ergebnis(_ergebnis(), zeitpunkt_utc=_T)
    assert live.sim is False


def test_alte_protokollzeile_ohne_sim_gilt_als_live():
    zeile = _lauf(False).als_json().replace(', "sim": false', "")
    assert '"sim"' not in zeile
    assert LlmRankingLauf.aus_json(zeile).sim is False


def test_trichter_nicht_belastbar_bei_sim_lauf():
    from core.engine.llm_trichter import GRUND_LLM_LOOKAHEAD, baue_trichter

    assert GRUND_LLM_LOOKAHEAD == "llm_lookahead"
    for quelle in ("llm", "llm_und_lstm"):
        t = baue_trichter(_lauf(True), [], {}, quelle=quelle)
        assert t["sim"] is True
        assert t["belastbar"] is False
        assert t["belastbar_grund"] == "llm_lookahead"

    live = baue_trichter(_lauf(False), [], {}, quelle="llm")
    assert live["belastbar"] is True
    assert live["belastbar_grund"] is None

    leer = baue_trichter(None, [], {})
    assert leer["belastbar"] is True
    assert leer["belastbar_grund"] is None


def test_ist_ergebnis_belastbar_false_bei_llm_quelle():
    from core.engine.llm_trichter import ist_ergebnis_belastbar

    # Strategie als Objekt oder als Mapping (die Felder aus #4654 sind noch nicht da).
    assert ist_ergebnis_belastbar(SimpleNamespace(ranking_quelle="llm")) == (
        False,
        "llm_lookahead",
    )
    assert ist_ergebnis_belastbar({"ranking_quelle": "LLM_und_LSTM"}) == (
        False,
        "llm_lookahead",
    )


def test_lstm_quelle_unberuehrt():
    from core.engine.llm_trichter import baue_trichter, ist_ergebnis_belastbar

    assert ist_ergebnis_belastbar({"ranking_quelle": "lstm"}) == (True, None)
    # Ohne Feld gilt der Bestand: LSTM.
    assert ist_ergebnis_belastbar(SimpleNamespace()) == (True, None)
    # Ein Sim-Lauf liegt noch im Speicher, die Quelle ist inzwischen LSTM: keine Kennzeichnung.
    t = baue_trichter(_lauf(True), [], {}, quelle="lstm")
    assert t["belastbar"] is True
    assert t["belastbar_grund"] is None


def test_span_traegt_sim(tmp_path):
    import asyncio

    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.engine.zyklus_kontext import _llm_lauf

    tracer = MagicMock()
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    with patch("core.telemetry.get_tracer", return_value=tracer), patch(
        "core.engine.llm_ranking.frage_llm_ranking",
        AsyncMock(return_value=_ergebnis()),
    ), patch("core.engine.llm_ranking_lauf.is_sim_mode", return_value=True):
        lauf = asyncio.run(
            _llm_lauf(LaufSpeicher(tmp_path / "l.jsonl"), "p", ["AAPL"], 2, None, _T)
        )
    assert lauf.sim is True
    tracer.start_as_current_span.assert_called_once_with("llm_ranking.lauf")
    span.set_attribute.assert_any_call("sim", True)


@pytest.mark.parametrize(
    "quelle, belastbar, grund",
    [("llm", False, "llm_lookahead"), ("lstm", True, None)],
)
def test_route_kennzeichnet_sim_lauf(tmp_path, quelle, belastbar, grund):
    # Die Route reicht die eingestellte Quelle durch: ein liegengebliebener Sim-Lauf
    # kennzeichnet nach dem Wechsel auf LSTM nichts mehr.
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api
    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.sim.clock import engine_now

    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    lauf = _lauf(True)
    lauf.zeitpunkt_utc = engine_now(timezone.utc)
    speicher.protokolliere(lauf)
    cfg = SimpleNamespace(
        ROUND_TABLE_RANK_REFRESH_MINUTES=120, LLM_RANKING_QUELLE=quelle
    )
    with patch.dict(os.environ, {"ENGINE_API_KEY": "k-4662"}), patch.object(
        api, "engine", SimpleNamespace(_llm_lauf_speicher=speicher)
    ), patch.object(api.config, "get_config", return_value=cfg):
        r = TestClient(api.app).get(
            "/llm-vorauswahl/trichter", headers={"X-Engine-Key": "k-4662"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["sim"] is True
    assert body["belastbar"] is belastbar
    assert body["belastbar_grund"] == grund


def test_llm_quellen_wie_im_rang_trichter():
    from core.engine import llm_trichter, zyklus_kontext

    assert set(llm_trichter.LLM_QUELLEN) == set(zyklus_kontext._LLM_QUELLEN)
