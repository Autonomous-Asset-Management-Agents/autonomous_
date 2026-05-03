from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from core.cloud_logger import DecisionContext


@dataclass
class SignalEvent:
    """
    Encapsulates a trading signal (BUY, SELL, HOLD) along with its comprehensive
    reasoning trace (DecisionContext). This enables asynchronous processing and
    strict Separation of Concerns between Strategy (Math) and Guardian (Execution).
    """

    symbol: str
    action: str  # BUY, SELL, HOLD
    decision_context: DecisionContext

    # Optional fields for execution
    suggested_quantity: float = 0.0
    is_simulation: bool = False

    @property
    def is_significant_hold(self) -> bool:
        """
        Smart Logging rule: Is this HOLD significant enough to log to Cloud SQL?
        We only want to log 'Boundary Collisions' (e.g. LSTM was highly confident but RL vetoed).
        """
        if self.action != "HOLD":
            return False

        # Significant if LSTM had high confidence (>0.6 or <-0.6) but we held
        if abs(self.decision_context.lstm_prediction) > 0.6:
            return True

        # Significant if Risk or Portfolio Managers blocked a trade
        if (
            not self.decision_context.risk_approved
            or not self.decision_context.portfolio_approved
        ):
            return True

        return False
