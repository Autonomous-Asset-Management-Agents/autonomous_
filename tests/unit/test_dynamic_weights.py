from unittest.mock import MagicMock, patch

import allure

from core.round_table.base_agent import VoteResult, VotingAgent


# Create a concrete subclass for testing
class MockAgent(VotingAgent):
    default_weight: float = 0.40
    min_weight: float = 0.15
    max_weight: float = 1.50

    async def vote(self, state):
        return VoteResult(
            agent_name="MockAgent",
            symbol="TEST",
            score=0.8,
            weight=self.weight,
            reasoning="mock",
        )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
def test_agent_uses_default_weight_without_redis():
    """Test that agent falls back to default_weight if no Redis cache exists."""
    agent = MockAgent()
    assert agent.weight == 0.40


@patch("core.round_table.base_agent.RedisClient")
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
def test_agent_fetches_dynamic_weight_from_redis(redis_mock):
    """Test that agent fetches weight from Redis correctly and clamps to guardrails."""
    # Mock Redis sync client
    mock_sync_redis = MagicMock()
    redis_mock.get_sync_redis.return_value = mock_sync_redis

    # 1. Within bounds
    mock_sync_redis.hget.return_value = "0.75"
    agent = MockAgent()
    assert agent.weight == 0.75

    # 2. Clamped to Max (try to set it to 2.0, should clamp to 1.50)
    mock_sync_redis.hget.return_value = "2.0"
    assert agent.weight == 1.50

    # 3. Clamped to Min (try to set to 0.05, should clamp to 0.15)
    mock_sync_redis.hget.return_value = "0.05"
    assert agent.weight == 0.15

    # 4. Invalid data in Redis (falls back to default)
    mock_sync_redis.hget.return_value = "invalid_string"
    assert agent.weight == 0.40
