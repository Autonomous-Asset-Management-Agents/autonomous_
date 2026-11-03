"""#4890 (STW-3c, Epic #4625) — coded English error responses for strategy and settings routes.

Every error path of ``/api/strategies*`` and ``/api/trading-settings`` answers
``{"detail": "<English sentence>", "code": "<snake_case>", "params": {...}}``; ``detail``
stays a string, the status code stays as before. The flag-off 404 is the deliberate
exemption: it keeps the framework shape ``{"detail": "Not Found"}``.

Plan: ``docs/4890-*/implementation_plan.md`` §2, TDD-Reihenfolge Schritt 1.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: German words that must not appear in an error ``detail`` (Plan §5 step 1). Matched as
#: whole words, case-insensitive — ``strategien.json`` or ``LLM_RANKING_QUELLE`` are
#: identifiers, not prose.
DEUTSCHE_WOERTER = (
    "zulaessig",
    "ausserhalb",
    "hoechstens",
    "loeschen",
    "gueltig",
    "fuer",
    "ueber",
    "erwartet",
    "fehlt",
    "nicht",
    "braucht",
    "Strategie",
)
_DEUTSCH = re.compile(
    r"[äöüÄÖÜß]|\b(" + "|".join(DEUTSCHE_WOERTER) + r")\b", re.IGNORECASE
)


@pytest.fixture
def stw(tmp_path, monkeypatch):
    """Flag on, HITL on, a real chain and store in ``tmp_path``, auth off."""
    import config
    from core import hitl_gate
    from core.engine import api_routes
    from core.round_table.senate_log import LocalJSONAuditLogger

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path / "senate"))
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "udd"))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    from fastapi.testclient import TestClient

    yield SimpleNamespace(
        client=TestClient(api_routes.app),
        ablage=tmp_path / "udd" / "strategien.json",
    )
    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)


def _anlegen(name="A", nonce="n-1", **werte):
    return {
        "name": name,
        "werte": werte or {"STOP_LOSS_PCT": 5.0},
        "acknowledgment": "confirmed",
        "nonce": nonce,
    }


def _quittung(nonce="n-x"):
    return {"acknowledgment": "confirmed", "nonce": nonce}


def _settings(nonce="s-1", **settings):
    return {"settings": settings, "acknowledgment": "confirmed", "nonce": nonce}


def _pruefe_form(r, status: int, code: str) -> dict:
    """Status, ``code`` and an English string ``detail`` — returns ``params``."""
    assert r.status_code == status, r.text
    body = r.json()
    assert body.get("code") == code, body
    assert isinstance(body["detail"], str), body
    assert not _DEUTSCH.search(body["detail"]), body["detail"]
    assert isinstance(body.get("params"), dict), body
    return body["params"]


# ── individual paths ────────────────────────────────────────────────────────────


def test_name_zu_lang_422_mit_code_und_limit(stw):
    from core import strategie_profile as sp

    r = stw.client.post("/api/strategies", json=_anlegen("x" * 200))
    params = _pruefe_form(r, 422, "strategy_name_length")
    assert params == {"min": 1, "max": sp.NAME_MAXLEN}


def test_vierte_strategie_422_strategy_limit_reached(stw):
    for i, name in enumerate("ABC"):
        r = stw.client.post("/api/strategies", json=_anlegen(name, f"n-{i}"))
        assert r.status_code == 201, r.text
    r = stw.client.post("/api/strategies", json=_anlegen("D", "n-9"))
    assert _pruefe_form(r, 422, "strategy_limit_reached") == {"limit": 3}


def test_loeschen_unbekannt_404_strategy_not_found(stw):
    r = stw.client.request("DELETE", "/api/strategies/X", json=_quittung())
    assert _pruefe_form(r, 404, "strategy_not_found") == {"name": "X"}


def test_zuweisen_unbekannt_404_strategy_not_found(stw):
    r = stw.client.post("/api/strategies/X/assign-live", json=_quittung())
    assert _pruefe_form(r, 404, "strategy_not_found") == {"name": "X"}


def test_leitplanke_422_guardrail_in_strategy(stw):
    r = stw.client.post("/api/strategies", json=_anlegen(HARD_STOP_LOSS_PCT=-5.0))
    params = _pruefe_form(r, 422, "guardrail_in_strategy")
    assert params == {"keys": ["HARD_STOP_LOSS_PCT"]}


def test_trading_settings_enum_422_setting_value_not_allowed(stw):
    from core import trading_settings as ts

    r = stw.client.post(
        "/api/trading-settings", json=_settings(LLM_RANKING_QUELLE="foo")
    )
    params = _pruefe_form(r, 422, "setting_value_not_allowed")
    assert params == {
        "key": "LLM_RANKING_QUELLE",
        "value": "foo",
        "allowed": list(ts.REGISTRY["LLM_RANKING_QUELLE"].choices),
    }


def test_prompt_fehlt_422_ranking_prompt_missing(stw):
    r = stw.client.post(
        "/api/trading-settings",
        json=_settings(LLM_RANKING_QUELLE="llm", LLM_RANKING_PROMPT=""),
    )
    assert _pruefe_form(r, 422, "ranking_prompt_missing") == {"source": "llm"}


def test_liste_fehlt_422_ranking_list_missing(stw):
    r = stw.client.post(
        "/api/trading-settings",
        json=_settings(LLM_RANKING_QUELLE="liste", LLM_RANKING_LISTE="SAP, 12X"),
    )
    assert _pruefe_form(r, 422, "ranking_list_missing") == {}


def test_unbekannter_setting_key_422_unknown_setting(stw):
    r = stw.client.post("/api/trading-settings", json=_settings(GIBT_ES_NICHT=1))
    assert _pruefe_form(r, 422, "unknown_setting") == {"keys": ["GIBT_ES_NICHT"]}


def test_nonce_doppelt_409_nonce_reused_detail_unveraendert(stw):
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    r = stw.client.post("/api/strategies", json=_anlegen("B", "n-1"))
    assert _pruefe_form(r, 409, "nonce_reused") == {}
    assert r.json()["detail"] == "nonce already used (replay-distinct)"


def test_hitl_aus_409_hitl_required(stw, monkeypatch):
    import config

    assert stw.client.post("/api/strategies", json=_anlegen("B")).status_code == 201
    monkeypatch.setattr(config, "HITL_ENABLED", False, raising=False)
    r = stw.client.post("/api/strategies/B/assign-live", json=_quittung())
    assert _pruefe_form(r, 409, "hitl_required") == {}
    assert "HITL" in r.json()["detail"]


def test_audit_fehler_503_audit_write_failed(stw, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_definition_event",
        AsyncMock(side_effect=OSError("chain broken")),
    )
    r = stw.client.post("/api/strategies", json=_anlegen())
    assert _pruefe_form(r, 503, "audit_write_failed") == {}


def test_zuweisung_audit_fehler_503_audit_write_failed(stw, monkeypatch):
    from core import hitl_gate

    assert stw.client.post("/api/strategies", json=_anlegen("B")).status_code == 201
    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_assignment_event",
        AsyncMock(side_effect=OSError("chain broken")),
    )
    r = stw.client.post("/api/strategies/B/assign-live", json=_quittung())
    assert _pruefe_form(r, 503, "audit_write_failed") == {}


def test_ablage_unlesbar_503_ohne_rohtext(stw):
    stw.ablage.parent.mkdir(parents=True, exist_ok=True)
    stw.ablage.write_text("{broken-json-marker", encoding="utf-8")
    r = stw.client.get("/api/strategies")
    assert _pruefe_form(r, 503, "strategy_store_unreadable") == {}
    detail = r.json()["detail"]
    assert "Expecting" not in detail and "line 1" not in detail, "no raw exception text"


def test_ungueltiger_body_422_request_invalid_ohne_werte(stw):
    r = stw.client.post(
        "/api/strategies",
        json={"name": "A", "werte": {}, "acknowledgment": "secret-ack", "nonce": ""},
    )
    params = _pruefe_form(r, 422, "request_invalid")
    assert params == {"fields": [["nonce"]]}
    assert "secret-ack" not in r.text, "no request values in the error"


def test_flag_aus_404_bleibt_framework_form(stw, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    for r in (
        stw.client.get("/api/strategies"),
        stw.client.post("/api/strategies", json=_anlegen()),
        stw.client.request("DELETE", "/api/strategies/A", json=_quittung()),
        stw.client.post("/api/strategies/A/assign-live", json=_quittung()),
        stw.client.get("/api/strategies/A/versions"),
    ):
        assert r.status_code == 404
        assert r.json() == {"detail": "Not Found"}


def test_ein_unberuehrter_pfad_behaelt_die_framework_form(stw):
    """A plain ``HTTPException`` elsewhere keeps the framework handler (Plan §2 point 2)."""
    r = stw.client.get("/route-that-does-not-exist-4890")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


# ── language sweep over the catalogue ──────────────────────────────────────────


def _vorbereiten_drei(stw):
    for i, name in enumerate("ABC"):
        assert (
            stw.client.post(
                "/api/strategies", json=_anlegen(name, f"p-{i}")
            ).status_code
            == 201
        )


def _vorbereiten_a(stw):
    assert (
        stw.client.post("/api/strategies", json=_anlegen("A", "p-a")).status_code == 201
    )


def _hitl_aus(stw, monkeypatch):
    import config

    _vorbereiten_a(stw)
    monkeypatch.setattr(config, "HITL_ENABLED", False, raising=False)


def _audit_kaputt(stw, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_definition_event",
        AsyncMock(side_effect=OSError("chain broken")),
    )


def _ablage_kaputt(stw, monkeypatch):
    stw.ablage.parent.mkdir(parents=True, exist_ok=True)
    stw.ablage.write_text("{broken", encoding="utf-8")


def _kette_kaputt(stw, monkeypatch):
    from core import strategie_profile as sp

    _vorbereiten_a(stw)

    def kaputt(log_dir, name):
        raise OSError("chain not readable")

    monkeypatch.setattr(sp, "lies_versionen", kaputt)


def _a_zugewiesen(stw, monkeypatch):
    """#4891: ``A`` ist dem Live-Konto zugewiesen."""
    _vorbereiten_a(stw)
    r = stw.client.post("/api/strategies/A/assign-live", json=_quittung("p-assign"))
    assert r.status_code == 201, r.text


def _spiegel_kaputt(stw, monkeypatch):
    """#4891: ``zuweisungen.json`` neben ``strategien.json`` ist unlesbar."""
    stw.ablage.parent.mkdir(parents=True, exist_ok=True)
    (stw.ablage.parent / "zuweisungen.json").write_text("{broken", encoding="utf-8")


#: (id, preparation, method, path, json, status, code)
# fmt: off
FEHLERPFADE = [
    ("name_length", None, "POST", "/api/strategies", _anlegen("x" * 200), 422, "strategy_name_length"),
    ("name_empty", None, "POST", "/api/strategies", _anlegen("   "), 422, "strategy_name_length"),
    ("guardrail", None, "POST", "/api/strategies", _anlegen(HITL_ENABLED=False), 422, "guardrail_in_strategy"),
    ("limit", _vorbereiten_drei, "POST", "/api/strategies", _anlegen("D", "n-9"), 422, "strategy_limit_reached"),
    ("unknown_strategy_key", None, "POST", "/api/strategies", _anlegen(GIBT_ES_NICHT=1), 422, "unknown_setting"),
    ("enum", None, "POST", "/api/strategies", _anlegen(EXIT_TRAIL_PROFILE="aggressive"), 422, "setting_value_not_allowed"),
    ("bool", None, "POST", "/api/strategies", _anlegen(ROTATION_EXIT_ENABLED="maybe"), 422, "setting_value_type"),
    ("number", None, "POST", "/api/strategies", _anlegen(SIGNAL_BUY_THRESHOLD="abc"), 422, "setting_value_type"),
    ("number_bool", None, "POST", "/api/strategies", _anlegen(STOP_LOSS_PCT=True), 422, "setting_value_type"),
    ("integer", None, "POST", "/api/strategies", _anlegen(MAX_BUYS_PER_HOUR=2.5), 422, "setting_value_type"),
    ("text_type", None, "POST", "/api/strategies", _anlegen(LLM_RANKING_PROMPT=5), 422, "setting_value_type"),
    ("range", None, "POST", "/api/strategies", _anlegen(STOP_LOSS_PCT=9.5), 422, "setting_value_out_of_range"),
    ("text_long", None, "POST", "/api/strategies", _anlegen(LLM_RANKING_PROMPT="x" * 5000), 422, "setting_text_too_long"),
    ("strategy_prompt", None, "POST", "/api/strategies", _anlegen(LLM_RANKING_QUELLE="llm"), 422, "ranking_prompt_missing"),
    ("strategy_list", None, "POST", "/api/strategies", _anlegen(LLM_RANKING_QUELLE="liste", LLM_RANKING_LISTE=""), 422, "ranking_list_missing"),
    ("request_post", None, "POST", "/api/strategies", {"name": "A"}, 422, "request_invalid"),
    ("request_delete", None, "DELETE", "/api/strategies/A", {}, 422, "request_invalid"),
    ("request_assign", None, "POST", "/api/strategies/A/assign-live", {}, 422, "request_invalid"),
    ("delete_name", None, "DELETE", "/api/strategies/" + "x" * 70, _quittung(), 422, "strategy_name_length"),
    ("delete_missing", None, "DELETE", "/api/strategies/X", _quittung(), 404, "strategy_not_found"),
    ("assign_missing", None, "POST", "/api/strategies/X/assign-live", _quittung(), 404, "strategy_not_found"),
    ("versions_name", None, "GET", "/api/strategies/" + "x" * 70 + "/versions", None, 422, "strategy_name_length"),
    ("versions_missing", None, "GET", "/api/strategies/X/versions", None, 404, "strategy_not_found"),
    ("versions_chain", _kette_kaputt, "GET", "/api/strategies/A/versions", None, 503, "audit_chain_unreadable"),
    ("nonce", _vorbereiten_a, "POST", "/api/strategies", _anlegen("B", "p-a"), 409, "nonce_reused"),
    ("hitl", _hitl_aus, "POST", "/api/strategies/A/assign-live", _quittung(), 409, "hitl_required"),
    ("audit", _audit_kaputt, "POST", "/api/strategies", _anlegen(), 503, "audit_write_failed"),
    ("store", _ablage_kaputt, "GET", "/api/strategies", None, 503, "strategy_store_unreadable"),
    ("delete_assigned", _a_zugewiesen, "DELETE", "/api/strategies/A", _quittung(), 409, "strategy_assigned"),
    ("assignments_store", _spiegel_kaputt, "GET", "/api/strategy-assignments", None, 503, "strategy_store_unreadable"),
    ("settings_unknown", None, "POST", "/api/trading-settings", _settings(HARD_STOP_LOSS_PCT=-3.0), 422, "unknown_setting"),
    ("settings_enum", None, "POST", "/api/trading-settings", _settings(LLM_RANKING_RHYTHMUS="hourly"), 422, "setting_value_not_allowed"),
    ("settings_prompt", None, "POST", "/api/trading-settings", _settings(LLM_RANKING_QUELLE="llm_und_lstm", LLM_RANKING_PROMPT=""), 422, "ranking_prompt_missing"),
    ("settings_list", None, "POST", "/api/trading-settings", _settings(LLM_RANKING_QUELLE="liste", LLM_RANKING_LISTE=""), 422, "ranking_list_missing"),
]
# fmt: on


@pytest.mark.parametrize(
    "vorbereitung,methode,pfad,body,status,code",
    [p[1:] for p in FEHLERPFADE],
    ids=[p[0] for p in FEHLERPFADE],
)
def test_sprachsweep_alle_fehlerpfade(
    stw, monkeypatch, vorbereitung, methode, pfad, body, status, code
):
    if vorbereitung is not None:
        if vorbereitung in (_vorbereiten_drei, _vorbereiten_a):
            vorbereitung(stw)
        else:
            vorbereitung(stw, monkeypatch)
    r = stw.client.request(methode, pfad, json=body)
    _pruefe_form(r, status, code)
