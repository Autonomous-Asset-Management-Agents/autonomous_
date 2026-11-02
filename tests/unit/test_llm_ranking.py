"""#4648 (Epic #4638) — LLM-Ranking-Quelle: gespeicherter Prompt -> gepruefte Rangliste.

Plan: ``docs/4648-llm-ranking-quelle-gespeicherten-prompt-ueber-di/implementation_plan.md``.

Die Quelle fragt den Prompt des Nutzers ueber die LLM-Seam ab, prueft jeden Ticker
(Form, Universum, Top-K) und liefert die Rangliste samt Verwurfsgruenden. Ein Fehlschlag
ist ``ok=False`` mit WARNING, nie ein leeres Ranking, und die Funktion wirft nie.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine.llm_ranking import als_cross_section, frage_llm_ranking
from core.engine.rank_funnel import apply_rank_cache_funnel

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

UNIVERSUM = {"AAPL", "MSFT", "TSLA", "NVDA", "BRK.B"}


def _provider(antwort=None, side_effect=None, model="llama3"):
    provider = MagicMock()
    provider.model = model
    provider.generate_content_async = AsyncMock(
        return_value=antwort, side_effect=side_effect
    )
    return provider


async def test_parst_und_dedupliziert():
    provider = _provider(" aapl, MSFT,\nAAPL ,  nvda, msft ")

    erg = await frage_llm_ranking("Meine Idee", UNIVERSUM, 10, provider=provider)

    assert erg.ok is True
    assert erg.rangliste == ["AAPL", "MSFT", "NVDA"]
    assert erg.verworfen == {}
    assert erg.modell == "llama3"
    assert erg.anbieter == type(provider).__name__
    assert erg.prompt_hash == hashlib.sha256("Meine Idee".encode("utf-8")).hexdigest()
    gesendet = provider.generate_content_async.await_args.args[0]
    assert gesendet.startswith("Meine Idee")
    assert len(gesendet) > len("Meine Idee")  # Formatklausel angehaengt


async def test_verwirft_formfehler():
    provider = _provider("AAPL, 1ABC, MS FT, $TSLA, BRK.B, TOOLONGTICKER")

    erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider)

    assert erg.ok is True
    assert erg.rangliste == ["AAPL", "BRK.B"]
    assert erg.verworfen == {
        "1ABC": "formfehler",
        "MS FT": "formfehler",
        "$TSLA": "formfehler",
        "TOOLONGTICKER": "formfehler",
    }


async def test_verwirft_nicht_im_universum():
    provider = _provider("AAPL, NOTINUNI, MSFT")

    erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider)

    assert erg.rangliste == ["AAPL", "MSFT"]
    assert erg.verworfen == {"NOTINUNI": "nicht_im_universum"}


async def test_kappt_auf_top_k_mit_grund():
    """Gherkin #4648, Szenario 1 (mit Bekannt-Menge fuer 'unbekannt')."""
    provider = _provider("AAPL, MSFT, FOOBARX, TSLA, NOTINUNI")

    erg = await frage_llm_ranking(
        "p",
        {"AAPL", "MSFT", "TSLA"},
        2,
        provider=provider,
        bekannte_symbole={"AAPL", "MSFT", "TSLA", "NOTINUNI"},
    )

    assert erg.ok is True
    assert erg.rangliste == ["AAPL", "MSFT"]
    assert erg.verworfen == {
        "FOOBARX": "unbekannt",
        "NOTINUNI": "nicht_im_universum",
        "TSLA": "ueber_top_k",
    }


async def test_timeout_ist_fehlschlag_mit_warning(caplog):
    provider = _provider(side_effect=asyncio.TimeoutError())

    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider)

    assert erg.ok is False
    assert erg.rangliste == []
    assert erg.fehler
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_exception_ist_fehlschlag_mit_warning(caplog):
    provider = _provider(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider)

    assert erg.ok is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_kein_provider_ist_fehlschlag(monkeypatch, caplog):
    import core.engine.llm_ranking as modul

    monkeypatch.setattr(modul, "get_llm_provider", lambda: None)
    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking("p", UNIVERSUM, 10)

    assert erg.ok is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("antwort", ["", "   ", None, "1ABC, $$$, NOTINUNI"])
async def test_leere_antwort_ist_fehlschlag(antwort, caplog):
    provider = _provider(antwort)

    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider)

    assert erg.ok is False
    assert erg.rangliste == []
    assert any(r.levelno == logging.WARNING for r in caplog.records)


@pytest.mark.parametrize("k", [0, -1, -5])
async def test_ungueltiges_top_k_ist_fehlschlag(k, caplog):
    provider = _provider("AAPL, MSFT")

    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking("p", UNIVERSUM, k, provider=provider)

    assert erg.ok is False
    assert erg.fehler == "ungueltiges_top_k"
    assert provider.generate_content_async.await_count == 0
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_als_cross_section_rangtreu():
    rangliste = ["NVDA", "AAPL", "MSFT"]

    xsec = als_cross_section(rangliste)

    assert xsec == {"NVDA": 3.0, "AAPL": 2.0, "MSFT": 1.0}
    reihenfolge = ["AAPL", "BRK.B", "MSFT", "NVDA", "TSLA"]
    # Rundreise: Top-2 aus dem LLM-Rang, gehaltene Position bleibt immer drin.
    assert apply_rank_cache_funnel(reihenfolge, {"TSLA"}, xsec, 2) == [
        "AAPL",
        "NVDA",
        "TSLA",
    ]


# ── #4861: eigene S&P-500-Liste — dieselbe Pruefung wie die LLM-Antwort ─────────────────


def test_pruefe_symbolliste_gruende():
    from core.engine.llm_ranking import pruefe_symbolliste
    from core.round_table.sp500_universe import get_sp500_symbols

    rang, verworfen = pruefe_symbolliste("AAPL, SAP, 12X", set(get_sp500_symbols()), 60)

    assert rang == ["AAPL"]
    assert verworfen == {"SAP": "nicht_im_universum", "12X": "formfehler"}


def test_pruefe_symbolliste_reihenfolge_und_duplikate():
    from core.engine.llm_ranking import pruefe_symbolliste

    rang, verworfen = pruefe_symbolliste(" nvda; AAPL\nmsft, NVDA ,aapl", UNIVERSUM, 10)

    assert rang == ["NVDA", "AAPL", "MSFT"]
    assert verworfen == {}


def test_pruefe_symbolliste_65_auf_60():
    from core.engine.llm_ranking import pruefe_symbolliste
    from core.round_table.sp500_universe import get_sp500_symbols

    universum = sorted(s for s in get_sp500_symbols() if s not in ("SPY", "QQQ"))
    werte = universum[:65]

    rang, verworfen = pruefe_symbolliste(", ".join(werte), set(universum), 60)

    assert rang == werte[:60]
    assert verworfen == dict.fromkeys(werte[60:], "ueber_top_k")


# ── #4960: Websuche des Anbieters, eigener Anbieter ──────────────────────────────────────


def _such_provider(text="AAPL, MSFT", quellen=()):
    from core.llm.provider import SuchAntwort

    provider = _provider("NVDA")
    provider.generate_with_search_async = AsyncMock(
        return_value=SuchAntwort(text, list(quellen))
    )
    return provider


async def test_websuche_ruft_suchmethode_und_traegt_quellen():
    urls = ["https://a.example/1", "https://b.example/2"]
    provider = _such_provider(quellen=urls)

    erg = await frage_llm_ranking(
        "Idee", UNIVERSUM, 10, provider=provider, websuche=True
    )

    assert (erg.ok, erg.rangliste) == (True, ["AAPL", "MSFT"])
    assert erg.websuche is True and erg.quellen == urls
    provider.generate_content_async.assert_not_awaited()
    args, kwargs = provider.generate_with_search_async.await_args
    assert args[0].startswith("Idee") and len(args[0]) > len("Idee")
    assert kwargs == {"max_output_tokens": 512}


async def test_ohne_websuche_aufruf_byte_identisch():
    from core.engine.llm_ranking import FORMATKLAUSEL, MAX_OUTPUT_TOKENS

    provider = _such_provider()

    erg = await frage_llm_ranking("Idee", UNIVERSUM, 10, provider=provider)

    provider.generate_with_search_async.assert_not_awaited()
    provider.generate_content_async.assert_awaited_once_with(
        "Idee" + FORMATKLAUSEL, max_output_tokens=MAX_OUTPUT_TOKENS
    )
    assert erg.rangliste == ["NVDA"]
    assert (erg.websuche, erg.quellen, erg.kanonischer_anbieter) == (False, [], None)


async def test_websuche_ohne_methode_faellt_auf_alten_pfad():
    from types import SimpleNamespace

    provider = SimpleNamespace(
        model="llama3", generate_content_async=AsyncMock(return_value="AAPL")
    )

    erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider, websuche=True)

    assert erg.rangliste == ["AAPL"]
    assert erg.websuche is False, "ohne Suchmethode wird keine Suche behauptet"


async def test_quellen_dedupliziert_und_gekappt():
    from core.engine.llm_ranking import MAX_QUELLEN

    urls = [f"https://q.example/{i}" for i in range(MAX_QUELLEN + 5)]
    provider = _such_provider(quellen=urls + urls[:3] + ["", "  "])

    erg = await frage_llm_ranking("p", UNIVERSUM, 10, provider=provider, websuche=True)

    assert MAX_QUELLEN == 20
    assert erg.quellen == urls[:MAX_QUELLEN]


async def test_gewaehlter_anbieter_ohne_schluessel_kein_fallback(caplog, monkeypatch):
    fuer = MagicMock(return_value=None)
    system = MagicMock()
    monkeypatch.setattr("core.engine.llm_ranking.get_llm_provider_fuer", fuer)
    monkeypatch.setattr("core.engine.llm_ranking.get_llm_provider", system)

    with caplog.at_level(logging.WARNING):
        erg = await frage_llm_ranking(
            "p", UNIVERSUM, 10, anbieter="openai", websuche=True
        )

    fuer.assert_called_once_with("openai")
    system.assert_not_called()
    assert (erg.ok, erg.fehler) == (False, "kein_schluessel")
    assert erg.kanonischer_anbieter == "openai"
    assert erg.prompt_hash == hashlib.sha256(b"p").hexdigest()
    assert any("openai" in r.getMessage() for r in caplog.records)


async def test_anbieter_system_nutzt_systemprovider_und_effektiven_namen(monkeypatch):
    provider = _provider("AAPL")
    monkeypatch.setattr(
        "core.engine.llm_ranking.get_llm_provider", MagicMock(return_value=provider)
    )
    monkeypatch.setattr(
        "core.engine.llm_ranking.resolved_provider_name", lambda: "ollama"
    )

    erg = await frage_llm_ranking("p", UNIVERSUM, 10, anbieter="system")

    assert erg.ok is True and erg.kanonischer_anbieter == "ollama"
    assert erg.anbieter == type(provider).__name__


@pytest.mark.parametrize(
    "top_k, fehler", [(10, "kein_provider"), (0, "ungueltiges_top_k")]
)
async def test_kein_provider_traegt_kanonischen_anbieter(monkeypatch, top_k, fehler):
    """P2-02 aus dem Review zu #4947: auch ein Fehlschlag nennt den effektiven Anbieter."""
    monkeypatch.setattr("core.engine.llm_ranking.get_llm_provider", lambda: None)
    monkeypatch.setattr(
        "core.engine.llm_ranking.resolved_provider_name", lambda: "ollama"
    )

    erg = await frage_llm_ranking("p", UNIVERSUM, top_k, anbieter="system")

    assert (erg.fehler, erg.kanonischer_anbieter) == (fehler, "ollama")
