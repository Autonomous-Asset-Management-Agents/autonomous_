from core.round_table.base_agent import VotingAgent, VoteResult
from unittest.mock import patch, MagicMock


# Create a concrete subclass for testing
class MockAgent(VotingAgent):
    default_weight: float = 25.0
    min_weight: float = 15.0
    max_weight: float = 35.0

    async def vote(self, state):
        return VoteResult(
            agent_name="MockAgent",
            symbol="TEST",
            score=0.8,
            weight=self.weight,
            reasoning="mock",
        )


def test_agent_uses_default_weight_without_redis():
    """Test that agent falls back to default_weight if no Redis cache exists."""
    agent = MockAgent()
    assert agent.weight == 25.0


@patch("core.round_table.base_agent.RedisClient")
def test_agent_fetches_dynamic_weight_from_redis(redis_mock):
    """Test that agent fetches weight from Redis correctly and clamps to guardrails."""
    # Mock Redis sync client
    mock_sync_redis = MagicMock()
    redis_mock.get_sync_redis.return_value = mock_sync_redis

    # 1. Within bounds
    mock_sync_redis.hget.return_value = "30.5"
    agent = MockAgent()
    assert agent.weight == 30.5

    # 2. Clamped to Max (try to set it to 50.0, should clamp to 35.0)
    mock_sync_redis.hget.return_value = "50.0"
    assert agent.weight == 35.0

    # 3. Clamped to Min (try to set to 5.0, should clamp to 15.0)
    mock_sync_redis.hget.return_value = "5.0"
    assert agent.weight == 15.0

    # 4. Invalid data in Redis (falls back to default)
    mock_sync_redis.hget.return_value = "invalid_string"
    assert agent.weight == 25.0
