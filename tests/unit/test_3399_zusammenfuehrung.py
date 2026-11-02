import pytest

pytestmark = [pytest.mark.vc0]

from unittest.mock import AsyncMock, patch

import pytest

from core.orchestration.graph import SymbolEvalState, _run_strategy_node


@pytest.mark.asyncio
async def test_exception_im_round_table_loest_abstain_aus_nicht_legacy_fallback():
    # Wir tun so, als wuerde _run_round_table eine Exception werfen
    state: SymbolEvalState = {
        "symbol": "AAPL",
        "ohlc": None,  # mock
        "current_time": "2026-09-20",
    }

    with patch("core.orchestration.graph._ROUND_TABLE_AVAILABLE", True), patch(
        "core.orchestration.graph._run_round_table", new_callable=AsyncMock
    ) as mock_rt:

        mock_rt.side_effect = ValueError("Boom")

        # Test the node
        res = await _run_strategy_node(state)

        # Verify abstain signal
        assert res.get("signal") is not None
        assert res["signal"].action == "HOLD"
        assert res["signal"].decision_context is not None
        assert (
            res["signal"].decision_context.reasoning_summary
            == "abstain:council_unavailable"
        )
