# tests/unit/test_start_live_offload.py
# PR-4 INC-2a — the POST /start-live route is `async def` but calls the SYNCHRONOUS
# engine.start_live_strategy(), which does blocking network I/O (send_slack_alert,
# api.get_account, optional S&P-500 scrape, thread teardown). Run inline on the main
# uvicorn event loop it starves every other handler — including /health — for the full
# duration (~36s observed), which can co-trigger the desktop's health-probe restart storm.
# The route MUST offload it via asyncio.to_thread so the loop stays responsive.

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock, patch


async def test_start_live_route_offloads_start_live_strategy_via_to_thread():
    """`/start-live` must dispatch the blocking `engine.start_live_strategy()` through
    `asyncio.to_thread`, never call it directly on the event loop. A regression that
    unwraps it fails this test."""
    from core.engine import api_routes

    fake_engine = MagicMock()
    # Return False → the route returns its early error dict, so the test never touches the
    # iron-dome policy code below the call (no extra wiring needed).
    fake_engine.start_live_strategy.return_value = False

    real_to_thread = asyncio.to_thread
    dispatched = []

    async def tracking_to_thread(fn, *args, **kwargs):
        dispatched.append(fn)
        return await real_to_thread(fn, *args, **kwargs)

    with patch.object(api_routes, "engine", fake_engine), patch(
        "asyncio.to_thread", tracking_to_thread
    ):
        result = await api_routes.start_live(_=None)

    assert (
        fake_engine.start_live_strategy in dispatched
    ), "start_live_strategy was not offloaded via asyncio.to_thread — it blocks the loop (/health)"
    fake_engine.start_live_strategy.assert_called_once()
    assert (
        result["status"] == "error"
    )  # False → early error dict (start_live_strategy failed)


async def test_start_live_strategy_runs_off_the_event_loop_thread():
    """Stronger evidence: the blocking body must execute on a WORKER thread, not the
    event-loop thread — proving /health can be serviced concurrently while it runs."""
    from core.engine import api_routes

    loop_thread_id = threading.get_ident()
    ran_on = {}

    def _record_thread():
        ran_on["id"] = threading.get_ident()
        return False  # early error return → no iron-dome wiring required

    fake_engine = MagicMock()
    fake_engine.start_live_strategy.side_effect = _record_thread

    with patch.object(api_routes, "engine", fake_engine):
        await api_routes.start_live(_=None)

    assert "id" in ran_on, "start_live_strategy was never called"
    assert (
        ran_on["id"] != loop_thread_id
    ), "start_live_strategy ran ON the event-loop thread — it will starve /health"
