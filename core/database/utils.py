import logging
import os

logger = logging.getLogger(__name__)


def get_database_url() -> str:
    """
    Resolve the DATABASE_URL from environment (GCP Secret Manager injects this at runtime).
    For local development, it falls back to the application configuration (which handles SQLite).
    """
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        try:
            from config import get_config

            config = get_config()
            # On local desktop (no Docker, no PG), this resolves to sqlite+aiosqlite:///...
            url = config.DATABASE_URL
        except Exception:
            logger.warning("Failed to load database URL from config", exc_info=True)

    if not url:
        raise RuntimeError("DATABASE_URL is not set in environment or config.")

    # Convert old schema string for asyncpg if necessary
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://") and not url.startswith(
        "postgresql+asyncpg://"
    ):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return url
