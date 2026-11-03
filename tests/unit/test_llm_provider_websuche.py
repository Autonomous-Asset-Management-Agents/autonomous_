"""#4939 (Epic #4638) — Websuche des Anbieters an der LLM-Seam, opt-in.

Plan: ``docs/4939-prompt-run-with-provider-web-search-own-provider/implementation_plan.md``.

OpenAI und Anthropic bekommen je eine NEUE Methode ``generate_with_search_async``; sie liefert
``SuchAntwort(text, quellen)``. Die bestehenden ``generate_content``/``generate_content_async``
bleiben byte-identisch. Fehlervertrag wie bisher: nie werfen, leere Antwort mit WARNING.
httpx ist gemockt (AsyncMock fuer die async-Schnittstelle, ``pfad_regeln/tests.toml``).
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]


def _client_cm(client):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _client(daten, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = daten
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    return client


def _openai():
    from core.llm.provider import OpenAIProvider

    return OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")


def _anthropic():
    from core.llm.provider import AnthropicProvider

    return AnthropicProvider(model="claude-haiku-4-5", api_key="sk-ant-test")


_OPENAI_ANTWORT = {
    "output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed"},
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "AAPL, MSFT",
                    "annotations": [
                        {"type": "url_citation", "url": "https://a.example/1"},
                        {"type": "url_citation", "url": "https://b.example/2"},
                        {"type": "file_citation", "file_id": "f"},
                    ],
                }
            ],
        },
    ]
}

_ANTHROPIC_ANTWORT = {
    "stop_reason": "end_turn",
    "content": [
        {"type": "text", "text": "I'll search for current market news."},
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "stocks"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [{"type": "web_search_result", "url": "https://x.example/a"}],
        },
        {
            "type": "text",
            "text": "AAPL",
            "citations": [
                {"type": "web_search_result_location", "url": "https://x.example/a"}
            ],
        },
        {
            "type": "text",
            "text": ", MSFT",
            "citations": [
                {"type": "web_search_result_location", "url": "https://y.example/b"}
            ],
        },
    ],
}


async def test_openai_suche_nutzt_responses_mit_web_search():
    client = _client(_OPENAI_ANTWORT)
    with patch("httpx.AsyncClient", return_value=_client_cm(client)):
        antwort = await _openai().generate_with_search_async(
            "Idee", max_output_tokens=256
        )
    assert antwort.text == "AAPL, MSFT"
    assert client.post.call_args.args[0] == "https://api.openai.com/v1/responses"
    kwargs = client.post.call_args.kwargs
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    payload = kwargs["json"]
    assert payload["model"] == "gpt-4o-mini"
    assert payload["tools"] == [{"type": "web_search"}]
    assert payload["input"] == "Idee"
    assert payload["max_output_tokens"] == 256


async def test_openai_quellen_aus_url_citation():
    client = _client(_OPENAI_ANTWORT)
    with patch("httpx.AsyncClient", return_value=_client_cm(client)):
        antwort = await _openai().generate_with_search_async("Idee")
    assert antwort.quellen == ["https://a.example/1", "https://b.example/2"]


async def test_anthropic_suche_sendet_web_search_tool_mit_max_uses():
    from core.llm.provider import WEBSUCHE_MAX_USES

    client = _client(_ANTHROPIC_ANTWORT)
    with patch("httpx.AsyncClient", return_value=_client_cm(client)):
        await _anthropic().generate_with_search_async("Idee", max_output_tokens=300)
    assert client.post.call_args.args[0] == "https://api.anthropic.com/v1/messages"
    payload = client.post.call_args.kwargs["json"]
    assert WEBSUCHE_MAX_USES == 3
    assert payload["tools"] == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": WEBSUCHE_MAX_USES,
        }
    ]
    assert payload["messages"] == [{"role": "user", "content": "Idee"}]
    assert payload["max_tokens"] == 300
    assert payload["model"] == "claude-haiku-4-5"


async def test_anthropic_text_nur_nach_letztem_suchergebnis():
    client = _client(_ANTHROPIC_ANTWORT)
    with patch("httpx.AsyncClient", return_value=_client_cm(client)):
        antwort = await _anthropic().generate_with_search_async("Idee")
    assert antwort.text == "AAPL, MSFT"
    assert "search" not in antwort.text
    assert antwort.quellen == ["https://x.example/a", "https://y.example/b"]


async def test_anthropic_pause_turn_ist_fehlschlag_mit_warning(caplog):
    daten = dict(_ANTHROPIC_ANTWORT, stop_reason="pause_turn")
    client = _client(daten)
    with patch("httpx.AsyncClient", return_value=_client_cm(client)), caplog.at_level(
        logging.WARNING
    ):
        antwort = await _anthropic().generate_with_search_async("Idee")
    assert antwort.text == "" and antwort.quellen == []
    assert any(
        "pause_turn" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


@pytest.mark.parametrize("fabrik", ["openai", "anthropic"])
@pytest.mark.parametrize("fall", ["ohne_schluessel", "http_500", "ausnahme"])
async def test_suche_fehlschlag_leer_mit_warning(fabrik, fall, caplog):
    from core.llm.provider import AnthropicProvider, OpenAIProvider

    if fall == "ohne_schluessel":
        with patch.dict(os.environ, {}, clear=True):
            prov = OpenAIProvider() if fabrik == "openai" else AnthropicProvider()
        hinweis = "OPENAI_API_KEY" if fabrik == "openai" else "ANTHROPIC_API_KEY"
    else:
        prov = _openai() if fabrik == "openai" else _anthropic()
        hinweis = "500" if fall == "http_500" else "refused"
    client = _client({}, status=500)
    if fall == "ausnahme":
        client.post = AsyncMock(side_effect=OSError("refused"))
    with patch("httpx.AsyncClient", return_value=_client_cm(client)), caplog.at_level(
        logging.WARNING
    ):
        antwort = await prov.generate_with_search_async("Idee")
    assert antwort.text == "" and antwort.quellen == []
    if fall == "ohne_schluessel":
        client.post.assert_not_awaited()
    assert any(
        hinweis in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )


async def test_generate_content_async_payload_unveraendert():
    """Der alte Pfad bleibt byte-identisch: derselbe Endpunkt, dasselbe ``_payload``."""
    oa = _openai()
    assert oa._payload("p", 128) == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "p"}],
        "max_tokens": 128,
        "temperature": 0.0,
    }
    an = _anthropic()
    assert an._payload("p", 128) == {
        "model": "claude-haiku-4-5",
        "max_tokens": 128,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": "p"}],
    }
    for prov, url, daten in (
        (
            oa,
            "https://api.openai.com/v1/chat/completions",
            {"choices": [{"message": {"content": "ok"}}]},
        ),
        (an, "https://api.anthropic.com/v1/messages", {"content": [{"text": "ok"}]}),
    ):
        client = _client(daten)
        with patch("httpx.AsyncClient", return_value=_client_cm(client)):
            assert await prov.generate_content_async("p", max_output_tokens=128) == "ok"
        assert client.post.call_args.args[0] == url
        assert client.post.call_args.kwargs["json"] == prov._payload("p", 128)
        assert "tools" not in client.post.call_args.kwargs["json"]
