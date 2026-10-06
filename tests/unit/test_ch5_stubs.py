import datetime
import time

import pytest

from tests.helpers.stubs import DeterministicModelContext, FixedClock


@pytest.mark.mutates_global_state
@pytest.mark.vc0
def test_fixed_clock():
    fixed_time = datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=datetime.timezone.utc)
    with FixedClock(fixed_time):
        from core.round_table.runner import datetime as runner_datetime

        assert runner_datetime.now(datetime.timezone.utc) == fixed_time
        assert time.time() == fixed_time.timestamp()


@pytest.mark.asyncio
@pytest.mark.mutates_global_state
@pytest.mark.vc0
async def test_deterministic_model():
    with DeterministicModelContext({"buy": "I suggest a STRONG BUY."}) as stub:
        from core.gemini_client import get_gemini_instance
        from core.llm.provider import get_llm_provider

        provider = get_llm_provider()
        res1 = provider.generate_content("should I buy AAPL?")
        assert res1.text == "I suggest a STRONG BUY."

        provider2 = get_gemini_instance()
        res2 = await provider2.generate_content_async("should I sell?")
        assert res2.text == "DETERMINISTIC_MOCK_RESPONSE"
