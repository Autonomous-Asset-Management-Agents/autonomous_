"""#4939 (Epic #4638) — Grounding with Google Search am ``GeminiModelWrapper``, opt-in.

Plan: ``docs/4939-prompt-run-with-provider-web-search-own-provider/implementation_plan.md``.

Die neue Methode ``generate_with_search_async`` setzt das Google-Search-Tool und liest die
Quellen aus ``grounding_metadata.grounding_chunks[].web.uri``. ``generate_content_async``
bleibt ohne Tools. Der SDK-Client ist gemockt (AsyncMock fuer ``aio.models.generate_content``).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import types as genai_types

pytestmark = [pytest.mark.unit, pytest.mark.vc1]


def _wrapper(antwort=None, side_effect=None):
    from core.gemini_client import GeminiModelWrapper

    w = GeminiModelWrapper.__new__(GeminiModelWrapper)
    w.model_name = "gemini-2.5-flash"
    w._model = None
    w._client = MagicMock()
    w._client.aio.models.generate_content = AsyncMock(
        return_value=antwort, side_effect=side_effect
    )
    return w


def _antwort(text, uris):
    chunks = [SimpleNamespace(web=SimpleNamespace(uri=u)) for u in uris]
    chunks.append(SimpleNamespace(web=None))  # Chunk ohne Web-Quelle
    kandidat = SimpleNamespace(
        grounding_metadata=SimpleNamespace(grounding_chunks=chunks)
    )
    return SimpleNamespace(text=text, candidates=[kandidat])


def _config(w):
    return w._client.aio.models.generate_content.await_args.kwargs["config"]


async def test_gemini_suche_setzt_google_search_tool():
    w = _wrapper(_antwort("AAPL, MSFT", []))
    antwort = await w.generate_with_search_async("Idee", max_output_tokens=256)
    assert antwort.text == "AAPL, MSFT"
    kw = w._client.aio.models.generate_content.await_args.kwargs
    assert kw["model"] == "gemini-2.5-flash"
    assert kw["contents"] == "Idee"
    cfg = _config(w)
    assert cfg.max_output_tokens == 256
    assert len(cfg.tools) == 1
    assert isinstance(cfg.tools[0], genai_types.Tool)
    assert isinstance(cfg.tools[0].google_search, genai_types.GoogleSearch)


async def test_gemini_quellen_aus_grounding_chunks():
    w = _wrapper(
        _antwort(
            "AAPL",
            ["https://a.example/1", "https://b.example/2", "https://a.example/1"],
        )
    )
    antwort = await w.generate_with_search_async("Idee")
    assert antwort.quellen == ["https://a.example/1", "https://b.example/2"]


async def test_gemini_suche_fehler_leer_mit_warning(caplog):
    w = _wrapper(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"))
    with caplog.at_level(logging.WARNING):
        antwort = await w.generate_with_search_async("Idee")
    assert antwort.text == "" and antwort.quellen == []
    assert any("429" in r.getMessage() for r in caplog.records)


async def test_gemini_suche_ohne_kandidaten_text_ohne_quellen():
    w = _wrapper(SimpleNamespace(text="AAPL", candidates=None))
    antwort = await w.generate_with_search_async("Idee")
    assert antwort.text == "AAPL" and antwort.quellen == []


async def test_gemini_generate_content_async_ohne_tools():
    w = _wrapper(_antwort("ok", []))
    assert await w.generate_content_async("p", max_output_tokens=128) == "ok"
    cfg = _config(w)
    assert cfg.tools is None
    assert cfg.max_output_tokens == 128
    assert cfg.temperature == 0.7
