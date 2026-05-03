# core/user_secrets.py
# Epic 3.4-pre: Alpaca User-Account Mapping (Issue #411)
# TDD GREEN — implementation written after tests confirmed RED.
#
# Security contract:
#   - Credentials NEVER logged (masking enforced via _mask)
#   - Credentials NEVER stored in Cloud SQL (only secret_ref prefixes)
#   - Naming: alpaca-{safe_uid}-api-key / -secret-key / -base-url

import logging
import os
from dataclasses import dataclass
from typing import Optional

from google.cloud import secretmanager
from google.api_core.exceptions import NotFound, AlreadyExists

try:
    from opentelemetry import trace

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

import config

logger = logging.getLogger(__name__)


# ── Custom exceptions ─────────────────────────────────────────────────────────


class UserAlpacaCredentialsNotFoundError(Exception):
    """Raised when no Alpaca credentials exist for the given Firebase UID."""

    def __init__(self, uid: str):
        super().__init__(
            f"No Alpaca credentials found for uid={uid!r}. "
            "Register via POST /admin/users/{uid}/alpaca-account."
        )
        self.uid = uid


class UserAlpacaSecretStoreError(Exception):
    """Raised when storing credentials to GCP Secret Manager fails."""


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class AlpacaCredentials:
    """Resolved Alpaca credentials — never serialise or include in logs."""

    api_key: str
    secret_key: str
    base_url: str
    uid: str

    def __repr__(self) -> str:
        return (
            f"AlpacaCredentials(uid={self.uid!r}, "
            f"api_key=***masked***, base_url={self.base_url!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()


# ── Helpers ───────────────────────────────────────────────────────────────────


def _safe_uid(uid: str) -> str:
    """Convert Firebase UID to GCP Secret Manager-safe identifier."""
    return "".join(c if c.isalnum() or c == "-" else "_" for c in uid)


def _mask(value: str) -> str:
    """Returns a safely masked version for logging."""
    if not value:
        return "***empty***"
    return value[:4] + "***" + value[-2:] if len(value) > 6 else "***masked***"


def _secret_ref_prefix(uid: str) -> str:
    """Returns the secret_ref prefix used in Cloud SQL (not a full GCP path)."""
    return f"alpaca-{_safe_uid(uid)}"


# ── Null context manager for when OTel is unavailable ────────────────────────


class _nullspan:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        pass


# ── Core implementation ───────────────────────────────────────────────────────


class AlpacaUserSecretManager:
    """
    Firebase-UID-scoped Alpaca credentials in GCP Secret Manager.

    Naming convention:
        alpaca-{safe_uid}-api-key     → Alpaca API Key
        alpaca-{safe_uid}-secret-key  → Alpaca Secret Key
        alpaca-{safe_uid}-base-url    → Alpaca Base URL
    """

    def __init__(self, project_id: Optional[str] = None):
        self.project_id = (
            project_id
            or getattr(config, "GCP_PROJECT_ID", None)
            or os.getenv("GCP_PROJECT_ID")
        )
        if not self.project_id:
            logger.warning(
                "GCP_PROJECT_ID not set. AlpacaUserSecretManager uses local fallback."
            )
            self._client = None
        else:
            try:
                self._client = secretmanager.SecretManagerServiceClient()
            except Exception as exc:
                logger.error("Failed to init SecretManagerServiceClient: %s", exc)
                self._client = None

        self._local_store: dict[str, str] = {}

    def _ensure_secret_exists(self, secret_id: str) -> None:
        parent = f"projects/{self.project_id}"
        try:
            self._client.get_secret(request={"name": f"{parent}/secrets/{secret_id}"})
        except NotFound:
            logger.info("Creating GCP secret: %s", secret_id)
            try:
                self._client.create_secret(
                    request={
                        "parent": parent,
                        "secret_id": secret_id,
                        "secret": {"replication": {"automatic": {}}},
                    }
                )
            except AlreadyExists:
                pass

    def _write_version(self, secret_id: str, value: str) -> None:
        secret_path = f"projects/{self.project_id}/secrets/{secret_id}"
        self._client.add_secret_version(
            request={"parent": secret_path, "payload": {"data": value.encode("UTF-8")}}
        )

    def _read_latest(self, secret_id: str) -> Optional[str]:
        version_path = f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
        try:
            response = self._client.access_secret_version(
                request={"name": version_path}
            )
            return response.payload.data.decode("UTF-8")
        except NotFound:
            return None
        except Exception as exc:
            logger.error("GCP Secret read error for %s: %s", secret_id, exc)
            return None

    def store_user_alpaca_secret(
        self, uid: str, api_key: str, secret_key: str, base_url: str
    ) -> str:
        """
        Stores all three Alpaca credentials in GCP Secret Manager.
        Returns secret_ref prefix for Cloud SQL storage.
        Raises UserAlpacaSecretStoreError on failure.
        """
        safe = _safe_uid(uid)
        secrets = {
            f"alpaca-{safe}-api-key": api_key,
            f"alpaca-{safe}-secret-key": secret_key,
            f"alpaca-{safe}-base-url": base_url,
        }

        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None
        span_ctx = (
            tracer.start_as_current_span("alpaca.secrets.store")
            if tracer
            else _nullspan()
        )

        with span_ctx as span:
            if span and OTEL_AVAILABLE:
                try:
                    span.set_attribute("user.uid_prefix", uid[:8] + "***")
                    span.set_attribute("secret.count", len(secrets))
                except Exception:
                    pass

            if not self._client or not self.project_id:
                for secret_id, value in secrets.items():
                    self._local_store[secret_id] = value
                logger.info(
                    "[Local Dev] Stored Alpaca secrets for uid=%s (api_key=%s)",
                    uid,
                    _mask(api_key),
                )
                return _secret_ref_prefix(uid)

            try:
                for secret_id, value in secrets.items():
                    self._ensure_secret_exists(secret_id)
                    self._write_version(secret_id, value)
                    logger.info("Stored secret version: %s", secret_id)
            except Exception as exc:
                raise UserAlpacaSecretStoreError(
                    f"Failed to store Alpaca secrets for uid={uid!r}: {exc}"
                ) from exc

        return _secret_ref_prefix(uid)

    def get_user_alpaca_credentials(self, uid: str) -> AlpacaCredentials:
        """
        Retrieves Alpaca credentials for the given Firebase UID.
        Raises UserAlpacaCredentialsNotFoundError if no secrets exist.
        Credentials are NEVER logged — only masked values in logs.
        """
        safe = _safe_uid(uid)
        key_types = {
            "api_key": f"alpaca-{safe}-api-key",
            "secret_key": f"alpaca-{safe}-secret-key",
            "base_url": f"alpaca-{safe}-base-url",
        }

        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None
        span_ctx = (
            tracer.start_as_current_span("alpaca.credentials.resolve")
            if tracer
            else _nullspan()
        )

        with span_ctx as span:
            if span and OTEL_AVAILABLE:
                try:
                    span.set_attribute("user.uid_prefix", uid[:8] + "***")
                except Exception:
                    pass

            if not self._client or not self.project_id:
                resolved = {}
                for field, secret_id in key_types.items():
                    value = self._local_store.get(secret_id)
                    if not value:
                        raise UserAlpacaCredentialsNotFoundError(uid)
                    resolved[field] = value
                return AlpacaCredentials(uid=uid, **resolved)

            resolved = {}
            for field, secret_id in key_types.items():
                value = self._read_latest(secret_id)
                if value is None:
                    raise UserAlpacaCredentialsNotFoundError(uid)
                resolved[field] = value

            logger.debug(
                "Resolved credentials uid=%s api_key=%s base_url=%s",
                uid,
                _mask(resolved["api_key"]),
                resolved["base_url"],
            )

        return AlpacaCredentials(uid=uid, **resolved)

    def revoke_user_alpaca_secret(self, uid: str) -> None:
        """
        Disables all Alpaca secret versions for a user (not deleted — audit trail preserved).
        """
        safe = _safe_uid(uid)
        secret_ids = [
            f"alpaca-{safe}-api-key",
            f"alpaca-{safe}-secret-key",
            f"alpaca-{safe}-base-url",
        ]

        if not self._client or not self.project_id:
            for secret_id in secret_ids:
                self._local_store.pop(secret_id, None)
            logger.info("[Local Dev] Revoked Alpaca secrets for uid=%s", uid)
            return

        for secret_id in secret_ids:
            try:
                version_path = (
                    f"projects/{self.project_id}/secrets/{secret_id}/versions/latest"
                )
                self._client.disable_secret_version(request={"name": version_path})
                logger.info("Disabled secret version: %s", secret_id)
            except NotFound:
                logger.warning("Secret not found during revoke: %s", secret_id)
            except Exception as exc:
                logger.error("Failed to revoke secret %s: %s", secret_id, exc)


# ── Singleton ─────────────────────────────────────────────────────────────────

user_alpaca_secrets = AlpacaUserSecretManager()
