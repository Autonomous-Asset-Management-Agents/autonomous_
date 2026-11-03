"""#4069 (ARC-E6 G-4c) — die Steuerung liegt in ``core/engine/routes/steuerung.py``.

HITL, Einstellungen, Abweichungen, Portfolio-Form und Strategie sind die Stellschrauben,
die ein Mensch an der laufenden Engine dreht. Die HTTP-Flaeche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich umgezogen sind
und Patches auf ``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4069-g4c-steuerungs-router/implementation_plan.md``.
"""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
STEUERUNG = {
    ("GET", "/strategy"): "get_strategy",
    ("POST", "/set-strategy"): "set_strategy",
    ("POST", "/api/strategy/swap"): "strategy_swap",
    ("GET", "/api/hitl/pending"): "hitl_pending",
    ("POST", "/api/hitl/approve"): "hitl_approve",
    ("POST", "/api/hitl/reject"): "hitl_reject",
    ("POST", "/api/portfolio-shape"): "portfolio_shape",
    ("GET", "/api/trading-settings"): "trading_settings_get",
    ("POST", "/api/trading-settings"): "trading_settings_post",
    ("POST", "/api/settings-deviation-ack"): "settings_deviation_ack",
    ("GET", "/api/hitl/policy"): "hitl_get_policy",
    ("POST", "/api/hitl/policy"): "hitl_set_policy",
    # #4629 (STW-3): benannte Strategien.
    ("GET", "/api/strategies"): "strategies_list",
    ("POST", "/api/strategies"): "strategies_post",
    ("DELETE", "/api/strategies/{name}"): "strategies_delete",
    # #4630 (STW-4): Live-Zuweisung.
    ("POST", "/api/strategies/{name}/assign-live"): "strategies_assign_live",
    # #4863 (STW-3b): Versionsliste.
    ("GET", "/api/strategies/{name}/versions"): "strategies_versions",
    # #4891 (STW-4d): Lesepfad der Zuweisungen.
    ("GET", "/api/strategy-assignments"): "strategies_assignments",
}
HELFER = ("_hitl_policy_dto",)

_KEY = "test-engine-key-steuerung"


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_sechzehn_routen_liegen_im_steuerungs_router():
    from core.engine.routes import ROUTER, steuerung

    assert steuerung.router in ROUTER
    assert _routen(steuerung.router) == STEUERUNG


def test_die_app_bedient_die_steuerung_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in STEUERUNG
    }
    assert bedient == dict.fromkeys(STEUERUNG, "core.engine.routes.steuerung")


def test_api_routes_enthaelt_die_steuerung_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*STEUERUNG.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_ein_patch_auf_api_routes_hitl_gate_erreicht_die_steuerung():
    """Der Handler prueft die Nonce ueber ``ar._enforce_replay_distinct_nonce``, und der liest
    ``api_routes.hitl_gate`` — ein Patch dort muss im umgezogenen Handler ankommen."""
    from fastapi.testclient import TestClient

    from core import hitl_gate as echter_hitl_gate
    from core.engine import api_routes

    gepatcht = MagicMock()
    gepatcht.collect_live_enablement_nonces = AsyncMock(return_value={"n-1"})
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
        echter_hitl_gate, "log_settings_deviation_ack", AsyncMock()
    ) as siegel, patch.object(api_routes, "hitl_gate", gepatcht):
        antwort = TestClient(api_routes.app).post(
            "/api/settings-deviation-ack",
            headers={"X-Engine-Key": _KEY},
            json={
                "decision": "keep",
                "deviations": [{"key": "K", "current": "1", "default": "2"}],
                "nonce": "n-1",
            },
        )

    assert antwort.status_code == 409
    gepatcht.collect_live_enablement_nonces.assert_awaited_once()
    siegel.assert_not_awaited()


def test_ein_patch_auf_api_routes_engine_erreicht_den_strategie_swap():
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    gepatcht = MagicMock()
    gepatcht.api.list_positions.return_value = [MagicMock(symbol="AAPL")]
    # Die HMAC-Pruefung des Proxys gehoert nicht zu dieser Frage — nur der Positions-Lock.
    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes, "engine", gepatcht
        ):
            antwort = TestClient(api_routes.app).post(
                "/api/strategy/swap",
                headers={"X-Engine-Key": _KEY},
                json={"strategy_name": "RLAgent"},
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)

    assert antwort.status_code == 423
    gepatcht.api.list_positions.assert_called_once()


# ── #4629 (STW-3): /api/strategies und das auditierte /set-strategy ──────────
# Plan: docs/4629-*/implementation_plan.md §2 Punkte 4 und 5. Die Kette ist echt
# (LocalJSONAuditLogger in tmp_path), damit 409 und „Siegel vor Ablage" an dem gemessen
# werden, was auf der Platte steht.


def _kette_lesen(log_dir) -> list:
    import json

    return [
        json.loads(z)
        for f in sorted(log_dir.glob("audit_log_*.jsonl"))
        for z in f.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


@pytest.fixture
def stw(tmp_path, monkeypatch):
    """Flag an, echte Kette und Ablage in ``tmp_path``, Auth aus."""
    import config
    from core import hitl_gate
    from core.engine import api_routes
    from core.round_table.senate_log import LocalJSONAuditLogger

    log_dir = tmp_path / "senate"
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "udd"))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", True, raising=False)
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    from fastapi.testclient import TestClient

    yield SimpleNamespace(
        client=TestClient(api_routes.app),
        log_dir=log_dir,
        ablage=tmp_path / "udd" / "strategien.json",
    )
    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)


def _anlegen(name="A", nonce="n-1", **werte):
    return {
        "name": name,
        "werte": werte or {"STOP_LOSS_PCT": 5.0},
        "acknowledgment": "bestaetigt",
        "nonce": nonce,
    }


def _loeschen(client, name, nonce):
    return client.request(
        "DELETE",
        f"/api/strategies/{name}",
        json={"acknowledgment": "bestaetigt", "nonce": nonce},
    )


def test_strategies_404_bei_flag_aus(stw, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    assert stw.client.get("/api/strategies").status_code == 404
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 404
    assert _loeschen(stw.client, "A", "n-2").status_code == 404
    assert _kette_lesen(stw.log_dir) == []
    assert not stw.ablage.exists()


def test_strategies_anlegen_aendern_loeschen(stw):
    r = stw.client.post("/api/strategies", json=_anlegen())
    assert r.status_code == 201, r.text
    assert r.json()["aktion"] == "create"
    assert r.json()["werte"] == {"STOP_LOSS_PCT": "5.0"}

    r = stw.client.post("/api/strategies", json=_anlegen(nonce="n-2", STOP_LOSS_PCT=4))
    assert r.json()["aktion"] == "update"

    liste = stw.client.get("/api/strategies").json()
    assert liste["strategien"] == [{"name": "A", "werte": {"STOP_LOSS_PCT": "4.0"}}]
    assert liste["max_strategien"] == 3

    assert _loeschen(stw.client, "A", "n-3").status_code == 200
    assert stw.client.get("/api/strategies").json()["strategien"] == []

    kette = _kette_lesen(stw.log_dir)
    assert [(z["event_type"], z["aktion"]) for z in kette] == [
        ("strategy_definition", "create"),
        ("strategy_definition", "update"),
        ("strategy_definition", "delete"),
    ]
    assert kette[2]["werte"] == '{"STOP_LOSS_PCT":"4.0"}', "Loeschen nennt den Satz"


def test_strategies_siegel_vor_ablage(stw, monkeypatch):
    """Beim Schreiben der Ablage steht der Eintrag schon auf der Kette."""
    from core import strategie_profile as sp

    gesehen = []
    echt = sp.speichere_strategien

    def beobachtet(strategien, pfad=None):
        gesehen.append([z["event_type"] for z in _kette_lesen(stw.log_dir)])
        echt(strategien, pfad)

    monkeypatch.setattr(sp, "speichere_strategien", beobachtet)
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    assert gesehen == [["strategy_definition"]]


def test_strategies_422_bounds_und_unknown(stw):
    for body in (
        _anlegen(STOP_LOSS_PCT=9.5),
        _anlegen(EXIT_TRAIL_PROFILE="aggressiv"),
        _anlegen(HARD_STOP_LOSS_PCT=-5.0),
        _anlegen(GIBT_ES_NICHT=1),
    ):
        r = stw.client.post("/api/strategies", json=body)
        assert r.status_code == 422, (body, r.text)
    assert _kette_lesen(stw.log_dir) == []
    assert not stw.ablage.exists()


def test_strategies_vierte_ist_422(stw):
    for i, name in enumerate("ABC"):
        r = stw.client.post("/api/strategies", json=_anlegen(name, f"n-{i}"))
        assert r.status_code == 201
    r = stw.client.post("/api/strategies", json=_anlegen("D", "n-9"))
    assert r.status_code == 422
    assert len(_kette_lesen(stw.log_dir)) == 3


def test_strategies_409_nonce_replay(stw):
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    vorher = stw.ablage.read_bytes()

    r = stw.client.post("/api/strategies", json=_anlegen("B", "n-1"))
    assert r.status_code == 409
    assert _loeschen(stw.client, "A", "n-1").status_code == 409
    assert stw.ablage.read_bytes() == vorher
    assert len(_kette_lesen(stw.log_dir)) == 1


def test_strategies_unveraendert_schreibt_nichts(stw):
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    r = stw.client.post("/api/strategies", json=_anlegen(nonce="n-2"))
    assert r.status_code == 201
    assert r.json()["detail"].startswith("no change")
    assert len(_kette_lesen(stw.log_dir)) == 1


def test_strategies_loeschen_unbekannt_ist_404(stw):
    assert _loeschen(stw.client, "X", "n-1").status_code == 404
    assert _kette_lesen(stw.log_dir) == []


def test_strategies_worm_fehler_speichert_nichts(stw, monkeypatch):
    from core import hitl_gate

    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    vorher = stw.ablage.read_bytes()
    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_definition_event",
        AsyncMock(side_effect=OSError("Kette kaputt")),
    )
    r = stw.client.post("/api/strategies", json=_anlegen(nonce="n-2", STOP_LOSS_PCT=3))
    assert r.status_code == 503
    assert stw.ablage.read_bytes() == vorher


def test_strategies_span_ohne_werte(stw, monkeypatch):
    from core.engine import api_routes

    erfasst = []

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            erfasst.append((k, v))

    tracer = MagicMock()
    tracer.start_as_current_span.side_effect = lambda name: (
        erfasst.append(("__name__", name)) or _Span()
    )
    monkeypatch.setattr(api_routes, "_tracer", tracer)

    stw.client.post("/api/strategies", json=_anlegen())
    attrs = dict(erfasst)
    assert attrs["__name__"] == "strategy.define"
    assert (attrs["aktion"], attrs["name"], attrs["nonce"]) == ("create", "A", "n-1")
    assert "5.0" not in str(attrs.values()), "keine Werte in der Telemetrie"


# ── #4660: Pruefgrund je Symbol bei der eigenen Liste ──────────────────────────


def _erwartet_verworfen(text, top_k=None):
    from core import trading_settings as ts
    from core.engine.llm_ranking import pruefe_symbolliste
    from core.round_table.sp500_universe import get_sp500_symbols

    k = top_k if top_k is not None else ts.REGISTRY["ROUND_TABLE_TOP_K_EVAL"].default
    return pruefe_symbolliste(text, set(get_sp500_symbols()), int(k))[1]


def test_strategies_liste_nennt_verworfene_symbole(stw):
    r = stw.client.post(
        "/api/strategies",
        json=_anlegen(
            LLM_RANKING_QUELLE="liste",
            LLM_RANKING_LISTE="AAPL, XYZ1",
            ROUND_TABLE_TOP_K_EVAL=20,
        ),
    )
    assert r.status_code == 201
    verworfen = r.json()["liste_verworfen"]
    assert set(verworfen) == {"XYZ1"}
    assert verworfen == _erwartet_verworfen("AAPL, XYZ1", 20)


def test_strategies_andere_quelle_liste_verworfen_ist_null(stw):
    r = stw.client.post("/api/strategies", json=_anlegen(LLM_RANKING_QUELLE="lstm"))
    assert r.status_code == 201
    assert r.json()["liste_verworfen"] is None


def test_strategies_liste_ohne_aenderung_nennt_verworfene_trotzdem(stw):
    werte = {"LLM_RANKING_QUELLE": "liste", "LLM_RANKING_LISTE": "AAPL, XYZ1"}
    assert stw.client.post("/api/strategies", json=_anlegen(**werte)).status_code == 201
    r = stw.client.post("/api/strategies", json=_anlegen(nonce="n-2", **werte))
    assert r.json()["detail"].startswith("no change")
    assert r.json()["liste_verworfen"] == _erwartet_verworfen("AAPL, XYZ1")


def test_strategies_liste_ohne_top_k_nimmt_registry_default(stw):
    r = stw.client.post(
        "/api/strategies",
        json=_anlegen(LLM_RANKING_QUELLE="liste", LLM_RANKING_LISTE="AAPL, XYZ1"),
    )
    assert r.status_code == 201
    assert r.json()["liste_verworfen"] == _erwartet_verworfen("AAPL, XYZ1")


# — /set-strategy —


@pytest.fixture
def set_strategy_kette(monkeypatch):
    from core import hitl_gate
    from core.engine import api_routes

    geschrieben = []

    async def schreiben(**kw):
        # Beim Schreiben ist noch die alte Strategie aktiv (audit-before-change).
        geschrieben.append((kw, api_routes.config.ACTIVE_STRATEGY))

    monkeypatch.setattr(
        hitl_gate, "log_settings_change_event", AsyncMock(side_effect=schreiben)
    )
    monkeypatch.setattr(
        api_routes, "config", SimpleNamespace(ACTIVE_STRATEGY="RLAgent")
    )
    return geschrieben


def _set_strategy(name):
    import asyncio

    from core.engine.routes import steuerung

    return asyncio.run(steuerung.set_strategy({"strategy": name}, None))


def test_set_strategy_schreibt_vor_umschalten(set_strategy_kette):
    from core.engine import api_routes

    assert _set_strategy("LSTMDynamic") == {
        "status": "success",
        "strategy": "LSTMDynamic",
    }
    assert api_routes.config.ACTIVE_STRATEGY == "LSTMDynamic"
    ((kw, aktiv_beim_schreiben),) = set_strategy_kette
    assert aktiv_beim_schreiben == "RLAgent"
    assert kw["changes"] == [
        {"key": "ACTIVE_STRATEGY", "from": "RLAgent", "to": "LSTMDynamic"}
    ]
    assert kw["actor"] == "operator"
    assert kw["acknowledgment"] == "Operator switched active strategy via /set-strategy"
    assert kw["strict"] is True
    assert kw["nonce"] and kw["switch_id"] == kw["nonce"]


def test_set_strategy_funktioniert_auch_wenn_config_active_strategy_gemockt(
    set_strategy_kette, monkeypatch
):
    from core.engine import api_routes

    monkeypatch.setattr(api_routes, "config", MagicMock())
    assert _set_strategy("LSTMDynamic")["status"] == "success"
    ((kw, _),) = set_strategy_kette
    assert kw["changes"][0]["from"] == "RLAgent"


def test_set_strategy_write_fehler_kein_umschalten(monkeypatch):
    from fastapi import HTTPException

    from core import hitl_gate
    from core.engine import api_routes

    monkeypatch.setattr(
        hitl_gate,
        "log_settings_change_event",
        AsyncMock(side_effect=OSError("Kette kaputt")),
    )
    monkeypatch.setattr(
        api_routes, "config", SimpleNamespace(ACTIVE_STRATEGY="RLAgent")
    )
    with pytest.raises(HTTPException) as fehler:
        _set_strategy("LSTMDynamic")
    assert fehler.value.status_code == 503
    assert api_routes.config.ACTIVE_STRATEGY == "RLAgent"


def test_set_strategy_gleicher_name_kein_write(set_strategy_kette):
    assert _set_strategy("RLAgent") == {"status": "success", "strategy": "RLAgent"}
    assert set_strategy_kette == []


# ── #4630 (STW-4): POST /api/strategies/{name}/assign-live ─────────────────────
# Plan: docs/4630-*/implementation_plan.md §2 Punkt 3. Kette und Ablage echt wie oben.


@pytest.fixture
def zuweisung(stw, monkeypatch):
    """``stw`` plus HITL an und eine angelegte Strategie ``B``."""
    import config

    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    r = stw.client.post(
        "/api/strategies", json=_anlegen("B", "n-def", STOP_LOSS_PCT=5.0)
    )
    assert r.status_code == 201, r.text
    return stw


def _zuweisen(client, name="B", nonce="N_ASSIGN"):
    return client.post(
        f"/api/strategies/{name}/assign-live",
        json={"acknowledgment": "bestaetigt", "nonce": nonce},
    )


def _zuweisungen(log_dir) -> list:
    return [
        z for z in _kette_lesen(log_dir) if z["event_type"] == "strategy_assignment"
    ]


def test_assign_live_404_bei_flag_aus(zuweisung, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    assert _zuweisen(zuweisung.client).status_code == 404
    assert _zuweisungen(zuweisung.log_dir) == []


def test_assign_live_404_unbekannte_strategie(zuweisung):
    assert _zuweisen(zuweisung.client, name="X").status_code == 404
    assert _zuweisungen(zuweisung.log_dir) == []


def test_assign_live_409_ohne_hitl_kein_write(zuweisung, monkeypatch):
    import config

    monkeypatch.setattr(config, "HITL_ENABLED", False, raising=False)
    r = _zuweisen(zuweisung.client)
    assert r.status_code == 409
    assert "HITL" in r.json()["detail"]
    assert _zuweisungen(zuweisung.log_dir) == []


def test_assign_live_409_nonce_replay(zuweisung):
    assert _zuweisen(zuweisung.client).status_code == 201
    assert _zuweisen(zuweisung.client).status_code == 409
    assert len(_zuweisungen(zuweisung.log_dir)) == 1


def test_assign_live_schreibt_vor_antwort(zuweisung):
    from core import strategie_profile as sp
    from core import trading_settings as ts

    vorher = ts.current_settings()
    r = _zuweisen(zuweisung.client)
    assert r.status_code == 201, r.text
    antwort = r.json()

    (eintrag,) = _zuweisungen(zuweisung.log_dir)
    assert (eintrag["strategie"], eintrag["konto"]) == ("B", "live")
    assert eintrag["nonce"] == eintrag["switch_id"] == "N_ASSIGN"
    werte = sp.werte_fuer_kette({"STOP_LOSS_PCT": "5.0"})
    assert (
        eintrag["strategie_hash"]
        == "sha256:" + hashlib.sha256(werte.encode("utf-8")).hexdigest()
    )
    assert json.loads(eintrag["changes"]) == ts.worm_changes(antwort["changes"])

    assert antwort["strategie"] == "B"
    assert antwort["applied"]["STOP_LOSS_PCT"] == "5.0"
    if vorher["STOP_LOSS_PCT"] != "5.0":
        assert {
            "key": "STOP_LOSS_PCT",
            "from": vorher["STOP_LOSS_PCT"],
            "to": "5.0",
        } in antwort["changes"]
    # Die Engine-Werte selbst aendert die Route nicht — wirksam wird erst der Neustart.
    assert ts.current_settings() == vorher


def test_assign_live_leerer_diff_wird_trotzdem_versiegelt(zuweisung, monkeypatch):
    """Ziel == Ist-Stand: ``changes == []`` und trotzdem ein Eintrag (Plan §2.3.7)."""
    from core import trading_settings as ts
    from core.engine.routes import steuerung

    monkeypatch.setattr(
        steuerung, "_zuweisungs_ziel", lambda werte: ts.current_settings()
    )
    r = _zuweisen(zuweisung.client)
    assert r.status_code == 201, r.text
    assert r.json()["changes"] == []
    (eintrag,) = _zuweisungen(zuweisung.log_dir)
    assert eintrag["changes"] == "[]"


def test_assign_live_diff_nach_cross_clamping(zuweisung):
    """MAX ueber MAX_SIZING wird geklemmt — Antwort und Kette nennen den geklemmten Wert."""
    r = zuweisung.client.post(
        "/api/strategies",
        json=_anlegen(
            "C",
            "n-def-c",
            MAX_POSITION_PERCENT=0.40,
            MAX_POSITION_PERCENT_SIZING=0.30,
        ),
    )
    assert r.status_code == 201, r.text

    r = _zuweisen(zuweisung.client, name="C", nonce="N_C")
    assert r.status_code == 201, r.text
    antwort = r.json()
    assert antwort["applied"]["MAX_POSITION_PERCENT"] == "0.30"
    assert all(c["to"] != "0.40" for c in antwort["changes"])
    (eintrag,) = _zuweisungen(zuweisung.log_dir)
    assert '"0.40"' not in eintrag["changes"]


def test_assign_live_nicht_genannte_keys_auf_lieferzustand(zuweisung, monkeypatch):
    """Ziel-Satz = Lieferzustand | Strategie (Plan §2.3.2, §6)."""
    import config
    from core import trading_settings as ts

    # Ist-Stand der Engine: eine Abweichung vom Lieferzustand, die B nicht nennt.
    monkeypatch.setattr(
        config, "get_config", lambda: SimpleNamespace(SIGNAL_BUY_THRESHOLD=0.60)
    )
    assert ts.current_settings()["SIGNAL_BUY_THRESHOLD"] == "0.60"
    r = _zuweisen(zuweisung.client)
    assert r.status_code == 201, r.text
    lieferung = ts.canonical_text(
        "SIGNAL_BUY_THRESHOLD", ts.REGISTRY["SIGNAL_BUY_THRESHOLD"].default
    )
    assert {"key": "SIGNAL_BUY_THRESHOLD", "from": "0.60", "to": lieferung} in (
        r.json()["changes"]
    )


def test_assign_live_write_fehler_keine_201(zuweisung, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_assignment_event",
        AsyncMock(side_effect=OSError("Kette kaputt")),
    )
    r = _zuweisen(zuweisung.client)
    assert r.status_code == 503
    assert _zuweisungen(zuweisung.log_dir) == []


def test_assign_live_span_ohne_werte(zuweisung, monkeypatch):
    from core.engine import api_routes

    erfasst = []

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            erfasst.append((k, v))

    tracer = MagicMock()
    tracer.start_as_current_span.side_effect = lambda name: (
        erfasst.append(("__name__", name)) or _Span()
    )
    monkeypatch.setattr(api_routes, "_tracer", tracer)

    assert _zuweisen(zuweisung.client).status_code == 201
    attrs = dict(erfasst)
    assert attrs["__name__"] == "strategy.assign"
    assert (attrs["strategie"], attrs["nonce"]) == ("B", "N_ASSIGN")
    assert attrs["restart_required"] == "true"
    assert attrs["actor"] == "operator"
    assert "5.0" not in str(attrs.values()), "keine Werte in der Telemetrie"


def test_set_strategy_unbekannt_kein_write(set_strategy_kette):
    assert _set_strategy("Gibtsnicht")["status"] == "error"
    assert set_strategy_kette == []


# ── #4863 (STW-3b): Anlagereihenfolge (ADR-027 Regel 2) ───────────────────────


def _namen(client) -> list:
    return [s["name"] for s in client.get("/api/strategies").json()["strategien"]]


def test_strategies_liste_in_anlagereihenfolge(stw):
    for name, nonce in (("Wider stops", "n-1"), ("Momentum only", "n-2")):
        r = stw.client.post("/api/strategies", json=_anlegen(name, nonce))
        assert r.status_code == 201
    assert _namen(stw.client) == ["Wider stops", "Momentum only"]


def test_strategies_aendern_verschiebt_nicht(stw):
    for name, nonce in (("B", "n-1"), ("A", "n-2")):
        r = stw.client.post("/api/strategies", json=_anlegen(name, nonce))
        assert r.status_code == 201
    r = stw.client.post("/api/strategies", json=_anlegen("B", "n-3", STOP_LOSS_PCT=4))
    assert r.json()["aktion"] == "update"
    assert _namen(stw.client) == ["B", "A"]


def test_strategies_loeschen_und_neu_steht_am_ende(stw):
    for name, nonce in (("A", "n-1"), ("B", "n-2")):
        r = stw.client.post("/api/strategies", json=_anlegen(name, nonce))
        assert r.status_code == 201
    assert _loeschen(stw.client, "A", "n-3").status_code == 200
    assert (
        stw.client.post("/api/strategies", json=_anlegen("A", "n-4")).status_code == 201
    )
    assert _namen(stw.client) == ["B", "A"]


# ── #4863 (STW-3b): GET /api/strategies/{name}/versions ──────────────────────


def test_strategies_versions_aus_der_kette(stw):
    from core import strategie_profile as sp

    assert (
        stw.client.post("/api/strategies", json=_anlegen("Wider stops")).status_code
        == 201
    )
    r = stw.client.post(
        "/api/strategies", json=_anlegen("Wider stops", "n-2", STOP_LOSS_PCT=4)
    )
    assert r.status_code == 201

    r = stw.client.get("/api/strategies/Wider stops/versions")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Wider stops"
    versionen = body["versionen"]
    assert [v["aktion"] for v in versionen] == ["update", "create"]
    assert versionen[0]["felder"] == ["STOP_LOSS_PCT"]
    assert versionen[0]["hash"] == sp.strategie_hash({"STOP_LOSS_PCT": "4.0"})
    assert versionen[0]["hash_kuerzel"] == versionen[0]["hash"][7:15]
    assert all(v["zeitpunkt"] for v in versionen)


def test_strategies_versions_404_bei_flag_aus(stw, monkeypatch):
    import config

    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    assert stw.client.get("/api/strategies/A/versions").status_code == 404


def test_strategies_versions_404_unbekannt(stw):
    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201
    assert stw.client.get("/api/strategies/Z/versions").status_code == 404


def test_strategies_versions_503_kette_unlesbar(stw, monkeypatch):
    from core import strategie_profile as sp

    assert stw.client.post("/api/strategies", json=_anlegen()).status_code == 201

    def kaputt(log_dir, name):
        raise OSError("Kette nicht lesbar")

    monkeypatch.setattr(sp, "lies_versionen", kaputt)
    r = stw.client.get("/api/strategies/A/versions")
    assert r.status_code == 503
    assert "detail" in r.json()


def test_strategies_versions_braucht_engine_key(stw):
    from core.engine import api_routes

    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}):
        assert stw.client.get("/api/strategies/A/versions").status_code in (401, 403)
        r = stw.client.get("/api/strategies/A/versions", headers={"X-Engine-Key": _KEY})
        assert r.status_code == 404, "mit Key: Ablage leer, also unbekannt"


# ── #4891 (STW-4d): Lesepfad der Zuweisung, Spiegel, Loeschsperre ────────────
# Plan: docs/4891-*/implementation_plan.md §2 Punkte 2-4, TDD-Reihenfolge Schritte 2-4.
# Kette, Ablage und Spiegel echt in ``tmp_path`` wie oben.


def _spiegel(stw):
    return stw.ablage.parent / "zuweisungen.json"


class _SpanAttribute:
    """Sammelt die Attribute eines Spans in ``erfasst``."""

    def __init__(self, erfasst: dict):
        self.erfasst = erfasst

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def set_attribute(self, k, v):
        self.erfasst[k] = v


def _span_erfassen(monkeypatch) -> dict:
    from core.engine import api_routes

    erfasst: dict = {}
    tracer = MagicMock()
    tracer.start_as_current_span.side_effect = lambda name: _SpanAttribute(erfasst)
    monkeypatch.setattr(api_routes, "_tracer", tracer)
    return erfasst


def test_assignments_404_bei_flag_aus(zuweisung, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    assert zuweisung.client.get("/api/strategy-assignments").status_code == 404


def test_assignments_leer_ohne_zuweisung(zuweisung):
    r = zuweisung.client.get("/api/strategy-assignments")
    assert r.status_code == 200, r.text
    assert r.json() == {"zuweisungen": []}


def test_assignments_nach_assign_live(zuweisung):
    from core import strategie_profile as sp

    assert _zuweisen(zuweisung.client).status_code == 201
    (eintrag,) = _zuweisungen(zuweisung.log_dir)

    r = zuweisung.client.get("/api/strategy-assignments")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "zuweisungen": [
            {
                "konto": "live",
                "strategie": "B",
                "strategie_hash": sp.strategie_hash({"STOP_LOSS_PCT": "5.0"}),
                "timestamp": eintrag["timestamp"],
            }
        ]
    }


def test_assignments_503_ablage_unlesbar(zuweisung):
    _spiegel(zuweisung).write_text("{kaputt", encoding="utf-8")
    r = zuweisung.client.get("/api/strategy-assignments")
    assert r.status_code == 503
    assert r.json()["code"] == "strategy_store_unreadable"


def test_assignments_liest_kette_nicht(zuweisung, monkeypatch):
    """Der Lesepfad haengt am Spiegel, nie an der Kette (Plan §2 Punkt 3, Option B verworfen)."""
    from core import hitl_gate
    from core import strategie_profile as sp

    assert _zuweisen(zuweisung.client).status_code == 201

    def kette_verboten(*a, **kw):
        raise AssertionError("Lesepfad liest die Kette")

    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", kette_verboten)
    monkeypatch.setattr(sp, "lies_versionen", kette_verboten)
    r = zuweisung.client.get("/api/strategy-assignments")
    assert r.status_code == 200, r.text
    assert [z["strategie"] for z in r.json()["zuweisungen"]] == ["B"]


def test_assign_live_spiegel_nach_worm(zuweisung, monkeypatch):
    """Beim Schreiben des Spiegels steht die Zuweisung schon auf der Kette."""
    from core import strategie_profile as sp

    gesehen = []
    echt = sp.speichere_zuweisung

    def beobachtet(*a, **kw):
        gesehen.append(len(_zuweisungen(zuweisung.log_dir)))
        echt(*a, **kw)

    monkeypatch.setattr(sp, "speichere_zuweisung", beobachtet)
    assert _zuweisen(zuweisung.client).status_code == 201
    assert gesehen == [1]


def test_assign_live_worm_fehler_kein_spiegel(zuweisung, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_assignment_event",
        AsyncMock(side_effect=OSError("Kette kaputt")),
    )
    assert _zuweisen(zuweisung.client).status_code == 503
    assert not _spiegel(zuweisung).exists()


def test_assign_live_spiegel_fehler_bleibt_201(zuweisung, monkeypatch, caplog):
    """Versiegelt ist versiegelt: die Shell muss die Zuweisung wirksam machen (Plan §2.2)."""
    import logging

    from core import strategie_profile as sp

    erfasst = _span_erfassen(monkeypatch)

    def kaputt(*a, **kw):
        raise OSError("Platte voll")

    monkeypatch.setattr(sp, "speichere_zuweisung", kaputt)
    with caplog.at_level(logging.WARNING):
        r = _zuweisen(zuweisung.client)
    assert r.status_code == 201, r.text
    assert len(_zuweisungen(zuweisung.log_dir)) == 1
    assert any(
        rec.levelno == logging.WARNING and "zuweisungen.json" in rec.getMessage()
        for rec in caplog.records
    )
    assert erfasst["mirror_failed"] == "true"


def test_assign_live_span_mirror_failed_false(zuweisung, monkeypatch):
    erfasst = _span_erfassen(monkeypatch)
    assert _zuweisen(zuweisung.client).status_code == 201
    assert erfasst["mirror_failed"] == "false"


def test_strategies_loeschen_zugewiesen_ist_409(zuweisung, monkeypatch):
    from core import hitl_gate

    assert _zuweisen(zuweisung.client).status_code == 201
    vorher = zuweisung.ablage.read_bytes()
    kette_vorher = _kette_lesen(zuweisung.log_dir)
    siegel = AsyncMock()
    monkeypatch.setattr(hitl_gate, "log_strategy_definition_event", siegel)

    r = _loeschen(zuweisung.client, "B", "N_DEL")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "strategy_assigned"
    assert r.json()["detail"] == (
        "This strategy is assigned to an account. "
        "Assign another strategy to that account first."
    )
    assert zuweisung.ablage.read_bytes() == vorher
    assert _kette_lesen(zuweisung.log_dir) == kette_vorher
    siegel.assert_not_awaited()


def test_strategies_loeschen_zugewiesen_verbraucht_nonce_nicht(zuweisung):
    assert _zuweisen(zuweisung.client).status_code == 201
    assert _loeschen(zuweisung.client, "B", "N_DEL").status_code == 409

    r = zuweisung.client.post("/api/strategies", json=_anlegen("C", "n-def-c"))
    assert r.status_code == 201
    assert _loeschen(zuweisung.client, "C", "N_DEL").status_code == 200


def test_strategies_loeschen_nach_neuzuweisung_erlaubt(zuweisung):
    r = zuweisung.client.post("/api/strategies", json=_anlegen("C", "n-def-c"))
    assert r.status_code == 201
    assert _zuweisen(zuweisung.client, "B", "N_B").status_code == 201
    assert _zuweisen(zuweisung.client, "C", "N_C").status_code == 201

    assert _loeschen(zuweisung.client, "B", "N_DEL").status_code == 200
    assert _namen(zuweisung.client) == ["C"]
    assert _kette_lesen(zuweisung.log_dir)[-1]["aktion"] == "delete"
    assert _loeschen(zuweisung.client, "C", "N_DEL2").status_code == 409


def test_strategies_loeschen_503_bei_unlesbarer_zuweisung(zuweisung):
    _spiegel(zuweisung).write_text("{kaputt", encoding="utf-8")
    vorher = zuweisung.ablage.read_bytes()
    kette_vorher = _kette_lesen(zuweisung.log_dir)

    r = _loeschen(zuweisung.client, "B", "N_DEL")
    assert r.status_code == 503
    assert r.json()["code"] == "strategy_store_unreadable"
    assert zuweisung.ablage.read_bytes() == vorher
    assert _kette_lesen(zuweisung.log_dir) == kette_vorher


# ── #4989: Verfahrenswechsel im Live-Betrieb ───────────────────────────────────


def _verfahren_text(alt, neu):
    # Gepinnt: dieselben Bytes stehen in src/console/desktop/strategyWorkshop/acknowledgments.ts.
    return (
        f'I confirm changing the live sizing method from "{alt}" to "{neu}". '
        "The change applies to real-money trading and is recorded on a tamper-evident "
        "audit trail."
    )


@pytest.fixture
def live(stw, monkeypatch):
    """``stw`` im Live-Betrieb, Verfahren ``b``, HITL an, Strategie ``B`` wechselt auf ``a``."""
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", False, raising=False)
    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    monkeypatch.setattr(config.get_config(), "CLEAN_WEIGHT_SIZING", "b")
    r = stw.client.post(
        "/api/strategies", json=_anlegen("B", "n-def", CLEAN_WEIGHT_SIZING="a")
    )
    assert r.status_code == 201, r.text
    return stw


def _wechsel(client, ack=None, nonce="N_SIZE", **settings):
    return client.post(
        "/api/trading-settings",
        json={
            "settings": settings or {"CLEAN_WEIGHT_SIZING": "a"},
            "acknowledgment": ack or "generisch bestaetigt",
            "nonce": nonce,
        },
    )


def _einstell_eintraege(log_dir) -> list:
    return [z for z in _kette_lesen(log_dir) if z["event_type"] == "settings_change"]


def test_live_wechsel_ohne_text_ist_409_ohne_eintrag(live):
    vorher = _kette_lesen(live.log_dir)

    r = _wechsel(live.client)

    assert r.status_code == 409
    assert r.json()["code"] == "sizing_change_unconfirmed"
    assert r.json()["params"] == {"from": "b", "to": "a"}
    assert _kette_lesen(live.log_dir) == vorher


def test_live_wechsel_mit_vertauschtem_text_ist_409(live):
    r = _wechsel(live.client, ack=_verfahren_text("a", "b"))
    assert r.status_code == 409
    assert r.json()["code"] == "sizing_change_unconfirmed"


def test_live_wechsel_mit_text_steht_auf_der_kette(live):
    text = _verfahren_text("b", "a")

    r = _wechsel(live.client, ack=text)

    assert r.status_code == 201, r.text
    assert r.json()["applied"]["CLEAN_WEIGHT_SIZING"] == "a"
    eintrag = _einstell_eintraege(live.log_dir)[-1]
    assert eintrag["acknowledgment"] == text
    assert json.loads(eintrag["changes"]) == [
        {"key": "CLEAN_WEIGHT_SIZING", "from": "b", "to": "a"}
    ]


def test_live_abgelehnter_wechsel_verbraucht_die_nonce_nicht(live):
    assert _wechsel(live.client, nonce="N_GLEICH").status_code == 409
    r = _wechsel(live.client, ack=_verfahren_text("b", "a"), nonce="N_GLEICH")
    assert r.status_code == 201, r.text


def test_live_aenderung_anderer_keys_braucht_keinen_verfahrenstext(live):
    r = _wechsel(live.client, STOP_LOSS_PCT=5.0)
    assert r.status_code == 201, r.text


def test_live_gleiches_verfahren_ist_kein_wechsel(live):
    r = _wechsel(live.client, CLEAN_WEIGHT_SIZING="b")
    assert r.status_code == 201, r.text
    assert r.json()["changes"] == []


def test_paper_wechsel_bleibt_bei_der_normalen_bestaetigung(live, monkeypatch):
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    r = _wechsel(live.client)
    assert r.status_code == 201, r.text
    assert (
        _einstell_eintraege(live.log_dir)[-1]["acknowledgment"]
        == "generisch bestaetigt"
    )


@pytest.mark.parametrize("modus", [None, "false", 0, "unbekannt", MagicMock()])
def test_unbekannter_modus_zaehlt_als_live(live, monkeypatch, modus):
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", modus, raising=False)
    assert _wechsel(live.client).status_code == 409


def test_abgelehnter_wechsel_span_nennt_keys_und_nonce_nie_werte(live, monkeypatch):
    from core.engine import api_routes

    attribute = {}
    span = MagicMock()
    span.__enter__.return_value.set_attribute.side_effect = attribute.__setitem__
    tracer = MagicMock()
    tracer.start_as_current_span.return_value = span
    monkeypatch.setattr(api_routes, "_tracer", tracer)

    assert _wechsel(live.client).status_code == 409

    tracer.start_as_current_span.assert_called_once_with("settings.apply_rejected")
    assert attribute["keys"] == "CLEAN_WEIGHT_SIZING"
    assert attribute["nonce"] == "N_SIZE"
    assert set(map(str, attribute.values())).isdisjoint({"a", "b"})


def test_assign_live_ohne_text_ist_409_ohne_eintrag(live):
    vorher = _kette_lesen(live.log_dir)

    r = _zuweisen(live.client)

    assert r.status_code == 409
    assert r.json()["code"] == "sizing_change_unconfirmed"
    assert r.json()["params"] == {"from": "b", "to": "a"}
    assert _kette_lesen(live.log_dir) == vorher


def test_assign_live_mit_text_steht_auf_der_kette(live):
    text = _verfahren_text("b", "a")

    r = live.client.post(
        "/api/strategies/B/assign-live",
        json={"acknowledgment": text, "nonce": "N_ASSIGN"},
    )

    assert r.status_code == 201, r.text
    eintrag = _zuweisungen(live.log_dir)[-1]
    assert eintrag["acknowledgment"] == text
    assert {"key": "CLEAN_WEIGHT_SIZING", "from": "b", "to": "a"} in json.loads(
        eintrag["changes"]
    )


def test_assign_live_lieferzustand_ist_auch_ein_wechsel(live, monkeypatch):
    # Strategie ohne das Feld: sie faellt auf den Auslieferungswert ``b`` zurueck.
    import config

    monkeypatch.setattr(config.get_config(), "CLEAN_WEIGHT_SIZING", "a")
    r = live.client.post("/api/strategies", json=_anlegen("C", "n-def-c"))
    assert r.status_code == 201, r.text

    r = _zuweisen(live.client, "C")

    assert r.status_code == 409
    assert r.json()["params"] == {"from": "a", "to": "b"}


def test_assign_live_paper_bleibt_bei_der_normalen_bestaetigung(live, monkeypatch):
    import config

    monkeypatch.setattr(config, "PAPER_TRADING", True, raising=False)
    assert _zuweisen(live.client).status_code == 201
