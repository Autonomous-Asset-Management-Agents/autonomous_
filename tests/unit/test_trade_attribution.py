import json
from unittest.mock import MagicMock, patch

import allure

from core.trade_intelligence import TradeIntelligence


@patch("core.trade_intelligence.RedisClient")
@allure.feature("VC-6 Reporting & Client Servicing")
@allure.story("Reporting & Auditing")
def test_trade_attribution_profitable(redis_mock):
    # Mock Redis responses
    mock_sync_redis = MagicMock()
    redis_mock.get_sync_redis.return_value = mock_sync_redis

    # Start with empty trust scores
    mock_sync_redis.get.return_value = None

    ti = TradeIntelligence("dummy_file.json")

    # Fake Entry with RoundTable Votes
    ti.record_entry(
        symbol="AAPL",
        entry_price=100.0,
        qty=1.0,
        confidence=0.8,
        round_table_scores={
            "LSTMSignalAgent": 0.9,  # Voted BUY strongly
            "MomentumAgent": 0.1,  # Voted SELL strongly
            "RegimeDetectionAgent": 0.5,  # Neutral
        },
    )

    # Fake Exit with Profit
    ti.record_exit(symbol="AAPL", exit_price=110.0, exit_reason="profit")

    # Identify the call to agent_trust_scores
    trust_call = None
    for call in mock_sync_redis.set.call_args_list:
        if call.args[0] == "agent_trust_scores":
            trust_call = call
            break

    assert trust_call is not None
    saved_scores = json.loads(trust_call.args[1])
    assert saved_scores["LSTMSignalAgent"] == 1.0
    assert saved_scores["MomentumAgent"] == -1.0
    assert saved_scores["RegimeDetectionAgent"] == 0.0


@patch("core.trade_intelligence.RedisClient")
@allure.feature("VC-6 Reporting & Client Servicing")
@allure.story("Reporting & Auditing")
def test_trade_attribution_loss(redis_mock):
    mock_sync_redis = MagicMock()
    redis_mock.get_sync_redis.return_value = mock_sync_redis
    mock_sync_redis.get.return_value = None

    ti = TradeIntelligence("dummy_file.json")

    # Fake Entry
    ti.record_entry(
        symbol="TSLA",
        entry_price=200.0,
        qty=1.0,
        confidence=0.7,
        round_table_scores={
            "LSTMSignalAgent": 0.9,  # Voted BUY strongly
            "MomentumAgent": 0.1,  # Voted SELL strongly
        },
    )

    # Fake Exit with Loss
    ti.record_exit(symbol="TSLA", exit_price=190.0, exit_reason="stop_loss")

    # LSTMSignalAgent was WRONG (voted buy, resulted in loss) -> gets -1
    # MomentumAgent was RIGHT (voted sell, resisted the buy, trade lost) -> gets +1
    trust_call = None
    for call in mock_sync_redis.set.call_args_list:
        if call.args[0] == "agent_trust_scores":
            trust_call = call
            break

    assert trust_call is not None
    saved_scores = json.loads(trust_call.args[1])
    assert saved_scores["LSTMSignalAgent"] == -1.0
    assert saved_scores["MomentumAgent"] == 1.0
