import os
import sys
from datetime import datetime, timezone

import allure
import pytest

# Add main dir to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from core.cloud_logger import CloudLogger, DecisionContext


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
def test_decision_context_reasoning_trace():
    # Create a dummy DecisionContext
    ctx = DecisionContext(
        symbol="AAPL",
        action="BUY",
        lstm_prediction=0.85,
        conviction_score=0.90,
        current_price=150.0,
        rsi_14=45.0,
    )

    # Simulate the CloudLogger processing
    logger = CloudLogger()
    logger.log_decision(ctx)

    # Validate the summary was built and injected into reasoning_trace
    assert ctx.reasoning_summary != ""
    assert ctx.reasoning_trace == ctx.reasoning_summary
    assert "LSTM predicted 0.85 (bullish signal)" in ctx.reasoning_trace
    assert "BOUGHT AAPL at $150.00" in ctx.reasoning_trace
