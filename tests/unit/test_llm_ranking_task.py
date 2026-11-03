"""#4652 (Epic #4638, Sub-Issue 3) — der LLM-Lauf belastet den Zyklus nicht.

Ein faelliger Lauf startet als Single-Flight-``asyncio.Task`` ausserhalb des Zyklus (Muster des
Earnings-Cache-Tasks, ``_kontext_signale_auffrischen``); der Zyklus liest nur den
``LaufSpeicher``. Ein Fehler des Tasks wird im naechsten Zyklus als WARNING sichtbar.
Plan: ``docs/4652-*/implementation_plan.md`` §5.2.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

JETZT = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)
UNIVERSUM = ["AAPL", "TSLA", "MSFT", "NVDA"]


def _mixin(tmp_path, provider):
    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.api = MagicMock()
    m._rank_panel_producer = None
    m._llm_lauf_speicher = LaufSpeicher(tmp_path / "laeufe.jsonl")
    m._llm_ranking_provider = provider
    return m


def _cfg(**werte):
    basis = {
        "SIM_MODE": False,
        "ROUND_TABLE_TOP_K_EVAL": 2,
        "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 3,
        "ROUND_TABLE_RANK_REFRESH_MINUTES": 120,
        "LLM_RANKING_ENABLED": True,
        "LLM_RANKING_QUELLE": "llm",
        "LLM_RANKING_PROMPT": "Meine Idee",
    }
    basis.update(werte)
    return SimpleNamespace(**basis)


def _zustand():
    from core.engine.zyklus import ZyklusZustand

    z = ZyklusZustand()
    z.current_time_utc = JETZT
    z.symbols_to_process = list(UNIVERSUM)
    z.symbols_order = list(UNIVERSUM)
    z.positions_by_symbol = {}
    z.position_confirmed = True
    return z


def _patches(cfg):
    store = MagicMock()
    store.latest_snapshot_date.return_value = JETZT.date()
    return (
        patch("core.engine.trading_loop.get_config", return_value=cfg),
        patch("core.report.lstm_panel_store.get_store", return_value=store),
        patch(
            "core.report.lstm_panel_store.active_cross_section",
            return_value={"TSLA": 0.9, "NVDA": 0.8, "AAPL": 0.1, "MSFT": 0.05},
        ),
    )


def _wartender_provider():
    """Provider, dessen Antwort erst nach ``freigabe.set()`` kommt (AsyncMock, Testregel)."""
    freigabe = asyncio.Event()

    async def _antwort(*_a, **_kw):
        await freigabe.wait()
        return "AAPL, MSFT"

    provider = SimpleNamespace(
        model="stub-1", generate_content_async=AsyncMock(side_effect=_antwort)
    )
    return provider, freigabe


def test_lauf_startet_als_task_nicht_awaited(tmp_path):
    async def _szenario():
        provider, freigabe = _wartender_provider()
        m = _mixin(tmp_path, provider)
        z = _zustand()
        a, b, c = _patches(_cfg())
        with a, b, c:
            m._kontext_rang_trichter(
                z
            )  # synchron: kehrt zurueck, ohne den LLM abzuwarten
        task = m._llm_ranking_task
        assert isinstance(task, asyncio.Task)
        await asyncio.sleep(0)
        assert not task.done()
        assert m._llm_lauf_speicher.letzter_erfolg() is None
        assert z.symbols_order == ["TSLA", "NVDA"]  # dieser Zyklus: Rueckfall auf LSTM

        freigabe.set()
        await task
        lauf = m._llm_lauf_speicher.letzter_erfolg()
        assert lauf is not None and lauf.geliefert == ["AAPL", "MSFT"]
        assert lauf.anbieter == "SimpleNamespace" and lauf.modell == "stub-1"

        # Naechster Zyklus: liest nur den Speicher, ein neuer Lauf ist (noch) nicht faellig.
        z2 = _zustand()
        a, b, c = _patches(_cfg())
        with a, b, c:
            m._kontext_rang_trichter(z2)
        assert z2.symbols_order == ["AAPL", "MSFT"]
        assert z2.llm_lauf_id == lauf.lauf_id
        assert provider.generate_content_async.await_count == 1

    asyncio.run(_szenario())


def test_single_flight(tmp_path):
    async def _szenario():
        provider, freigabe = _wartender_provider()
        m = _mixin(tmp_path, provider)
        cfg = _cfg(ROUND_TABLE_RANK_REFRESH_MINUTES=0)  # jeder Zyklus waere faellig
        a, b, c = _patches(cfg)
        with a, b, c:
            m._kontext_rang_trichter(_zustand())
            erster = m._llm_ranking_task
            await asyncio.sleep(0)
            m._kontext_rang_trichter(_zustand())
            m._kontext_rang_trichter(_zustand())
        assert m._llm_ranking_task is erster
        freigabe.set()
        await erster
        assert provider.generate_content_async.await_count == 1

    asyncio.run(_szenario())


def test_task_fehler_wird_warning(tmp_path, caplog):
    async def _szenario():
        m = _mixin(tmp_path, SimpleNamespace(model="x"))
        cfg = _cfg(ROUND_TABLE_RANK_REFRESH_MINUTES=0)
        a, b, c = _patches(cfg)
        with a, b, c, patch(
            "core.engine.llm_ranking.frage_llm_ranking",
            new=AsyncMock(side_effect=RuntimeError("seam weg")),
        ):
            m._kontext_rang_trichter(_zustand())
            erster = m._llm_ranking_task
            with pytest.raises(RuntimeError):
                await erster
            with caplog.at_level(logging.WARNING):
                m._kontext_rang_trichter(_zustand())  # naechster Zyklus
            assert m._llm_ranking_task is not erster  # neuer Versuch
            with pytest.raises(RuntimeError):
                await m._llm_ranking_task

    asyncio.run(_szenario())
    assert any(
        "[RankFunnel] LLM-Ranking-Lauf fehlgeschlagen" in r.getMessage()
        and "seam weg" in r.getMessage()
        for r in caplog.records
    )


def test_sim_ohne_stub_oder_ollama_kein_lauf(tmp_path, caplog):
    async def _szenario():
        m = _mixin(tmp_path, None)
        a, b, c = _patches(_cfg(SIM_MODE=True))
        with a, b, c, patch(
            "core.llm.health.resolved_provider_name", return_value="gemini"
        ), caplog.at_level(logging.WARNING):
            m._kontext_rang_trichter(_zustand())
        assert getattr(m, "_llm_ranking_task", None) is None

    asyncio.run(_szenario())
    assert any("SIM_MODE" in r.getMessage() for r in caplog.records)


def test_sim_mit_ollama_startet_lauf(tmp_path):
    from core.engine.llm_ranking import LlmRankingErgebnis

    async def _szenario():
        m = _mixin(tmp_path, None)
        a, b, c = _patches(_cfg(SIM_MODE=True))
        with a, b, c, patch(
            "core.llm.health.resolved_provider_name", return_value="ollama"
        ), patch(
            "core.engine.llm_ranking.frage_llm_ranking",
            new=AsyncMock(return_value=LlmRankingErgebnis(ok=False, fehler="x")),
        ) as frage:
            m._kontext_rang_trichter(_zustand())
            await m._llm_ranking_task
        frage.assert_awaited_once()

    asyncio.run(_szenario())


# ── #4960: eigener Anbieter, Websuche, eigener Rhythmus ──────────────────────────────────

_ANTHROPIC_SUCHE = {
    "stop_reason": "end_turn",
    "content": [
        {"type": "web_search_tool_result", "tool_use_id": "s1", "content": []},
        {
            "type": "text",
            "text": "AAPL, MSFT",
            "citations": [
                {"type": "web_search_result_location", "url": "https://x.example/a"}
            ],
        },
    ],
}


def _env(**werte):
    """os.environ ohne K_SERVICE und ohne LLM-Schluessel, plus ``werte``."""
    weg = ("K_SERVICE", "LLM_PROVIDER", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    env = {k: v for k, v in os.environ.items() if k not in weg}
    return patch.dict(os.environ, {**env, **werte}, clear=True)


def _http(antworten):
    """``httpx.AsyncClient``-Ersatz: je URL-Teil eine JSON-Antwort; merkt sich jeden POST."""
    posts = []

    async def _post(url, **kw):
        posts.append((url, kw.get("json")))
        for teil, daten in antworten.items():
            if teil in url:
                resp = MagicMock(status_code=200)
                resp.json.return_value = daten
                return resp
        raise AssertionError(f"unerwarteter POST an {url}")

    client = MagicMock()
    client.post = AsyncMock(side_effect=_post)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("httpx.AsyncClient", return_value=cm), posts


def _ausloesen(m, cfg, z=None):
    from core.engine.zyklus_kontext import _llm_lauf_ausloesen

    with patch("core.engine.trading_loop.get_config", return_value=cfg):
        _llm_lauf_ausloesen(m, z or _zustand(), 2)


def _ein_lauf(tmp_path, cfg, antworten=None, vorlauf=None, danach=None, **env):
    """Ein faelliger Lauf ohne Stub-Provider, ueber die echte Seam; (Engine, POSTs)."""
    from core.llm import provider as seam

    http, posts = _http(antworten or {})

    async def _szenario():
        m = _mixin(tmp_path, None)
        if vorlauf is not None:
            m._llm_lauf_speicher.protokolliere(vorlauf)
        with _env(**env), http:
            seam.reset_llm_provider()
            _ausloesen(m, cfg)
            await m._llm_ranking_task
            if danach is not None:
                await danach(seam)
        seam.reset_llm_provider()
        return m

    return asyncio.run(_szenario()), posts


def test_stuendlich_drei_laeufe_in_drei_stunden_bei_refresh_120(tmp_path):
    """Gherkin: ``stuendlich`` laeuft dreimal, ``je_refresh`` bleibt beim Refresh (zweimal)."""

    def _laeufe(rhythmus):
        async def _szenario():
            provider = SimpleNamespace(
                model="stub", generate_content_async=AsyncMock(return_value="AAPL")
            )
            m = _mixin(tmp_path / rhythmus, provider)
            cfg = _cfg(LLM_RANKING_RHYTHMUS=rhythmus)
            uhr = [0.0]
            # Nur das Modul-``time`` tauschen: die Ereignisschleife braucht die echte Uhr.
            fake = SimpleNamespace(
                monotonic=lambda: uhr[0], perf_counter=time.perf_counter
            )
            with patch("core.engine.zyklus_kontext.time", fake):
                for minute in range(0, 180, 10):  # drei Stunden, Zyklus alle 10 min
                    uhr[0] = minute * 60.0
                    z = _zustand()
                    z.current_time_utc = JETZT + timedelta(minutes=minute)
                    _ausloesen(m, cfg, z)
                    if getattr(m, "_llm_ranking_task", None) is not None:
                        await m._llm_ranking_task
            return provider.generate_content_async.await_count

        return asyncio.run(_szenario())

    assert JETZT.weekday() == 2  # Mittwoch, Handelstag
    assert _laeufe("stuendlich") == 3
    assert _laeufe("je_refresh") == 2


def test_anbieter_anthropic_bei_llm_provider_ollama(tmp_path):
    """Gherkin: Prompt-Lauf ueber Anthropic mit Suche, ein Round-Table-Aufruf ueber dieselbe
    Seam bleibt beim lokalen Ollama ohne Such-Tool."""

    async def _round_table_aufruf(seam):
        andere = seam.get_llm_provider()
        assert isinstance(andere, seam.OllamaProvider)
        assert await andere.generate_content_async("x") == "ok"

    m, posts = _ein_lauf(
        tmp_path,
        _cfg(LLM_RANKING_ANBIETER="anthropic"),
        antworten={
            "api.anthropic.com": _ANTHROPIC_SUCHE,
            "/api/generate": {"response": "ok"},
        },
        danach=_round_table_aufruf,
        LLM_PROVIDER="ollama",
        ANTHROPIC_API_KEY="sk-ant",
    )

    assert posts[0][0] == "https://api.anthropic.com/v1/messages"
    assert posts[0][1]["tools"][0]["type"].startswith("web_search")
    assert posts[1][0].endswith("/api/generate")
    assert "tools" not in posts[1][1]
    lauf = m._llm_lauf_speicher.letzter_erfolg()
    assert lauf.geliefert == ["AAPL", "MSFT"]
    assert lauf.websuche is True and lauf.quellen == ["https://x.example/a"]
    assert lauf.kanonischer_anbieter == "anthropic"


def test_fehlender_schluessel_protokolliert_kein_schluessel_ohne_fallback(
    tmp_path, caplog
):
    """Gherkin: OpenAI ohne Schluessel → Fehlschlag mit WARNING, die letzte Liste bleibt, kein
    anderer Anbieter wird gerufen."""
    from core.engine.llm_ranking_lauf import VERALTET_ABER_GUELTIG, LlmRankingLauf

    frueher = LlmRankingLauf(
        zeitpunkt_utc=JETZT - timedelta(minutes=30),
        prompt_hash="h",
        modell="m",
        anbieter="a",
        ok=True,
        geliefert=["TSLA"],
    )
    gemini = MagicMock()
    with patch("core.gemini_client.get_gemini_instance", gemini), caplog.at_level(
        logging.WARNING
    ):
        m, posts = _ein_lauf(
            tmp_path,
            _cfg(LLM_RANKING_ANBIETER="openai"),
            vorlauf=frueher,
            LLM_PROVIDER="gemini",
        )

    assert posts == [] and not gemini.called, "kein anderer Anbieter"
    letzter = m._llm_lauf_speicher._laeufe[-1]
    assert (letzter.ok, letzter.fehler) == (False, "kein_schluessel")
    assert letzter.kanonischer_anbieter == "openai"
    liste, status, _ = m._llm_lauf_speicher.gueltige_liste(JETZT, timedelta(hours=4))
    assert (liste, status) == (["TSLA"], VERALTET_ABER_GUELTIG)
    assert any("OPENAI_API_KEY" in r.getMessage() for r in caplog.records)


def test_system_ollama_websuche_false(tmp_path):
    """Gherkin: lokales Modell — Lauf ohne Such-Tool, Protokoll websuche=false."""
    m, posts = _ein_lauf(
        tmp_path,
        _cfg(),  # LLM_RANKING_ANBIETER fehlt → Default "system"
        antworten={"/api/generate": {"response": "AAPL"}},
        LLM_PROVIDER="ollama",
    )

    assert len(posts) == 1 and "tools" not in posts[0][1]
    lauf = m._llm_lauf_speicher.letzter_erfolg()
    assert (lauf.websuche, lauf.quellen) == (False, [])
    assert lauf.kanonischer_anbieter == "ollama"


def test_system_gemini_default_ohne_suche(tmp_path):
    """``system`` sucht nie, auch wenn das System Gemini ist: derselbe Aufruf wie vor #4960."""
    from core.engine.llm_ranking import FORMATKLAUSEL, MAX_OUTPUT_TOKENS

    gemini = MagicMock(model="gemini-2.5-flash")
    gemini.generate_content_async = AsyncMock(return_value="AAPL")
    gemini.generate_with_search_async = AsyncMock()

    with patch("core.gemini_client.get_gemini_instance", return_value=gemini):
        m, _ = _ein_lauf(tmp_path, _cfg(), LLM_PROVIDER="gemini")

    gemini.generate_with_search_async.assert_not_awaited()
    gemini.generate_content_async.assert_awaited_once_with(
        "Meine Idee" + FORMATKLAUSEL, max_output_tokens=MAX_OUTPUT_TOKENS
    )
    lauf = m._llm_lauf_speicher.letzter_erfolg()
    assert lauf.websuche is False and lauf.kanonischer_anbieter == "gemini"


def test_sim_mode_sperrt_cloud_anbieter_effektiv(tmp_path, caplog):
    """SIM_MODE prueft den EFFEKTIVEN Anbieter: LLM_PROVIDER=ollama, Prompt-Lauf Gemini."""
    m = _mixin(tmp_path, None)
    with _env(LLM_PROVIDER="ollama"), caplog.at_level(logging.WARNING):
        _ausloesen(m, _cfg(SIM_MODE=True, LLM_RANKING_ANBIETER="gemini"))

    assert getattr(m, "_llm_ranking_task", None) is None
    assert any(
        "SIM_MODE" in r.getMessage() and "gemini" in r.getMessage()
        for r in caplog.records
    )


def test_span_traegt_websuche_und_n_quellen(tmp_path):
    from core.engine.llm_ranking import LlmRankingErgebnis
    from core.engine.llm_ranking_lauf import LaufSpeicher
    from core.engine.zyklus_kontext import _llm_lauf

    tracer = MagicMock()
    span = tracer.start_as_current_span.return_value.__enter__.return_value
    urls = ["https://a.example/1", "https://b.example/2"]
    ergebnis = LlmRankingErgebnis(
        ok=True,
        prompt_hash="abc",
        rangliste=["AAPL"],
        anbieter="GeminiModelWrapper",
        kanonischer_anbieter="gemini",
        websuche=True,
        quellen=urls,
    )
    frage = AsyncMock(return_value=ergebnis)
    with patch("core.telemetry.get_tracer", return_value=tracer), patch(
        "core.engine.llm_ranking.frage_llm_ranking", frage
    ):
        asyncio.run(
            _llm_lauf(
                LaufSpeicher(tmp_path / "l.jsonl"),
                "p",
                ["AAPL"],
                2,
                None,
                JETZT,
                anbieter="gemini",
                websuche=True,
            )
        )
    assert frage.await_args.kwargs["anbieter"] == "gemini"
    assert frage.await_args.kwargs["websuche"] is True
    attribute = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attribute["websuche"] is True
    assert attribute["n_quellen"] == 2
    assert attribute["anbieter"] == "gemini"
    assert not set(urls) & set(map(str, attribute.values())), "URLs nie in den Span"
