from sqlalchemy import Column, DateTime, String, func

from core.database.models import JSON_TYPE, Base


class UserWalletSeed(Base):
    """
    SQLAlchemy model mapped to the user_wallets table purely for factory_boy seeding.
    The core application uses asyncpg raw queries for this table.
    """

    __tablename__ = "user_wallets"

    user_id = Column(String, primary_key=True)
    broker_account_id = Column(String)
    secret_manager_id = Column(String, nullable=False)
    status = Column(String, default="inactive")
    risk_limits = Column(JSON_TYPE, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
