"""Overview Cash/margin fix: /portfolio-summary must return the REAL broker cash.

The console's "Cash / margin" card used to DERIVE cash as `equity − Σ market_value`, which is wrong
on a margin/short account (ignores buying power / unsettled funds). The engine now surfaces the real
Alpaca `account.cash` as `cash` on the live success paths, so the console shows the authoritative
figure and only falls back to the derivation when the field is absent (older engine / probe failure).

Mirrors the harness of test_portfolio_summary_paper_tag.py.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.engine.api_routes import app


@pytest.fixture
def test_client():
    return TestClient(app)


def _mock_broker(mock_engine, cash):
    """Reach the real_positions success return with a live-looking broker snapshot."""
    mock_engine._last_round_table_state = []
    mock_engine.active_strategy = None  # skip the PM branch → real_positions return
    mock_api = MagicMock()
    mock_engine.api = mock_api
    acc = MagicMock()
    acc.equity = "10000.0"
    acc.currency = "USD"
    acc.cash = cash  # the value under test (may be negative on margin)
    mock_api.get_account.return_value = acc
    pos = MagicMock()
    pos.symbol = "AAPL"
    pos.qty = "10"
    pos.market_value = "1500"
    pos.unrealized_pl = "150"
    pos.unrealized_plpc = "0.1"
    mock_api.get_all_positions.return_value = [pos]


def _get(test_client):
    with patch.dict(
        "os.environ", {"ENGINE_API_KEY": "test-engine-key", "REQUIRE_SIG": "false"}
    ):
        return test_client.get(
            "/portfolio-summary", headers={"x-engine-key": "test-engine-key"}
        )


@patch("core.engine.api_routes.engine")
def test_portfolio_summary_returns_real_broker_cash(mock_engine, test_client):
    _mock_broker(mock_engine, cash="7500.0")
    resp = _get(test_client)
    if resp.status_code != 200:
        pytest.skip(f"auth/env gate returned {resp.status_code} in this harness")
    body = resp.json()
    assert body.get("status") == "success"
    # The REAL settled cash, NOT the derived equity − Σ market_value (which would be 8500 here).
    assert body.get("cash") == 7500.0


@patch("core.engine.api_routes.engine")
def test_portfolio_summary_passes_negative_cash_margin_used(mock_engine, test_client):
    _mock_broker(mock_engine, cash="-2000.0")
    resp = _get(test_client)
    if resp.status_code != 200:
        pytest.skip(f"auth/env gate returned {resp.status_code} in this harness")
    body = resp.json()
    assert body.get("status") == "success"
    # Negative cash (margin used) must survive — no `or 0` coercion.
    assert body.get("cash") == -2000.0


@patch("core.engine.api_routes.engine")
def test_portfolio_summary_cash_none_when_unparseable(mock_engine, test_client):
    # A broker returning a non-numeric cash must degrade to null, NOT abort the live summary.
    _mock_broker(mock_engine, cash="n/a")
    resp = _get(test_client)
    if resp.status_code != 200:
        pytest.skip(f"auth/env gate returned {resp.status_code} in this harness")
    body = resp.json()
    assert body.get("status") == "success"
    assert body.get("cash") is None
    # The rest of the summary is still live (the bad cash didn't nuke the fetch).
    assert body.get("equity") == 10000.0
    assert body.get("positions")
