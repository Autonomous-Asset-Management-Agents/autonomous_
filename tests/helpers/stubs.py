import datetime
import time
from unittest import mock


class FixedClock:
    def __init__(self, fixed_datetime: datetime.datetime):
        self.fixed_datetime = fixed_datetime
        self.fixed_time = fixed_datetime.timestamp()
        self.patches = []

    def __enter__(self):
        class MockDatetime(datetime.datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return cls.fromtimestamp(self.fixed_time)
                return cls.fromtimestamp(self.fixed_time, tz=tz)

        self.patches = [
            mock.patch("core.engine.order_executor.datetime", MockDatetime),
            mock.patch("core.round_table.runner.datetime", MockDatetime),
            mock.patch("core.round_table.agents.datetime", MockDatetime),
            # In general, it's safer to mock where they are imported. We can also mock time.time.
            mock.patch("time.time", return_value=self.fixed_time),
        ]

        for p in self.patches:
            p.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for p in self.patches:
            p.stop()


class DeterministicLLMStub:
    """A deterministic test double for the LLM that always outputs a predefined response."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.call_count = 0
        self.prompts = []
        self.generate_content_async = mock.AsyncMock(side_effect=self.generate_content)

    def generate_content(self, prompt, max_output_tokens=None, **kwargs):
        self.call_count += 1
        self.prompts.append(prompt)
        for key, response in self.responses.items():
            if key in prompt:
                return type("MockResponse", (), {"text": response})()
        return type("MockResponse", (), {"text": "DETERMINISTIC_MOCK_RESPONSE"})()


class DeterministicModelContext:
    def __init__(self, responses=None):
        self.stub = DeterministicLLMStub(responses)
        self.patcher = mock.patch(
            "core.llm.provider.get_llm_provider", return_value=self.stub
        )
        self.patcher2 = mock.patch(
            "core.gemini_client.get_gemini_instance", return_value=self.stub
        )

    def __enter__(self):
        self.patcher.start()
        self.patcher2.start()
        return self.stub

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.patcher.stop()
        self.patcher2.stop()
