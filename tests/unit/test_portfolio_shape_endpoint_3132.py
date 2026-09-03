"""#3132 — /api/portfolio-shape: Vertrag des Endpunkts.

Die beiden Szenarien, auf die es ankommt:
  * **Audit vor Aenderung** — der WORM-Eintrag entsteht, BEVOR der Aufrufer persistiert.
  * **Fehlgeschlagene WORM-Schreibung verhindert die Aenderung** — der Endpunkt meldet
    dann keinen Erfolg, sonst gaebe es eine Kapitalentscheidung ohne Protokoll.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    from core.engine import api_routes

    # Engine-Key-Gate im Test aushebeln — geprueft wird der Endpunkt, nicht die Auth.
    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    monkeypatch.setattr(
        api_routes, "_enforce_replay_distinct_nonce", _noop_async, raising=False
    )
    yield TestClient(api_routes.app)
    api_routes.app.dependency_overrides.clear()


from unittest.mock import AsyncMock

_noop_async = AsyncMock()


@pytest.fixture
def worm(monkeypatch):
    """Sammelt die WORM-Schreibungen statt sie auf die Kette zu legen."""
    from core import hitl_gate

    written = []

    async def side_effect(**kw):
        written.append(kw)

    _log = AsyncMock(side_effect=side_effect)

    monkeypatch.setattr(hitl_gate, "log_portfolio_shape_event", _log)
    return written


@pytest.fixture(autouse=True)
def heutiger_stand(monkeypatch):
    from core import portfolio_shape

    monkeypatch.setattr(
        portfolio_shape,
        "current_shape",
        lambda cfg=None: {"positions": 10, "hold_days": 20.0, "vol_target": 0.015},
    )


def _body(**kw):
    return {"acknowledgment": "bestaetigt", "nonce": "n-1", **kw}


def test_grenzen_werden_geklemmt_nicht_abgelehnt(client, worm):
    r = client.post("/api/portfolio-shape", json=_body(positions=25, hold_days=1))
    assert r.status_code == 201
    body = r.json()
    assert body["applied"]["positions"] == 20, "25 klemmt auf die Mechanik-Obergrenze"
    assert body["applied"]["hold_days"] == 5.0


def test_ohne_aenderung_kein_worm_eintrag(client, worm):
    r = client.post(
        "/api/portfolio-shape", json=_body(positions=10, hold_days=20, vol_target=0.015)
    )
    assert r.status_code == 201
    assert r.json()["changes"] == []
    assert worm == [], "eine Nicht-Aenderung darf die Kette nicht beschreiben"


def test_aenderung_wird_protokolliert(client, worm):
    r = client.post("/api/portfolio-shape", json=_body(positions=20, hold_days=10))
    assert r.status_code == 201
    assert len(worm) == 1
    geschrieben = worm[0]
    assert geschrieben["strict"] is True, "ohne strict koennte ein Fehler durchrutschen"
    assert geschrieben["nonce"] == "n-1"
    keys = {c["key"] for c in geschrieben["changes"]}
    assert keys == {"FULL_UNIVERSE_MAX_POSITIONS", "SMART_EXIT_MIN_HOLD_DAYS"}


def test_fehlgeschlagene_worm_schreibung_verhindert_erfolg(client, monkeypatch):
    from core import hitl_gate

    _kaputt = AsyncMock(side_effect=RuntimeError("Kette nicht beschreibbar"))

    monkeypatch.setattr(hitl_gate, "log_portfolio_shape_event", _kaputt)
    with pytest.raises(RuntimeError):
        client.post("/api/portfolio-shape", json=_body(positions=20))


def test_fehlende_bestaetigung_wird_abgewiesen(client, worm):
    r = client.post("/api/portfolio-shape", json={"positions": 20, "nonce": "n-2"})
    assert r.status_code == 422
    assert worm == []


def test_nicht_gesetzte_werte_bleiben_unveraendert(client, worm):
    """Nur die Positionszahl senden darf die anderen beiden nicht zuruecksetzen."""
    r = client.post("/api/portfolio-shape", json=_body(positions=15))
    assert r.status_code == 201
    applied = r.json()["applied"]
    assert applied == {"positions": 15, "hold_days": 20.0, "vol_target": 0.015}
    assert {c["key"] for c in worm[0]["changes"]} == {"FULL_UNIVERSE_MAX_POSITIONS"}


def test_die_aenderungen_sind_reiner_text(client, worm):
    """Floats duerfen die SHA-256-Vorlage der Kette nie erreichen."""
    client.post("/api/portfolio-shape", json=_body(positions=20, vol_target=0.02))
    for c in worm[0]["changes"]:
        for feld, wert in c.items():
            assert isinstance(wert, str), f"{feld}={wert!r} ist kein str"
