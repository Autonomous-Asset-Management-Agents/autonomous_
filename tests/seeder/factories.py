import uuid
from datetime import datetime, timezone

import factory
import factory.fuzzy

from core.database.models import Decision, PortfolioSnapshot, Trade
from tests.seeder.models import UserWalletSeed


class UserWalletFactory(factory.Factory):
    class Meta:
        model = UserWalletSeed

    user_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    broker_account_id = factory.Sequence(lambda n: f"acc_{n:04d}")
    secret_manager_id = factory.Sequence(lambda n: f"sm_{n:04d}")
    status = "active"
    risk_limits = factory.Dict(
        {
            "pdt_status": "safe",
            "day_trades_count": 0,
            "max_drawdown_pct": 0.1,
            "equity": 30000.0,
        }
    )
    created_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    updated_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))


class DecisionFactory(factory.Factory):
    class Meta:
        model = Decision

    decision_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    symbol = factory.fuzzy.FuzzyChoice(["AAPL", "TSLA", "MSFT", "GOOGL"])
    decision_time = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    action = "BUY"
    action_executed = True
    current_price = 150.0
    reasoning_summary = "Synthetic Test Decision"
    is_simulation = False


class TradeFactory(factory.Factory):
    class Meta:
        model = Trade

    trade_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    decision_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    symbol = "AAPL"
    side = "buy"
    qty = 10.0
    price = 150.0
    total_value = 1500.0
    entry_price = 150.0
    position_pnl = 0.0
    account_id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    executed_at = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    order_status = "filled"
    is_simulation = False


class PortfolioSnapshotFactory(factory.Factory):
    class Meta:
        model = PortfolioSnapshot

    id = factory.LazyFunction(lambda: str(uuid.uuid4()))
    timestamp = factory.LazyFunction(lambda: datetime.now(timezone.utc))
    total_equity = 30000.0
    cash = 28500.0
    positions_json = factory.List(
        [{"symbol": "AAPL", "qty": 10.0, "entry_price": 150.0, "current_price": 150.0}]
    )
    strategy_name = "Synthetic"
    is_simulation = False
