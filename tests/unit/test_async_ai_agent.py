# tests/unit/test_async_ai_agent.py
import asyncio
import time

import allure
import pytest


@pytest.mark.anyio
async def test_async_ai_agent_threadpool_isolation():
    import threading

    from core.round_table.base_agent import AsyncAIAgent, VoteResult

    class ThreadCheckAgent(AsyncAIAgent):
        default_weight = 1.0

        def _run_inference(self, state):
            # Must NOT be in the main thread
            is_main_thread = threading.current_thread() is threading.main_thread()
            return VoteResult(
                "ThreadCheck",
                state.get("symbol", "AAPL"),
                0.9,
                1.0,
                f"is_main_thread={is_main_thread}",
                not is_main_thread,
            )

    agent = ThreadCheckAgent()
    state = {"symbol": "AAPL"}

    result = await agent.vote(state)
    assert (
        result.reasoning == "is_main_thread=False"
    ), "Inference must run in a worker thread, not the main thread!"
