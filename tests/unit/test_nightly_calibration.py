import json
from unittest.mock import MagicMock, patch

import allure

from core.learning.engine import AILearningEngine
from core.utils import BackendSignals


class MockBackendSignals(BackendSignals):
    def __init__(self):
        super().__init__()


@patch("core.redis_client.RedisClient")
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
def test_update_dynamic_agent_weights(redis_mock):
    mock_sync_redis = MagicMock()
    redis_mock.get_sync_redis.return_value = mock_sync_redis

    # Mock existing agent trust scores in Redis
    mock_sync_redis.get.side_effect = lambda key: {
        "agent_trust_scores": json.dumps(
            {
                "LSTMSignalAgent": 3.0,  # Strong positive trust
                "MomentumAgent": -5.0,  # Strong negative trust
                "PatternRecognitionAgent": 1.0,  # Slight positive trust
            }
        ),
        # agent_weights_v2 is actually loaded individually by the properties in the real agents,
        # so we don't mock it via redis.get() here, we mock the agent properties directly below.
    }.get(key, None)

    engine = AILearningEngine(MockBackendSignals())

    # We need an agent list with their bounds for the engine, we can mock import ALL_AGENTS
    with patch(
        "core.round_table.agents.ALL_AGENTS",
        [
            MagicMock(
                __class__=type("LSTMSignalAgent", (), {"__name__": "LSTMSignalAgent"}),
                min_weight=15.0,
                max_weight=40.0,
                weight=25.0,
            ),
            MagicMock(
                __class__=type("MomentumAgent", (), {"__name__": "MomentumAgent"}),
                min_weight=0.0,
                max_weight=1.50,
                weight=0.45,
            ),
            MagicMock(
                __class__=type(
                    "PatternRecognitionAgent",
                    (),
                    {"__name__": "PatternRecognitionAgent"},
                ),
                min_weight=0.0,
                max_weight=1.0,
                weight=0.30,
            ),
        ],
    ):
        engine.update_dynamic_agent_weights()

    # Engine should compute:
    # LSTM +3.0 -> 25.0 + 3.0 = 28.0 (<= 40.0) -> OK
    # Momentum -5.0 -> 0.45 - 5.0 = -4.55 -> clamped to 0.0 -> OK
    # Pattern +1.0 -> 0.30 + 1.0 = 1.30 -> clamped to 1.0 -> OK

    # Verify that the new agent weights are persisted correctly via hset
    new_weights = {}
    for call in mock_sync_redis.hset.call_args_list:
        if call.args[0] == "agent_weights_v2":
            new_weights[call.args[1]] = float(call.args[2])

    assert "LSTMSignalAgent" in new_weights
    assert new_weights["LSTMSignalAgent"] == 28.0
    assert new_weights["MomentumAgent"] == 0.0
    assert new_weights["PatternRecognitionAgent"] == 1.0

    # Verify trust scores were reset (using .set with a JSON blob)
    trust_scores_reset_call = None
    for call in mock_sync_redis.set.call_args_list:
        if call.args[0] == "agent_trust_scores":
            trust_scores_reset_call = call
            break
    assert trust_scores_reset_call is not None
    assert json.loads(trust_scores_reset_call.args[1]) == {}
