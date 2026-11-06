"""#4654 (Epic #4638) — die Lesestellen der LLM-Vorauswahl lesen geklemmt gegen die Registry.

Plan: ``docs/4654-ranking-quelle-prompt-und-rhythmus-als-worm-vers/implementation_plan.md`` §2.5.

Vorbild ``trading_settings.klemme_vol_ziel``: der Wert kommt aus ``settings.py``; was ausserhalb
der Registry liegt (Hand-Edit in ``setup.json``, env), wirkt nie ungeprueft — Default plus WARNING.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

JETZT = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def _cfg(**werte):
    basis = {
        "SIM_MODE": False,
        "ROUND_TABLE_RANK_REFRESH_MINUTES": 120,
        "LLM_RANKING_ENABLED": True,
        "LLM_RANKING_QUELLE": "llm",
        "LLM_RANKING_PROMPT": "Meine Idee",
    }
    basis.update(werte)
    return SimpleNamespace(**basis)


def test_klemme_llm_ranking_klemmt_mit_warning(caplog):
    from core import trading_settings as ts

    with caplog.at_level(logging.WARNING):
        assert ts.klemme_llm_ranking("LLM_RANKING_QUELLE", " LLM ") == "llm"
        assert ts.klemme_llm_ranking(
            "LLM_RANKING_RHYTHMUS", "taeglich_vor_oeffnung"
        ) == ("taeglich_vor_oeffnung")
    assert not caplog.records, "gueltige Werte klemmen still"

    with caplog.at_level(logging.WARNING):
        assert ts.klemme_llm_ranking("LLM_RANKING_QUELLE", "gpt") == "lstm"
        assert (
            ts.klemme_llm_ranking("LLM_RANKING_RHYTHMUS", "alle_2_stunden")
            == "je_refresh"
        )
        lang = "y" * (ts.REGISTRY["LLM_RANKING_PROMPT"].maxlen + 1)
        assert len(ts.klemme_llm_ranking("LLM_RANKING_PROMPT", lang)) == len(lang) - 1
    texte = " ".join(r.getMessage() for r in caplog.records)
    assert all(r.levelno == logging.WARNING for r in caplog.records)
    assert "LLM_RANKING_QUELLE" in texte and "LLM_RANKING_RHYTHMUS" in texte
    assert "LLM_RANKING_PROMPT" in texte
    assert "y" * 50 not in texte, "der Prompt-Klartext gehoert nicht ins Log"


@pytest.mark.parametrize(
    "quelle, aktiv",
    [("llm", True), ("LLM_UND_LSTM", True), ("lstm", False), ("gpt", False)],
)
def test_llm_ranking_aktiv_liest_geklemmt(quelle, aktiv, caplog):
    from core.engine.zyklus_kontext import _llm_ranking_aktiv

    with caplog.at_level(logging.WARNING):
        assert _llm_ranking_aktiv(_cfg(LLM_RANKING_QUELLE=quelle)) is aktiv
    assert any("LLM_RANKING_QUELLE" in r.getMessage() for r in caplog.records) is (
        quelle == "gpt"
    )


@pytest.mark.parametrize(
    "wert, erwartet",
    [
        (None, "je_refresh"),
        ("je_refresh", "je_refresh"),
        ("taeglich_vor_oeffnung", "taeglich_vor_oeffnung"),
        ("woechentlich", "woechentlich"),
        ("stuendlich", "stuendlich"),  # #4960
        ("alle_4_stunden", "alle_4_stunden"),  # #4960
        ("alle_2_stunden", "je_refresh"),
    ],
)
def test_llm_rhythmus_liefert_den_gespeicherten_wert(wert, erwartet):
    from core.engine.llm_ranking_lauf import Rhythmus
    from core.engine.zyklus_kontext import _llm_rhythmus

    cfg = _cfg() if wert is None else _cfg(LLM_RANKING_RHYTHMUS=wert)
    assert _llm_rhythmus(cfg) is Rhythmus(erwartet)
    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        assert _llm_rhythmus() is Rhythmus(erwartet)


def test_rang_quelle_waehlen_nutzt_den_gespeicherten_rhythmus(tmp_path):
    """Eine 30 h alte Liste ist bei ``je_refresh`` (2 x 120 min) abgelaufen, bei
    ``woechentlich`` noch gueltig — der gespeicherte Rhythmus entscheidet."""
    from core.engine.llm_ranking_lauf import LaufSpeicher, LlmRankingLauf
    from core.engine.zyklus import ZyklusZustand
    from core.engine.zyklus_kontext import _rang_quelle_waehlen

    speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    speicher.protokolliere(
        LlmRankingLauf(
            zeitpunkt_utc=JETZT - timedelta(hours=30),
            prompt_hash="h",
            modell="m",
            anbieter="Stub",
            ok=True,
            geliefert=["AAPL", "MSFT"],
        )
    )
    owner = SimpleNamespace(_llm_lauf_speicher=speicher)

    def _waehle(cfg):
        z = ZyklusZustand()
        z.current_time_utc = JETZT
        with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
            "core.report.lstm_panel_store.active_cross_section",
            return_value={"TSLA": 0.9},
        ):
            return _rang_quelle_waehlen(owner, z, MagicMock(), False, 2, True)

    assert _waehle(_cfg(LLM_RANKING_RHYTHMUS="je_refresh"))[1] == "lstm"
    assert _waehle(_cfg(LLM_RANKING_RHYTHMUS="woechentlich"))[1] == "llm"
    # llm_und_lstm wird geklemmt gelesen (Grossschreibung, Leerraum)
    assert (
        _waehle(
            _cfg(
                LLM_RANKING_RHYTHMUS="woechentlich", LLM_RANKING_QUELLE=" LLM_UND_LSTM "
            )
        )[1]
        == "llm_und_lstm"
    )


def test_llm_lauf_ausloesen_reicht_den_geklemmten_prompt_weiter():
    from core import trading_settings as ts
    from core.engine.zyklus import ZyklusZustand
    from core.engine.zyklus_kontext import _llm_lauf_ausloesen

    maxlen = ts.REGISTRY["LLM_RANKING_PROMPT"].maxlen
    roh = "   " + "p" * (maxlen + 10) + "  "
    lauf = AsyncMock()

    async def _szenario():
        owner = SimpleNamespace(_llm_ranking_provider=object())
        z = ZyklusZustand()
        z.current_time_utc = JETZT
        z.symbols_to_process = ["AAPL"]
        with patch(
            "core.engine.trading_loop.get_config",
            return_value=_cfg(
                LLM_RANKING_PROMPT=roh, LLM_RANKING_QUELLE="llm_und_lstm"
            ),
        ), patch("core.engine.zyklus_kontext._llm_lauf", lauf):
            _llm_lauf_ausloesen(owner, z, 2)
            await owner._llm_ranking_task

    asyncio.run(_szenario())
    lauf.assert_awaited_once()
    prompt = lauf.await_args.args[1]
    assert prompt == "p" * maxlen
    assert lauf.await_args.kwargs["quelle"] == "llm_und_lstm"


def test_llm_lauf_traegt_ranking_quelle_am_span(tmp_path):
    from core.engine.llm_ranking import LlmRankingErgebnis
    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.engine.zyklus_kontext import _llm_lauf

    tracer = MagicMock()
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    ergebnis = LlmRankingErgebnis(ok=True, prompt_hash="abc", rangliste=["AAPL"])
    with patch("core.telemetry.get_tracer", return_value=tracer), patch(
        "core.engine.llm_ranking.frage_llm_ranking", AsyncMock(return_value=ergebnis)
    ):
        asyncio.run(
            _llm_lauf(
                LaufSpeicher(tmp_path / "l.jsonl"),
                "Geheime Idee",
                ["AAPL"],
                2,
                None,
                JETZT,
                quelle="llm",
            )
        )
    span.set_attribute.assert_any_call("ranking_quelle", "llm")
    werte = [c.args[1] for c in span.set_attribute.call_args_list]
    assert "Geheime Idee" not in werte, "nur der Hash, nie der Prompt"


@pytest.mark.parametrize("roh, quelle", [("gpt", "lstm"), (" LLM ", "llm")])
def test_get_llm_trichter_liest_geklemmt(tmp_path, roh, quelle):
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api
    from core.engine.llm_ranking_lauf import LaufSpeicher, Rhythmus, hoechstdauer

    cfg = SimpleNamespace(
        ROUND_TABLE_RANK_REFRESH_MINUTES=120,
        LLM_RANKING_QUELLE=roh,
        LLM_RANKING_RHYTHMUS="woechentlich",
    )
    dauer = MagicMock(side_effect=hoechstdauer)
    trichter = MagicMock(return_value={})
    with patch.dict(os.environ, {"ENGINE_API_KEY": "k-4654"}), patch.object(
        api, "engine", SimpleNamespace(_llm_lauf_speicher=LaufSpeicher(tmp_path / "l"))
    ), patch.object(api.config, "get_config", return_value=cfg), patch(
        "core.engine.llm_ranking_lauf.hoechstdauer", dauer
    ), patch(
        "core.engine.llm_trichter.baue_trichter", trichter
    ):
        r = TestClient(api.app).get(
            "/llm-vorauswahl/trichter", headers={"X-Engine-Key": "k-4654"}
        )
    assert r.status_code == 200
    assert trichter.call_args.kwargs["quelle"] == quelle
    assert dauer.call_args.args[0] is Rhythmus.WOECHENTLICH
