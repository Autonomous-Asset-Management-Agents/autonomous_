"""#4070 (ARC-E6 G-4d) — die lesenden Portfolio-Routen liegen in ``core/engine/routes/portfolio.py``.

Summary, Intraday, Aktivitaeten, Trades, Kurse und Orders lesen nur: Sie zeigen der Konsole
den Stand des Kontos und platzieren nichts. Die HTTP-Flaeche bleibt gleich (das beweist
``test_api_flaeche_schnappschuss.py``); hier steht, dass die Routen wirklich umgezogen sind
und Patches auf ``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4070-g4d-portfolio-router/implementation_plan.md``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

#: Route → Name des Handlers (Plan §1).
PORTFOLIO = {
    ("GET", "/top-picks"): "get_top_picks",
    ("GET", "/recent-trades"): "get_recent_trades",
    ("GET", "/activities"): "get_activities",
    ("GET", "/open-orders"): "get_open_orders",
    ("GET", "/recent-news"): "get_recent_news",
    ("GET", "/stock-history"): "get_stock_history",
    ("GET", "/portfolio-summary"): "get_portfolio_summary",
    ("GET", "/portfolio-intraday"): "get_portfolio_intraday",
}
HELFER = (
    "_enum_value_lower",
    "_order_to_trade",
    "_order_to_open_order",
    "_stock_history_days",
    "_json_safe",
    "_safe_float",
    "_pct_or_none",
    "_INTRADAY_BAR_SECONDS",
    "_INTRADAY_RANGES",
    "_SORT_EPOCH",
)

_KEY = "test-engine-key-portfolio"


def _routen(router) -> dict:
    return {
        (methode, r.path): r.endpoint.__name__
        for r in router.routes
        for methode in getattr(r, "methods", ())
    }


def test_die_acht_routen_liegen_im_portfolio_router():
    from core.engine.routes import ROUTER, portfolio

    assert portfolio.router in ROUTER
    assert _routen(portfolio.router) == PORTFOLIO


def test_die_app_bedient_die_portfolio_routen_aus_dem_neuen_modul():
    from core.engine import api_routes

    bedient = {
        (methode, r.path): r.endpoint.__module__
        for r in api_routes.app.routes
        for methode in getattr(r, "methods", ())
        if (methode, getattr(r, "path", None)) in PORTFOLIO
    }
    assert bedient == dict.fromkeys(PORTFOLIO, "core.engine.routes.portfolio")


def test_api_routes_enthaelt_die_portfolio_routen_nicht_mehr():
    from core.engine import api_routes

    geblieben = [
        name for name in (*PORTFOLIO.values(), *HELFER) if hasattr(api_routes, name)
    ]
    assert geblieben == []


def test_ein_patch_auf_api_routes_get_live_cashflows_erreicht_die_intraday_kurve():
    """``_get_live_cashflows`` bleibt in ``api_routes`` (auch Benchmark) — der Patch dort muss
    im umgezogenen Handler ankommen: der Zufluss erscheint als Marker am Kurvenpunkt."""
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    hist = MagicMock()
    hist.timestamp = [1_780_000_000, 1_780_000_300]
    hist.equity = [100.0, 150.0]
    hist.base_value = 0.0
    client = MagicMock()
    client.get_portfolio_history.return_value = hist
    tag = datetime.fromtimestamp(hist.timestamp[0], tz=timezone.utc).date().isoformat()
    gepatcht = MagicMock(return_value={tag: 50.0})
    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes, "_resolve_orders_client", AsyncMock(return_value=client)
        ), patch.object(api_routes, "_get_live_cashflows", gepatcht), patch.object(
            api_routes, "_get_performance_baseline", AsyncMock(return_value=None)
        ):
            antwort = TestClient(api_routes.app).get(
                "/portfolio-intraday?range=1D", headers={"X-Engine-Key": _KEY}
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)

    assert antwort.status_code == 200
    gepatcht.assert_called_once()
    assert any("cashflow" in p for p in antwort.json()["points"])


def test_ein_patch_auf_api_routes_engine_erreicht_die_portfolio_summary():
    from fastapi.testclient import TestClient

    from core.engine import api_routes

    konto = MagicMock(equity="1234.5", cash="100", currency="USD")
    gepatcht = MagicMock(active_strategy=None, _last_round_table_state=[])
    gepatcht.api.get_account.return_value = konto
    gepatcht.api.get_all_positions.return_value = []
    api_routes.app.dependency_overrides[api_routes.verify_user_id_sig] = lambda: None
    try:
        with patch.dict(os.environ, {"ENGINE_API_KEY": _KEY}), patch.object(
            api_routes, "engine", gepatcht
        ):
            antwort = TestClient(api_routes.app).get(
                "/portfolio-summary", headers={"X-Engine-Key": _KEY}
            )
    finally:
        api_routes.app.dependency_overrides.pop(api_routes.verify_user_id_sig, None)

    assert antwort.status_code == 200
    assert antwort.json()["equity"] == 1234.5
    gepatcht.api.get_account.assert_called_once()
