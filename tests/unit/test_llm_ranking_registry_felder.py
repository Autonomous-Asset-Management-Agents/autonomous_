"""#4654 (Epic #4638) — Ranking-Quelle, Prompt und Rhythmus als versiegelte Registry-Felder.

Plan: ``docs/4654-ranking-quelle-prompt-und-rhythmus-als-worm-vers/implementation_plan.md``.

Die Registry bleibt die pruefende Schicht ueber ``settings.py``. Der Prompt ist Freitext (neue
Art ``text``): ``canonical_text`` liefert den geklemmten Klartext, nur die Kette sieht seinen
Hash (``worm_changes``, Option A). Unzulaessige Werte der drei Felder lehnt die Route mit 422 ab,
statt sie still zu klemmen.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core import trading_settings as ts

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

_KEYS = ("LLM_RANKING_QUELLE", "LLM_RANKING_RHYTHMUS", "LLM_RANKING_PROMPT")


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(autouse=True)
def leere_config(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace())


@pytest.fixture
def client(monkeypatch):
    from core.engine import api_routes

    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    monkeypatch.setattr(
        api_routes, "_enforce_replay_distinct_nonce", AsyncMock(), raising=False
    )
    yield TestClient(api_routes.app)
    api_routes.app.dependency_overrides.clear()


@pytest.fixture
def worm(monkeypatch):
    from core import hitl_gate

    mock = AsyncMock()
    monkeypatch.setattr(hitl_gate, "log_settings_change_event", mock)
    return mock


def _body(**settings):
    return {"settings": settings, "acknowledgment": "bestaetigt", "nonce": "n-4654"}


def test_defaults_lstm_leerer_prompt_je_refresh():
    assert all(k in ts.REGISTRY for k in _KEYS)
    cur = ts.current_settings()
    assert cur["LLM_RANKING_QUELLE"] == "lstm"
    assert cur["LLM_RANKING_RHYTHMUS"] == "je_refresh"
    assert cur["LLM_RANKING_PROMPT"] == ""
    quelle = ts.REGISTRY["LLM_RANKING_QUELLE"]
    assert quelle.kind == "enum"
    assert set(quelle.choices) == {"lstm", "llm", "llm_und_lstm", "liste"}
    from core.engine.llm_ranking_lauf import Rhythmus

    assert set(ts.REGISTRY["LLM_RANKING_RHYTHMUS"].choices) == {
        r.value for r in Rhythmus
    }
    assert ts.REGISTRY["LLM_RANKING_PROMPT"].kind == "text"


def test_text_art_trimmt_und_kuerzt_statt_zu_werfen():
    s = ts.REGISTRY["LLM_RANKING_PROMPT"]
    assert ts._parse(s, "  Meine Idee \n") == "Meine Idee"
    lang = "x" * (s.maxlen + 50)
    assert ts._parse(s, lang) == "x" * s.maxlen
    # nie werfen, nie raten: kein Text => Default
    assert ts._parse(s, None) == ""
    assert ts._parse(s, 42) == ""
    # Gross-/Kleinschreibung bleibt (anders als bei enum)
    assert ts._parse(s, "Wachstum UND Qualitaet") == "Wachstum UND Qualitaet"


def test_canonical_text_liefert_klartext():
    text = ts.canonical_text("LLM_RANKING_PROMPT", "  Titel mit Burggraben  ")
    assert text == "Titel mit Burggraben"
    assert not text.startswith("sha256:")
    meta = {m["key"]: m for m in ts.settings_meta()}["LLM_RANKING_PROMPT"]
    assert meta["kind"] == "text"
    assert meta["default"] == ""
    assert meta["maxlen"] == ts.REGISTRY["LLM_RANKING_PROMPT"].maxlen


def test_worm_changes_ersetzt_prompt_durch_hash():
    changes = [
        {"key": "LLM_RANKING_PROMPT", "from": "", "to": "Idee v2"},
        {"key": "LLM_RANKING_QUELLE", "from": "lstm", "to": "llm"},
        {"key": "STOP_LOSS_PCT", "from": "7.0", "to": "5.0"},
    ]
    kette = ts.worm_changes(changes)
    assert kette[0] == {"key": "LLM_RANKING_PROMPT", "from": "", "to": _hash("Idee v2")}
    assert kette[1:] == changes[1:]
    # die Eingabe bleibt unberuehrt — sie ist zugleich die Antwort an die Shell
    assert changes[0]["to"] == "Idee v2"
    # derselbe Hash wie am Lauf (llm_ranking.frage_llm_ranking::prompt_hash)
    assert kette[0]["to"] == "sha256:" + hashlib.sha256(b"Idee v2").hexdigest()


def test_applied_und_setup_json_behalten_den_klartext(client, worm):
    r = client.post(
        "/api/trading-settings",
        json=_body(LLM_RANKING_PROMPT="  Idee v2 ", LLM_RANKING_QUELLE="llm"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["applied"]["LLM_RANKING_PROMPT"] == "Idee v2"
    antwort = {c["key"]: c for c in body["changes"]}
    assert antwort["LLM_RANKING_PROMPT"]["to"] == "Idee v2"
    # ... und die Kette traegt die Version, nicht den Klartext
    kette = {c["key"]: c for c in worm.await_args.kwargs["changes"]}
    assert kette["LLM_RANKING_PROMPT"]["to"] == _hash("Idee v2")
    assert kette["LLM_RANKING_QUELLE"]["to"] == "llm"


def test_ungueltiger_rhythmus_422(client, worm):
    r = client.post(
        "/api/trading-settings", json=_body(LLM_RANKING_RHYTHMUS="alle_2_stunden")
    )
    assert r.status_code == 422
    assert "LLM_RANKING_RHYTHMUS" in r.text
    r = client.post("/api/trading-settings", json=_body(LLM_RANKING_QUELLE="gpt"))
    assert r.status_code == 422
    worm.assert_not_awaited()
    # ein gueltiger Wert geht durch
    r = client.post(
        "/api/trading-settings", json=_body(LLM_RANKING_RHYTHMUS="woechentlich")
    )
    assert r.status_code == 201
    assert r.json()["applied"]["LLM_RANKING_RHYTHMUS"] == "woechentlich"


def test_llm_quelle_ohne_prompt_422(client, worm, monkeypatch):
    for quelle in ("llm", "llm_und_lstm"):
        r = client.post("/api/trading-settings", json=_body(LLM_RANKING_QUELLE=quelle))
        assert r.status_code == 422, quelle
        r = client.post(
            "/api/trading-settings",
            json=_body(LLM_RANKING_QUELLE=quelle, LLM_RANKING_PROMPT="   "),
        )
        assert r.status_code == 422, quelle
    worm.assert_not_awaited()

    # Den gespeicherten Prompt leeren, solange eine LLM-Quelle gilt: ebenfalls 422.
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(LLM_RANKING_QUELLE="llm", LLM_RANKING_PROMPT="Idee"),
    )
    r = client.post("/api/trading-settings", json=_body(LLM_RANKING_PROMPT=""))
    assert r.status_code == 422
    worm.assert_not_awaited()
    # Mit gespeichertem Prompt darf die Quelle wechseln.
    r = client.post(
        "/api/trading-settings", json=_body(LLM_RANKING_QUELLE="llm_und_lstm")
    )
    assert r.status_code == 201


def test_event_vor_speicherung(client, monkeypatch):
    """Die Kette ist geschrieben, bevor die Route 201 meldet — erst danach schreibt die
    Shell ``setup.json`` (v2). Reihenfolge per Mock-Aufrufliste."""
    from core import hitl_gate
    from core.engine import api_routes

    ablauf = MagicMock()
    ablauf.attach_mock(AsyncMock(), "worm")
    monkeypatch.setattr(hitl_gate, "log_settings_change_event", ablauf.worm)
    tracer = MagicMock()
    ablauf.attach_mock(tracer.start_as_current_span, "span")
    monkeypatch.setattr(api_routes, "_tracer", tracer)

    r = client.post(
        "/api/trading-settings",
        json=_body(LLM_RANKING_QUELLE="llm", LLM_RANKING_PROMPT="Idee v2"),
    )
    assert r.status_code == 201
    namen = [c[0] for c in ablauf.mock_calls if c[0] in ("worm", "span")]
    assert namen[:2] == ["worm", "span"]
    kette = {c["key"]: c for c in ablauf.worm.await_args.kwargs["changes"]}
    assert kette["LLM_RANKING_PROMPT"]["to"] == _hash("Idee v2")


# ── #4861: eigene S&P-500-Liste ─────────────────────────────────────────────────────────


def test_liste_default_leer_und_quelle_lstm():
    s = ts.REGISTRY["LLM_RANKING_LISTE"]
    assert s.kind == "text"
    assert s.default == ""
    assert s.maxlen == ts.LLM_RANKING_LISTE_MAXLEN == 1000
    assert ts.REGISTRY["LLM_RANKING_QUELLE"].default == "lstm"
    cur = ts.current_settings()
    assert cur["LLM_RANKING_LISTE"] == ""
    assert cur["LLM_RANKING_QUELLE"] == "lstm"


def test_liste_ohne_prompt_erlaubt():
    ts.pruefe_llm_ranking(
        {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": "AAPL"}, {}
    )


def test_liste_ohne_gueltigen_wert_abgewiesen():
    for text in ("SAP, 12X", "", "   "):
        with pytest.raises(ts.UngueltigeEinstellungError):
            ts.pruefe_llm_ranking(
                {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": text}, {}
            )
    # auch das Leeren einer gespeicherten Liste, solange die Quelle "liste" gilt
    with pytest.raises(ts.UngueltigeEinstellungError):
        ts.pruefe_llm_ranking(
            {"LLM_RANKING_LISTE": "SAP"},
            {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": "AAPL"},
        )
    # eine ungueltige Liste stoert nicht, solange eine andere Quelle gilt
    ts.pruefe_llm_ranking({"LLM_RANKING_LISTE": "SAP"}, {})


def test_llm_ohne_prompt_weiter_abgewiesen():
    for quelle in ("llm", "llm_und_lstm"):
        with pytest.raises(ts.UngueltigeEinstellungError):
            ts.pruefe_llm_ranking(
                {"LLM_RANKING_QUELLE": quelle, "LLM_RANKING_LISTE": "AAPL"}, {}
            )


def test_klemme_liste_kuerzt_mit_warning(caplog):
    lang = "AAPL, " * 400
    with caplog.at_level("WARNING"):
        wert = ts.klemme_llm_ranking("LLM_RANKING_LISTE", lang)
    assert len(wert) == ts.LLM_RANKING_LISTE_MAXLEN
    assert any("LLM_RANKING_LISTE" in r.getMessage() for r in caplog.records)


# ── #4960: eigener Anbieter des Prompt-Laufs, neue Rhythmen ───────────────────────────────


def test_anbieter_feld_default_system():
    s = ts.REGISTRY["LLM_RANKING_ANBIETER"]
    assert (s.kind, s.default) == ("enum", "system")
    assert set(s.choices) == {"system", "gemini", "openai", "anthropic"}
    assert ts.current_settings()["LLM_RANKING_ANBIETER"] == "system"
    assert ts.klemme_llm_ranking("LLM_RANKING_ANBIETER", " OpenAI ") == "openai"
    assert ts.klemme_llm_ranking("LLM_RANKING_ANBIETER", "gpt") == "system"


@pytest.mark.parametrize("wert", ["gpt", "ollama", ""])
def test_unbekannter_anbieter_422(client, worm, wert):
    r = client.post("/api/trading-settings", json=_body(LLM_RANKING_ANBIETER=wert))
    assert r.status_code == 422
    assert r.json()["code"] == "setting_value_not_allowed"
    assert "LLM_RANKING_ANBIETER" in r.text
    worm.assert_not_awaited()


@pytest.mark.parametrize("wert", ["stuendlich", "alle_4_stunden"])
def test_neue_rhythmen_zulaessig(client, worm, wert):
    r = client.post("/api/trading-settings", json=_body(LLM_RANKING_RHYTHMUS=wert))
    assert r.status_code == 201, r.text
    assert r.json()["applied"]["LLM_RANKING_RHYTHMUS"] == wert


def test_anbieter_aenderung_als_settings_change_event(client, worm):
    r = client.post(
        "/api/trading-settings", json=_body(LLM_RANKING_ANBIETER="anthropic")
    )
    assert r.status_code == 201, r.text
    worm.assert_awaited_once()
    kette = {c["key"]: c for c in worm.await_args.kwargs["changes"]}
    assert (
        kette["LLM_RANKING_ANBIETER"]["from"],
        kette["LLM_RANKING_ANBIETER"]["to"],
    ) == (
        "system",
        "anthropic",
    )
