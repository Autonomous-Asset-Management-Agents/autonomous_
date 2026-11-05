"""#5123 (STW-2e, Epic #4625) — ``POST /api/strategies/{name}/assign-paper``.

Die Console (#4953) und die Shell (#5117) rufen die Route schon; hier steht der Vertrag der
Engine: Eintrag auf der Kette der antwortenden Engine mit ``konto`` am Eintrag und am Span,
kein HITL-Gate auf Paper — **unter der Bedingung**, dass ``konto='live'`` mit 422 abgewiesen
wird, bevor irgendetwas geschrieben oder eine Nonce verbraucht ist (Owner-Bestaetigung
03.11.2026 an #5123).

Kette und Ablage sind echt (``LocalJSONAuditLogger`` in ``tmp_path``), wie in
``test_routes_steuerung.py`` und ``test_4890_fehlercodes.py``.

Plan: ``docs/5123-*/implementation_plan.md`` §5.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _kette_lesen(log_dir) -> list:
    return [
        json.loads(z)
        for f in sorted(log_dir.glob("audit_log_*.jsonl"))
        for z in f.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def _kette_verifiziert(log_dir) -> bool:
    """Hash und Verkettung je Zeile nachrechnen (wie ``senate_log._haenge_an``)."""
    vorher = None
    for zeile in _kette_lesen(log_dir):
        eintrag = dict(zeile)
        h = eintrag.pop("hash")
        eintrag.pop("sig", None)
        if vorher is not None and eintrag.get("prev_hash") != vorher:
            return False
        neu = hashlib.sha256(json.dumps(eintrag, sort_keys=True).encode()).hexdigest()
        if neu != h:
            return False
        vorher = h
    return True


def _zuweisungen(log_dir) -> list:
    return [
        z for z in _kette_lesen(log_dir) if z["event_type"] == "strategy_assignment"
    ]


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """Flag an, HITL an, echte Kette und Ablage, Auth aus, Strategie ``A`` angelegt."""
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
    monkeypatch.setattr(config, "HITL_ENABLED", True, raising=False)
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    from fastapi.testclient import TestClient

    client = TestClient(api_routes.app)
    r = client.post(
        "/api/strategies",
        json={
            "name": "A",
            "werte": {"STOP_LOSS_PCT": 5.0},
            "acknowledgment": "confirmed",
            "nonce": "n-def",
        },
    )
    assert r.status_code == 201, r.text
    yield SimpleNamespace(client=client, log_dir=log_dir)
    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)


def _basis(**abweichung) -> dict:
    """Volle ``basis`` aller Registry-Keys im Lieferzustand, wie ``PaperSlotStrategy.tsx``."""
    from core import trading_settings as ts

    b = {s.key: ts.canonical_text(s.key, s.default) for s in ts._SETTINGS}
    b.update(abweichung)
    return b


def _zuweisen(client, konto="paper1", nonce="N_PAPER", name="A", basis=None):
    return client.post(
        f"/api/strategies/{name}/assign-paper",
        json={
            "konto": konto,
            "basis": _basis() if basis is None else basis,
            "acknowledgment": "confirmed",
            "nonce": nonce,
        },
    )


def _spiegel() -> dict:
    from core import strategie_profile as sp

    return sp.lade_zuweisungen()


def _span_erfassen(monkeypatch) -> list:
    from core.engine import api_routes

    erfasst: list = []

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
    return erfasst


# ── 1 ──────────────────────────────────────────────────────────────────────────


def test_paper1_zuweisung_versiegelt_mit_konto(paper):
    from core import strategie_profile as sp
    from core import trading_settings as ts

    r = _zuweisen(paper.client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["success"] is True and body["strategie"] == "A"
    assert set(body["applied"]) == {s.key for s in ts._SETTINGS}, "voller Zielstand"
    assert body["applied"]["STOP_LOSS_PCT"] == ts.canonical_text("STOP_LOSS_PCT", 5.0)

    eintraege = _zuweisungen(paper.log_dir)
    assert len(eintraege) == 1
    assert (eintraege[0]["strategie"], eintraege[0]["konto"]) == ("A", "paper1")
    assert _kette_verifiziert(paper.log_dir)

    spiegel = _spiegel()
    assert spiegel["paper1"]["strategie"] == "A"
    assert spiegel["paper1"]["strategie_hash"] == sp.strategie_hash(
        {"STOP_LOSS_PCT": ts.canonical_text("STOP_LOSS_PCT", 5.0)}
    )
    assert "live" not in spiegel


def test_paper_zuweisung_laesst_live_spiegel_unveraendert(paper):
    r = paper.client.post(
        "/api/strategies/A/assign-live",
        json={"acknowledgment": "confirmed", "nonce": "N_LIVE"},
    )
    assert r.status_code == 201, r.text
    live_vorher = _spiegel()["live"]
    assert _zuweisen(paper.client).status_code == 201
    spiegel = _spiegel()
    assert spiegel["live"] == live_vorher
    assert spiegel["paper1"]["strategie"] == "A"


# ── 2: Regressionsschutz fuer das HITL-Gate ────────────────────────────────────


def test_live_wird_422_ohne_eintrag(paper):
    vorher = len(_kette_lesen(paper.log_dir))
    r = _zuweisen(paper.client, konto="live", nonce="N_X")
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "request_invalid"
    assert len(_kette_lesen(paper.log_dir)) == vorher, "kein Eintrag"
    assert _spiegel() == {}, "Spiegel unveraendert"
    # Die Nonce wurde nicht verbraucht: derselbe Wert gelingt danach auf paper1.
    assert _zuweisen(paper.client, konto="paper1", nonce="N_X").status_code == 201


# ── 3 ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("konto", ["paper4", "", "Paper1", None])
def test_unbekanntes_konto_422(paper, konto):
    r = _zuweisen(paper.client, konto=konto)
    assert r.status_code == 422, r.text
    assert _zuweisungen(paper.log_dir) == []


# ── 4 ──────────────────────────────────────────────────────────────────────────


def test_diff_gegen_basis(paper, monkeypatch):
    import config
    from core import trading_settings as ts

    # Die antwortende Engine steht bei 7.0, der Platz (basis) bei 3.0, die Strategie bei 5.0.
    monkeypatch.setattr(config, "STOP_LOSS_PCT", 7.0, raising=False)
    basis = _basis(STOP_LOSS_PCT="3.0")
    r = _zuweisen(paper.client, basis=basis)
    assert r.status_code == 201, r.text
    changes = {c["key"]: c for c in r.json()["changes"]}
    assert changes["STOP_LOSS_PCT"]["from"] == ts.canonical_text("STOP_LOSS_PCT", 3.0)
    assert changes["STOP_LOSS_PCT"]["to"] == ts.canonical_text("STOP_LOSS_PCT", 5.0)
    assert list(changes) == ["STOP_LOSS_PCT"], "nur Abweichungen von basis"


def test_diff_fehlende_basis_keys_sind_lieferzustand(paper):
    # Leere basis: fehlende Keys zaehlen mit ihrem Default.
    r = _zuweisen(paper.client, basis={})
    assert r.status_code == 201, r.text
    assert [c["key"] for c in r.json()["changes"]] == ["STOP_LOSS_PCT"]


# ── 5 ──────────────────────────────────────────────────────────────────────────


def test_basis_unbekannter_key_422(paper):
    r = _zuweisen(paper.client, basis=_basis(GIBT_ES_NICHT="1"))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "unknown_setting"
    assert _zuweisungen(paper.log_dir) == []


# ── 6 ──────────────────────────────────────────────────────────────────────────


def test_ohne_hitl_enabled_201(paper, monkeypatch):
    import config

    monkeypatch.setattr(config, "HITL_ENABLED", False, raising=False)
    r = _zuweisen(paper.client, konto="paper2")
    assert r.status_code == 201, r.text
    assert _zuweisungen(paper.log_dir)[0]["konto"] == "paper2"


# ── 7 ──────────────────────────────────────────────────────────────────────────


def test_nonce_replay_abgewiesen(paper):
    assert _zuweisen(paper.client, nonce="N_R").status_code == 201
    assert _zuweisen(paper.client, konto="paper2", nonce="N_R").status_code == 409
    assert len(_zuweisungen(paper.log_dir)) == 1


# ── 8 ──────────────────────────────────────────────────────────────────────────


def test_span_traegt_konto(paper, monkeypatch):
    erfasst = _span_erfassen(monkeypatch)
    assert _zuweisen(paper.client, konto="paper3").status_code == 201
    attrs = dict(erfasst)
    assert attrs["__name__"] == "strategy.assign"
    assert attrs["konto"] == "paper3"
    assert (attrs["strategie"], attrs["nonce"]) == ("A", "N_PAPER")
    assert attrs["mirror_failed"] == "false"


# ── 9 ──────────────────────────────────────────────────────────────────────────


def test_flag_aus_404(paper, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    assert _zuweisen(paper.client).status_code == 404
    assert _zuweisungen(paper.log_dir) == []


def test_unbekannte_strategie_404(paper):
    r = _zuweisen(paper.client, name="X")
    assert r.status_code == 404
    assert r.json()["code"] == "strategy_not_found"


def test_audit_fehler_503_ohne_spiegel(paper, monkeypatch):
    from core import hitl_gate

    monkeypatch.setattr(
        hitl_gate,
        "log_strategy_assignment_event",
        AsyncMock(side_effect=OSError("disk full")),
    )
    r = _zuweisen(paper.client)
    assert r.status_code == 503
    assert r.json()["code"] == "audit_write_failed"
    assert _spiegel() == {}


# ── 10 ─────────────────────────────────────────────────────────────────────────


def test_live_spiegel_default_unveraendert(paper):
    from core.engine.routes import strategie_zuweisung as sz

    assert sz.zuweisung_spiegeln("A", "sha256:x", "2026-11-03T00:00:00Z") is True
    assert _spiegel()["live"]["strategie"] == "A"
    assert sz.zuweisung_spiegeln(
        "A", "sha256:x", "2026-11-03T00:00:00Z", konto="paper2"
    )
    assert _spiegel()["paper2"]["strategie"] == "A"
