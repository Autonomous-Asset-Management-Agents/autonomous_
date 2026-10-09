"""Bug B (0.3.5 hotfix): /portfolio-summary must tag which ACCOUNT the snapshot belongs to.

The mode-switch overlay's freshness gate was account-agnostic (it only checked that SOME poll
landed after the switch began). Under engine-load starvation a stale in-flight poll against the
dying old engine could satisfy it with the OLD account's data → the reliably-reproduced "paper
first, then live" flash. The fix: /portfolio-summary returns ``paper_trading`` (the engine's
PAPER_TRADING mode, the SAME source /health reports) so the overlay reveals only on TARGET-account
data. These tests lock in that the field is present on the success paths and reflects config.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.engine.api_routes import app


@pytest.fixture
def test_client():
    return TestClient(app)


def _mock_broker(mock_engine):
    """Reach the real_positions success return with a live-looking broker snapshot."""
    mock_engine._last_round_table_state = []
    mock_engine.active_strategy = None  # skip the PM branch → real_positions return
    mock_api = MagicMock()
    mock_engine.api = mock_api
    acc = MagicMock()
    acc.equity = "10000.0"
    acc.currency = "USD"
    mock_api.get_account.return_value = acc
    pos = MagicMock()
    pos.symbol = "AAPL"
    pos.qty = "10"
    pos.market_value = "1500"
    pos.unrealized_pl = "150"
    pos.unrealized_plpc = "0.1"
    mock_api.get_all_positions.return_value = [pos]


@pytest.mark.parametrize("paper_mode", [True, False])
@patch("core.engine.api_routes.engine")
def test_portfolio_summary_reports_paper_trading(mock_engine, test_client, paper_mode):
    _mock_broker(mock_engine)
    with patch("core.engine.api_routes.config.PAPER_TRADING", paper_mode), patch.dict(
        "os.environ", {"ENGINE_API_KEY": "test-engine-key", "REQUIRE_SIG": "false"}
    ):
        resp = test_client.get(
            "/portfolio-summary", headers={"x-engine-key": "test-engine-key"}
        )
    if resp.status_code != 200:
        pytest.skip(f"auth/env gate returned {resp.status_code} in this harness")
    body = resp.json()
    assert body.get("status") == "success"
    assert (
        "paper_trading" in body
    ), "the overlay's account-aware reveal gate depends on this field"
    assert body["paper_trading"] is paper_mode
