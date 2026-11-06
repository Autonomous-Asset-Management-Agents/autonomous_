"""#4917 (STW-9b) — ``POST/GET /api/strategy-sim/runs`` in ``core/engine/routes/strategie_sim.py``.

Plan: ``docs/4917-*/implementation_plan.md`` §2 Punkt 1, §5 Schritt 1. Der Subprozess ist echt
(Test-Modul statt ``core.sim.stimmen_sim``), die Strategien liegen echt in ``strategien.json``
(#4629). Fehlerantworten im Format von #4890 (``detail`` / ``code`` / ``params``).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import textwrap
import time
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests.unit.test_strategie_sim_vergleich import _TEST_SIM

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

HEUTE = date(2026, 10, 28)
RUNS = "/api/strategy-sim/runs"


def _body(**kw):
    body = {
        "beginn": "2026-01-02",
        "ende": "2026-06-30",
        "strategien": ["A", "B"],
        "startkapital": 1000,
    }
    body.update(kw)
    return body


@pytest.fixture
def sim(tmp_path, monkeypatch):
    """Flag an, zwei Strategien, Korpus mit SPY, Test-Modul, Auth aus, heute = 28.10.2026."""
    import pandas as pd

    import config
    from core import strategie_profile as sp
    from core.engine import api_routes
    from core.engine.routes import strategie_sim
    from core.sim import vergleich as v
    from core.sim.stimmen_rechner import stand

    udd = tmp_path / "udd"
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(udd))
    monkeypatch.setenv("APCA_API_KEY_ID", "k")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "s")
    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", True, raising=False)
    sp.speichere_strategien({"A": {"STOP_LOSS_PCT": "5.0"}, "B": {}})

    korpus = udd / v.KORPUS
    (korpus / "stimmen").mkdir(parents=True)
    (korpus / "korpus.json").write_text(
        json.dumps(
            {"zeitraum": {"von": "2023-10-28", "bis": "2026-10-27"}, "grenzen": []}
        ),
        encoding="utf-8",
    )
    tage = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])
    pd.DataFrame({"close": [50.0, 40.0, 55.0]}, index=tage).to_parquet(
        korpus / "SPY.parquet"
    )
    for name in ("decisions", "votes", "kurse"):
        (korpus / "stimmen" / f"{name}.parquet").write_bytes(b"")
    (korpus / "stimmen" / "stand.json").write_text(
        json.dumps(stand()), encoding="utf-8"
    )

    modul = tmp_path / "modul"
    modul.mkdir()
    (modul / "test_sim_4917.py").write_text(textwrap.dedent(_TEST_SIM), "utf-8")
    monkeypatch.setenv("PYTHONPATH", str(modul))
    monkeypatch.setattr(v, "_SIM_MODUL", "test_sim_4917")
    monkeypatch.setattr(strategie_sim, "_heute", lambda: HEUTE)
    v._LAEUFE.clear()

    api_routes.app.dependency_overrides[api_routes.require_engine_key] = lambda: None
    from fastapi.testclient import TestClient

    yield SimpleNamespace(
        client=TestClient(api_routes.app), udd=udd, korpus=korpus, v=v
    )
    api_routes.app.dependency_overrides.pop(api_routes.require_engine_key, None)
    v._LAEUFE.clear()


def _warte_fertig(client, run_id, sekunden=60):
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        r = client.get(f"{RUNS}/{run_id}")
        if r.json()["status"] != "laeuft":
            return r
        time.sleep(0.1)
    raise AssertionError("Lauf wurde nicht fertig")


def _laeufe(udd, v):
    ablage = udd / v.ABLAGE
    return sorted(p.name for p in ablage.iterdir()) if ablage.exists() else []


def test_404_bei_flag_aus(sim, monkeypatch):
    import config

    monkeypatch.setattr(config, "STRATEGY_PROFILES_ENABLED", False, raising=False)
    r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    assert sim.client.get(f"{RUNS}/0123456789abcdef").status_code == 404
    assert _laeufe(sim.udd, sim.v) == []


def test_422_unbekannte_strategie(sim):
    r = sim.client.post(RUNS, json=_body(strategien=["A", "X"]))
    assert r.status_code == 422
    assert r.json()["code"] == "simulation_input_invalid"
    r = sim.client.post(RUNS, json={"beginn": "kein Datum"})
    assert r.status_code == 422
    assert r.json()["code"] == "request_invalid"
    assert _laeufe(sim.udd, sim.v) == []


def test_422_zeitraum_vor_drei_jahren(sim):
    r = sim.client.post(RUNS, json=_body(beginn="2023-10-27"))
    assert r.status_code == 422
    assert r.json()["code"] == "simulation_input_invalid"
    assert _laeufe(sim.udd, sim.v) == []


def test_403_editions_grenze(sim, monkeypatch):
    import core.entitlement as ent

    junior = dataclasses.replace(ent.resolve_entitlement(), backtest_months=12)
    monkeypatch.setattr(ent, "resolve_entitlement", lambda: junior)
    r = sim.client.post(RUNS, json=_body(beginn="2025-04-28", ende="2026-10-28"))
    assert r.status_code == 403
    assert r.json()["code"] == "simulation_edition_limit"
    assert r.json()["params"] == {"months": 12}
    assert sim.v._LAEUFE == {}
    assert _laeufe(sim.udd, sim.v) == []


def test_409_ohne_korpus(sim, monkeypatch):
    # Korpus deckt den Zeitraum nicht ab.
    (sim.korpus / "korpus.json").write_text(
        json.dumps({"zeitraum": {"von": "2026-03-01", "bis": "2026-10-27"}}),
        encoding="utf-8",
    )
    r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 409
    assert r.json()["code"] == "simulation_corpus_missing"
    assert r.json()["params"] == {"reason": "kein_korpus"}

    # Kein Korpus und keine Keys: der Zustand "no-keys" des UX-Konzepts.
    (sim.korpus / "korpus.json").unlink()
    for name in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name)
    r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 409
    assert r.json()["code"] == "simulation_keys_missing"
    assert r.json()["params"] == {"reason": "keine_keys"}
    assert sim.v._LAEUFE == {}


def test_202_startet_subprozess(sim):
    r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    assert r.json() == {"run_id": run_id, "status": "laeuft"}
    assert len(run_id) == 16
    assert sim.v._LAEUFE[run_id].pid != os.getpid()
    assert _warte_fertig(sim.client, run_id).json()["status"] == "fertig"


def test_gleiche_eingabe_startet_keinen_zweiten_prozess(sim):
    run_id = sim.client.post(RUNS, json=_body()).json()["run_id"]
    erster = sim.v._LAEUFE[run_id]
    _warte_fertig(sim.client, run_id)

    r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 202
    assert r.json() == {"run_id": run_id, "status": "fertig"}
    assert sim.v._LAEUFE[run_id] is erster


def test_get_status_laeuft_und_fertig(sim):
    run_id = sim.client.post(RUNS, json=_body()).json()["run_id"]
    proc = sim.v._LAEUFE[run_id]
    sim.v._LAEUFE[run_id] = MagicMock(**{"poll.return_value": None})
    assert sim.client.get(f"{RUNS}/{run_id}").json() == {
        "run_id": run_id,
        "status": "laeuft",
    }
    sim.v._LAEUFE[run_id] = proc
    antwort = _warte_fertig(sim.client, run_id).json()
    assert "lauf_id" not in antwort
    assert antwort["run_id"] == run_id
    e = antwort["ergebnis"]
    assert [s["name"] for s in e["strategien"]] == ["A", "B"]
    assert set(e["strategien"][0]) >= {"twr_pct", "max_drawdown_pct", "spy_twr_pct"}
    assert e["zeitraum"] == {"beginn": "2026-01-02", "ende": "2026-06-30"}


def test_get_404_unbekannte_run_id(sim):
    assert sim.client.get(f"{RUNS}/0123456789abcdef").status_code == 404
    assert sim.client.get(f"{RUNS}/nicht-hex").status_code == 404


def _tracer(monkeypatch, span=None, fehler=None):
    from core.engine import api_routes as ar

    tracer = MagicMock()
    if fehler is not None:
        tracer.start_as_current_span.side_effect = fehler
    else:
        tracer.start_as_current_span.return_value.__enter__.return_value = span
    monkeypatch.setattr(ar, "_tracer", tracer)
    return tracer


def test_kein_bump_usage_und_keine_kennzahl_im_span(sim, monkeypatch):
    from core.engine import api_routes as ar

    bump = MagicMock()
    monkeypatch.setattr(ar, "bump_usage", bump)
    span = MagicMock()
    tracer = _tracer(monkeypatch, span=span)

    run_id = sim.client.post(RUNS, json=_body()).json()["run_id"]
    _warte_fertig(sim.client, run_id)

    bump.assert_not_called()
    tracer.start_as_current_span.assert_called_once_with("strategy_sim.start")
    attribute = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attribute == {
        "run_id": run_id,
        "strategy_count": 2,
        "days": 179,
        "reused": False,
        "delivery_state": False,
    }


def test_span_fehler_warnt_und_bricht_nicht(sim, monkeypatch, caplog):
    _tracer(monkeypatch, fehler=RuntimeError("otel down"))
    with caplog.at_level(logging.WARNING):
        r = sim.client.post(RUNS, json=_body())
    assert r.status_code == 202
    assert r.json()["run_id"] in sim.v._LAEUFE
    treffer = [
        rec
        for rec in caplog.records
        if "strategy_sim.start span failed" in rec.getMessage()
    ]
    assert treffer and all(rec.levelno == logging.WARNING for rec in treffer)
    _warte_fertig(sim.client, r.json()["run_id"])


# ── #5126 (STW-9d): Lieferzustand als Vergleichslinie (T20, E2) ─────────────────
# Plan: ``docs/5126-*/implementation_plan.md`` §6 Schritt 2.


def test_202_drei_strategien_plus_lieferzustand(sim):
    from core import strategie_profile as sp

    sp.speichere_strategien({"A": {"STOP_LOSS_PCT": "5.0"}, "B": {}, "C": {}})
    namen = ["A", "B", "C", sim.v.LIEFERZUSTAND]
    r = sim.client.post(RUNS, json=_body(strategien=namen))
    assert r.status_code == 202, r.text
    e = _warte_fertig(sim.client, r.json()["run_id"]).json()["ergebnis"]
    assert [s["name"] for s in e["strategien"]] == namen


def test_422_vier_strategien_ohne_lieferzustand(sim):
    from core import strategie_profile as sp

    # Direkt in die Ablage: ``pruefe_obergrenze`` liesse die vierte nicht anlegen.
    sp.speichere_strategien({n: {} for n in ("A", "B", "C", "D")})
    r = sim.client.post(RUNS, json=_body(strategien=["A", "B", "C", "D"]))
    assert r.status_code == 422
    assert r.json()["code"] == "simulation_input_invalid"
    r = sim.client.post(
        RUNS, json=_body(strategien=["A", "B", "C", "D", sim.v.LIEFERZUSTAND])
    )
    assert r.status_code == 422
    assert r.json()["code"] == "simulation_input_invalid"
    r = sim.client.post(RUNS, json=_body(strategien=[sim.v.LIEFERZUSTAND]))
    assert r.status_code == 422
    assert r.json()["code"] == "simulation_input_invalid"
    assert _laeufe(sim.udd, sim.v) == []


def test_route_loest_lieferzustand_nicht_ueber_lade_strategien_auf(sim):
    v = sim.v
    r = sim.client.post(RUNS, json=_body(strategien=["A", v.LIEFERZUSTAND]))
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    _warte_fertig(sim.client, run_id)
    eingabe = json.loads((sim.udd / v.ABLAGE / run_id / v.EINGABE).read_bytes())
    werte = {s["name"]: s["werte"] for s in eingabe["strategien"]}
    assert werte[v.LIEFERZUSTAND] == v.werte_lieferzustand()
    assert werte["A"] == {"STOP_LOSS_PCT": "5.0"}


def test_span_zaehlt_lieferzustand_getrennt(sim, monkeypatch):
    span = MagicMock()
    _tracer(monkeypatch, span=span)
    r = sim.client.post(RUNS, json=_body(strategien=["A", "B", sim.v.LIEFERZUSTAND]))
    _warte_fertig(sim.client, r.json()["run_id"])
    attribute = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attribute["strategy_count"] == 2
    assert attribute["delivery_state"] is True

    span.reset_mock()
    r = sim.client.post(RUNS, json=_body())
    _warte_fertig(sim.client, r.json()["run_id"])
    attribute = {c.args[0]: c.args[1] for c in span.set_attribute.call_args_list}
    assert attribute["strategy_count"] == 2
    assert attribute["delivery_state"] is False
