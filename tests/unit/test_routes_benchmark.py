"""#4074 (ARC-E6 G-4h) — die Benchmark-Kurve liegt in ``core/engine/routes/benchmark.py``.

``GET /benchmark-equity`` liefert die Rendite gegen SPY, einzahlungsbereinigt; die Route liest
nur. Sie zieht mit ihren vier eigenen Helfern um: die Route nach ``routes/benchmark.py``, die
Helfer nach ``routes/benchmark_daten.py``. Die HTTP-Flaeche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass alles wirklich umgezogen ist, ein Patch
auf den neuen Ort der Helfer die Route erreicht und die geteilten Helfer
(``_get_live_cashflows``) weiter ueber ``core.engine.api_routes`` gepatcht werden.

Plan: ``docs/4074-g4h-benchmark-kurve/implementation_plan.md``.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1). G-4i (#4075) zieht die Baseline-Routen und
#: ``/run-benchmark`` nach (Plan §2.5).
BENCHMARK = {
    ("GET", "/benchmark-equity"): "get_benchmark_equity",
    ("GET", "/api/performance-baseline"): "get_performance_baseline",
    ("POST", "/api/performance-baseline"): "set_performance_baseline",
    ("POST", "/run-benchmark"): "run_benchmark",
}
HELFER = (
    "_reconstruct_spy_points",
    "_get_inception_equity",
    "_read_metrics_db_or_fallback",
    "_return_metrics_from_points",
)

_KEY = "test-engine-key-benchmark"


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_route_liegt_im_benchmark_router():
    from core.engine.routes import ROUTER, benchmark

    assert benchmark.router in ROUTER
    assert _routen(benchmark.router) == BENCHMARK


def test_die_app_bedient_die_route_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in BENCHMARK
    }
    assert bedient == dict.fromkeys(BENCHMARK, "core.engine.routes.benchmark")


def test_die_helfer_liegen_in_benchmark_daten():
    from core.engine.routes import benchmark_daten

    assert {n: getattr(benchmark_daten, n).__module__ for n in HELFER} == dict.fromkeys(
        HELFER, "core.engine.routes.benchmark_daten"
    )


def test_api_routes_enthaelt_die_benchmark_namen_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        n
        for n in (*BENCHMARK.values(), *HELFER, "_last_trading_day")
        if hasattr(api_routes, n)
    ]
    assert geblieben == []


def test_run_benchmark_startet_ueber_ar_engine_mit_letztem_handelstag():
    """``/run-benchmark`` liest ``engine`` zur Laufzeit ueber ``ar`` (Zugriffsregel)."""
    from datetime import date

    from fastapi.testclient import TestClient

    from core.engine import api_routes
    from core.engine.routes import benchmark

    engine = MagicMock()
    with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
        api_routes, "engine", engine
    ):
        antwort = TestClient(api_routes.app).post(
            "/run-benchmark",
            json={"start_date": "2026-01-02"},
            headers={"X-Engine-Key": _KEY},
        )

    assert antwort.status_code == 200
    tag = benchmark._last_trading_day()
    assert date.fromisoformat(tag).weekday() < 5
    engine.run_benchmark_in_thread.assert_called_once_with(
        "2026-01-02", tag, 100000.0, "sp500"
    )


def test_baseline_routen_lesen_den_geteilten_helfer_ueber_ar():
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes,
            "_get_performance_baseline",
            AsyncMock(return_value="2026-08-03"),
        ):
            client = TestClient(api_routes.app)
            lesen = client.get(
                "/api/performance-baseline", headers={"X-Engine-Key": _KEY}
            )
            falsch = client.post(
                "/api/performance-baseline",
                json={"baseline_date": "kein-datum"},
                headers={"X-Engine-Key": _KEY},
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)

    assert lesen.json() == {"baseline_date": "2026-08-03"}
    assert falsch.status_code == 422


def _warmer_cache(paper: bool) -> str:
    """Ein Cache ohne ``spy_points`` — die Route heilt ihn ueber ``_reconstruct_spy_points``."""
    return json.dumps(
        {
            "points": [
                {"date": "2026-09-01", "equity": 1000.0},
                {"date": "2026-09-02", "equity": 1010.0},
            ],
            "spy_points": [],
            "initial_capital": 1000.0,
            "paper_trading": paper,
            "baseline_date": None,
            "flows_sig": "none",
        }
    )


def _hole_benchmark(engine, **patches):
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    redis = MagicMock()
    redis.get.return_value = _warmer_cache(
        bool(getattr(api_routes.config, "PAPER_TRADING", True))
    )
    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes.RedisClient, "get_sync_redis", return_value=redis
        ), patch.object(api_routes, "engine", engine), patch.object(
            api_routes, "_get_performance_baseline", AsyncMock(return_value=None)
        ), patch.multiple(
            "core.engine.routes.benchmark_daten", **patches
        ):
            return TestClient(api_routes.app).get(
                "/benchmark-equity", headers={"X-Engine-Key": _KEY}
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)


def test_ein_patch_auf_benchmark_daten_reconstruct_spy_points_erreicht_die_route():
    spy = [
        {"date": "2026-09-01", "equity": 1000.0},
        {"date": "2026-09-02", "equity": 1005.0},
    ]
    gepatcht = MagicMock(return_value=(spy, 400.0))
    antwort = _hole_benchmark(MagicMock(api=None), _reconstruct_spy_points=gepatcht)

    assert antwort.status_code == 200
    gepatcht.assert_called_once()
    assert antwort.json()["spy_points"] == spy


def test_ein_patch_auf_api_routes_get_live_cashflows_erreicht_die_route():
    """``_get_live_cashflows`` bleibt in ``api_routes`` (auch Portfolio) — der Patch dort muss
    in der umgezogenen Route ankommen (Plan §5, vierter Fall)."""
    from core.engine import api_routes

    engine = MagicMock()
    engine.api.get_account.return_value = MagicMock(equity="1010.0")
    gepatcht = MagicMock(return_value={})
    with patch.object(api_routes, "_get_live_cashflows", gepatcht):
        antwort = _hole_benchmark(
            engine, _reconstruct_spy_points=MagicMock(return_value=([], 1.0))
        )

    assert antwort.status_code == 200
    gepatcht.assert_called_once()
