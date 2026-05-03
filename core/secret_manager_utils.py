# core/secret_manager_utils.oss.py — OSS Stub
#
# Stores OAuth tokens in a local JSON file (~/.aaagents/oauth_tokens.json).
# Survives process restarts. NOT suitable for multi-user or production
# deployments — configure a proper secret backend (Redis, Vault, DB) for those.
import logging
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PERSIST_FILE = Path.home() / ".aaagents" / "oauth_tokens.json"


class OAuthSecretManager:
    """
    OSS stub — persists OAuth tokens to a local JSON file.

    ⚠️  NOT thread-safe. Concurrent writes from multiple async workers
        can cause token loss (last-write-wins). Single-worker deployments only.
        For multi-replica or async setups, use a Redis or DB backend.

    Replaces the GCP Secret Manager backend used in the Enterprise edition.
    Token data is stored at: ~/.aaagents/oauth_tokens.json

    Limitations vs Enterprise:
    - Single-machine only (no distributed secret storage)
    - File permissions control access (chmod 600 recommended)
    - Not suitable for Kubernetes/multi-replica deployments

    For production multi-user setups, implement a custom backend that
    stores tokens in PostgreSQL or Redis and inject it as ``oauth_secrets``.
    """

    def __init__(self, project_id: Optional[str] = None):
        # project_id accepted for API compatibility with Enterprise version;
        # ignored in OSS — all storage is local.
        _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._store: dict[str, str] = {}
        self._load()

    # ── Persistence helpers ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load token store from disk; recovers gracefully on corruption."""
        if not _PERSIST_FILE.exists():
            return
        try:
            data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._store = data
            else:
                logger.warning("Token store had unexpected format — discarding.")
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load token store (%s) — starting fresh.", exc)

    def _save(self) -> None:
        """Persist token store to disk atomically with strict permissions."""
        parent_dir = _PERSIST_FILE.parent
        parent_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Atomarer Write: Schreiben in ein temporäres File im gleichen Filesystem
            fd, temp_path = tempfile.mkstemp(
                dir=parent_dir, prefix="oauth_", suffix=".tmp"
            )

            # 2. Strict Permissions: fd wird direkt mit 600 von mkstemp erstellt
            # Kein TOCTOU-Fenster vorhanden!
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._store, f, indent=2)
                f.flush()
                os.fsync(f.fileno())  # Garantie, dass Daten physisch auf Disk liegen

            # 3. Atomarer Swap: Ersetzt die alte Datei unterbrechungsfrei
            os.replace(temp_path, _PERSIST_FILE)

        except OSError as exc:
            logger.error("Critical: Could not persist token store atomically: %s", exc)
            if "temp_path" in locals() and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    # ── Public API (mirrors Enterprise OAuthSecretManager) ────────────────────

    def save_tokens(self, user_id: str, access_token: str, refresh_token: str) -> str:
        """Save tokens and return the secret_id key."""
        key = f"alpaca-oauth-{user_id}"
        self._store[key] = json.dumps(
            {"access_token": access_token, "refresh_token": refresh_token}
        )
        self._save()
        logger.info("Saved OAuth tokens for %s to %s", key, _PERSIST_FILE)
        return key

    def get_tokens(self, secret_id: str) -> Optional[dict[str, str]]:
        """Retrieve tokens by secret_id, or None if not found."""
        payload = self._store.get(secret_id)
        if payload:
            logger.debug("Retrieved OAuth tokens for %s", secret_id)
            return json.loads(payload)
        logger.warning("OAuth tokens not found for %s", secret_id)
        return None


# Singleton — matches the Enterprise module's export surface.
oauth_secrets = OAuthSecretManager()
