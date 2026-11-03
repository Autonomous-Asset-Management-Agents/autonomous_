import asyncio
import logging
import os
import sys

from core.database.models import Decision, PortfolioSnapshot, Trade
from core.database.session import AsyncSessionLocal
from tests.seeder.factories import (
    DecisionFactory,
    PortfolioSnapshotFactory,
    TradeFactory,
    UserWalletFactory,
)
from tests.seeder.models import UserWalletSeed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DatabaseSeeder")


async def seed_database():
    env = os.environ.get("ENV", "local").lower()
    # SAFETY TRIPWIRE — DO NOT REPLACE THE PROJECT ID BELOW.
    # aaa-cloud-487813 is the real production GCP project (see infra/terraform/prod.tfvars,
    # cloudbuild-backend-deploy.yaml, and .github/workflows/bdd-on-demand.yml).
    # Replacing it with a placeholder disarms the tripwire: anyone running the seeder with
    # PROJECT_ID=aaa-cloud-487813 would bypass this check and write synthetic data into
    # production. This is a load-bearing constant, not a placeholder.
    if env == "production" or os.environ.get("PROJECT_ID", "") == "aaa-cloud-487813":
        logger.error("FATAL: Cannot run seeder against production environment!")
        raise RuntimeError("FATAL: Cannot run seeder against production environment!")

    logger.info(f"Starting Database Seeder for environment: {env}")

    async with AsyncSessionLocal() as session:
        # Phase 1: Clear existing synthetic data (Idempotency)
        # Assuming we can identify synthetic data, e.g., by checking a specific property
        # or simply clearing all if we are on a dedicated test DB.
        # But we must be careful. Let's just delete by a known 'Synthetic' criteria or clear all
        # since this is supposed to be a staging/test DB.
        # To be safe, we'll only delete if it's explicitly a local DB, or we can just use UPSERT.

        # Let's create the Personas

        # Persona 1: Vanilla (Normal retail user)
        vanilla_user = UserWalletFactory.build(
            user_id="user_vanilla",
            risk_limits={
                "pdt_status": "safe",
                "day_trades_count": 0,
                "max_drawdown_pct": 0.1,
                "equity": 50000.0,
            },
        )

        # Persona 2: PDT-Risk
        pdt_user = UserWalletFactory.build(
            user_id="user_pdt_risk",
            risk_limits={
                "pdt_status": "warning",
                "day_trades_count": 3,
                "max_drawdown_pct": 0.1,
                "equity": 24000.0,  # Under $25k triggers PDT rule
            },
        )

        # Persona 3: Wash-Trader (Requires recent loss trade)
        wash_trader_user = UserWalletFactory.build(
            user_id="user_wash_trader",
            risk_limits={
                "pdt_status": "safe",
                "day_trades_count": 0,
                "max_drawdown_pct": 0.1,
                "equity": 30000.0,
            },
        )

        # Persona 4: Margin-Call
        margin_call_user = UserWalletFactory.build(
            user_id="user_margin_call",
            risk_limits={
                "pdt_status": "safe",
                "day_trades_count": 0,
                "max_drawdown_pct": 0.5,  # Huge drawdown allowed just to test it
                "equity": 500.0,  # Very low equity
            },
        )

        # For the wash trader, we need a trade history (sold at a loss)
        wash_decision = DecisionFactory.build(
            decision_id="dec_wash",
            action="SELL",
            symbol="XYZ",
            reasoning_summary="Synthetic Wash Trade Setup",
        )
        wash_trade = TradeFactory.build(
            trade_id="trade_wash",
            decision_id="dec_wash",
            symbol="XYZ",
            side="sell",
            price=100.0,
            entry_price=120.0,  # Sold at a $20 loss
            position_pnl=-200.0,
            account_id="user_wash_trader",
        )

        # Add all to session
        session.add_all([vanilla_user, pdt_user, wash_trader_user, margin_call_user])
        session.add(wash_decision)
        session.add(wash_trade)

        try:
            await session.commit()
            logger.info("Successfully seeded synthetic personas and edge cases.")
        except Exception as e:
            await session.rollback()
            logger.error(f"Failed to seed database: {e}")


if __name__ == "__main__":
    asyncio.run(seed_database())
