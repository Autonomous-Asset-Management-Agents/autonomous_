"""TDD for GET /portfolio-intraday (#2124).

Read-only endpoint that returns the account's INTRADAY equity curve (Alpaca
portfolio/history at 5Min for 1D, 1H for 1W) so the Overview chart shows a real
intraday movement instead of a single straight segment (the daily curve from
/benchmark-equity collapses each day to one point). Fail-soft to an empty curve
— never a fabricated point.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key, verify_user_id_sig


@pytest.fixture
def client():
    app = api_routes_mod.app
    app.dependency_overrides[require_engine_key] = lambda: None
    app.dependency_overrides[verify_user_id_sig] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def _hist(n: int, step_s: int, base_ts: int = 1_700_000_000, base_eq: float = 10_000.0):
    """A fake Alpaca PortfolioHistory: n points, `step_s` apart, gently varying equity."""
    ts = [base_ts + i * step_s for i in range(n)]
    eq = [base_eq + (i % 7) * 12.5 for i in range(n)]  # real up/down movement, not flat
    return SimpleNamespace(timestamp=ts, equity=eq)


@patch("core.engine.api_routes.engine")
def test_intraday_1d_returns_many_5min_points(mock_engine, client):
    mock_engine.api.get_portfolio_history.return_value = _hist(
        78, 300
    )  # a trading day @5Min
    resp = client.get("/portfolio-intraday?range=1D")
    assert resp.status_code == 200
    pts = resp.json()["points"]
    assert len(pts) == 78, "1D must yield the full intraday series, not a 2-point line"
    # ISO datetime (has a time component), not a bare date -> spreads across the day
    assert "T" in pts[0]["date"] and isinstance(pts[0]["equity"], (int, float))
    # requested at 5Min / 1D
    req = mock_engine.api.get_portfolio_history.call_args.args[0]
    assert req.timeframe == "5Min" and req.period == "1D"


@patch("core.engine.api_routes.engine")
def test_intraday_1w_uses_hourly(mock_engine, client):
    mock_engine.api.get_portfolio_history.return_value = _hist(35, 3600)
    resp = client.get("/portfolio-intraday?range=1W")
    assert resp.status_code == 200
    assert len(resp.json()["points"]) == 35
    req = mock_engine.api.get_portfolio_history.call_args.args[0]
    assert req.timeframe == "1H" and req.period == "1W"


@patch("core.engine.api_routes.engine")
def test_intraday_failsoft_on_empty(mock_engine, client):
    mock_engine.api.get_portfolio_history.return_value = SimpleNamespace(
        timestamp=[], equity=[]
    )
    resp = client.get("/portfolio-intraday?range=1D")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


@patch("core.engine.api_routes.engine")
def test_intraday_failsoft_on_exception(mock_engine, client):
    mock_engine.api.get_portfolio_history.side_effect = RuntimeError("alpaca down")
    resp = client.get("/portfolio-intraday?range=1D")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


@patch("core.engine.api_routes.engine")
def test_intraday_failsoft_on_none_hist(mock_engine, client):
    # FINDING-01 (Archon #2124): Alpaca returns None -> explicit guard, empty curve.
    mock_engine.api.get_portfolio_history.return_value = None
    resp = client.get("/portfolio-intraday?range=1D")
    assert resp.status_code == 200
    assert resp.json()["points"] == []


@patch("core.engine.api_routes.engine", new=None)
def test_intraday_engine_starting(client):
    resp = client.get("/portfolio-intraday?range=1D")
    assert resp.status_code == 200
    assert resp.json()["points"] == []
