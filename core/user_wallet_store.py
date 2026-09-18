"""
core/user_wallet_store.py — UserWallet CRUD Operations (BORA dual-mode).

OSS-4 / #1085: Rewritten from raw asyncpg queries to SQLAlchemy ORM
for BORA dual-mode compatibility (SQLite + PostgreSQL).

Previous version used asyncpg.Pool with raw SQL and $1/$2 parameters.
New version uses SQLAlchemy ORM sessions from core.database.session.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class UserWalletStore:
    """
    Manages the `user_wallets` table via SQLAlchemy ORM.

    BORA dual-mode: Works identically on SQLite (desktop) and
    PostgreSQL (enterprise) via the shared engine in session.py.
    """

    def __init__(self):
        self.is_connected: bool = False
        self._session_factory = None
        self._engine = None

    async def connect(self):
        """Initialize the ORM session factory from the shared engine."""
        try:
            from core.database.session import AsyncSessionLocal, engine

            self._session_factory = AsyncSessionLocal
            self._engine = engine
            self.is_connected = True
            logger.info("UserWalletStore connected via SQLAlchemy ORM")
        except Exception as e:
            logger.error("Failed to connect UserWalletStore: %s", e)

    async def close(self):
        """No-op for ORM — engine lifecycle managed by session.py."""
        pass

    async def upsert_wallet(
        self, user_id: str, broker_account_id: str, secret_manager_id: str
    ) -> None:
        if not self.is_connected or not self._session_factory:
            logger.info(
                "[Local MOCK] Upserted wallet for %s: %s, %s",
                user_id,
                broker_account_id,
                secret_manager_id,
            )
            return

        from sqlalchemy import select

        from core.database.models import UserWallet

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id)
                )
                existing = result.scalars().first()

                now = datetime.now(timezone.utc)
                if existing:
                    existing.broker_account_id = broker_account_id
                    existing.secret_manager_id = secret_manager_id
                    existing.updated_at = now
                else:
                    wallet = UserWallet(
                        user_id=user_id,
                        broker_account_id=broker_account_id,
                        secret_manager_id=secret_manager_id,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(wallet)

    async def get_wallet(self, user_id: str) -> Optional[Dict[str, Any]]:
        if not self.is_connected or not self._session_factory:
            return None

        from sqlalchemy import select

        from core.database.models import UserWallet

        async with self._session_factory() as session:
            result = await session.execute(
                select(UserWallet).where(UserWallet.user_id == user_id)
            )
            row = result.scalars().first()
            if row is None:
                return None

            return {
                "user_id": row.user_id,
                "broker_account_id": row.broker_account_id,
                "secret_manager_id": row.secret_manager_id,
                "status": row.status,
                "risk_limits": row.risk_limits or {},
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }

    async def get_active_wallets(self) -> List[Dict[str, Any]]:
        if not self.is_connected or not self._session_factory:
            return []

        from sqlalchemy import select

        from core.database.models import UserWallet

        async with self._session_factory() as session:
            result = await session.execute(
                select(UserWallet).where(UserWallet.status == "active")
            )
            rows = result.scalars().all()
            return [
                {
                    "user_id": r.user_id,
                    "broker_account_id": r.broker_account_id,
                    "secret_manager_id": r.secret_manager_id,
                    "status": r.status,
                    "risk_limits": r.risk_limits or {},
                    "created_at": r.created_at,
                    "updated_at": r.updated_at,
                }
                for r in rows
            ]

    async def update_risk_limits(self, user_id: str, limits: Dict[str, Any]) -> bool:
        if not self.is_connected or not self._session_factory:
            logger.info("[Local MOCK] Updated risk limits for %s: %s", user_id, limits)
            return True

        from sqlalchemy import select

        from core.database.models import UserWallet

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id)
                )
                wallet = result.scalars().first()
                if wallet is None:
                    return False

                wallet.risk_limits = limits
                wallet.updated_at = datetime.now(timezone.utc)
                return True

    async def update_alpaca_keys(
        self, user_id: str, api_key: str, secret_key: str
    ) -> bool:
        """Disabled in OSS Edition. API keys must be set via .env.oss.

        This method is a no-op stub. Writing Alpaca keys to the database
        is not supported in the OSS edition; all credentials are loaded
        from environment variables (ALPACA_API_KEY, ALPACA_SECRET_KEY).

        Raises:
            NotImplementedError: Always. Instructs the caller to use .env instead.
        """
        raise NotImplementedError(
            "Storing Alpaca API keys in the database is disabled in the OSS Edition. "
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env.oss file. "
            "See README.oss.md for setup instructions."
        )

    @staticmethod
    def get_alpaca_keys_from_env() -> dict[str, str] | None:
        """Load Alpaca credentials exclusively from environment variables.

        Returns a dict with 'api_key' and 'secret_key' if both are set,
        or None if either is missing (triggering Offline/Shadow Boot mode).
        """
        api_key = os.environ.get("ALPACA_API_KEY", "").strip()
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "").strip()
        if not api_key or not secret_key:
            logger.warning(
                "ALPACA_API_KEY or ALPACA_SECRET_KEY not set in environment. "
                "System will run in Offline (Shadow Boot) mode — no orders will execute."
            )
            return None
        return {"api_key": api_key, "secret_key": secret_key}

    async def update_status(self, user_id: str, status: str) -> bool:
        if not self.is_connected or not self._session_factory:
            logger.info("[Local MOCK] Updated status for %s: %s", user_id, status)
            return True

        # Validation
        if status not in ["active", "inactive", "halted"]:
            raise ValueError("Invalid status")

        from sqlalchemy import select

        from core.database.models import UserWallet

        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(UserWallet).where(UserWallet.user_id == user_id)
                )
                wallet = result.scalars().first()
                if wallet is None:
                    return False

                wallet.status = status
                wallet.updated_at = datetime.now(timezone.utc)
                return True


# Singleton instance
wallet_store = UserWalletStore()
