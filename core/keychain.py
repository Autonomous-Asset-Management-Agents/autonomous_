"""SEC-5 (#1084): OS Keychain integration for credential storage.

Replaces plaintext .env.oss secrets with encrypted OS-native storage.
Uses the ``keyring`` library which maps to:

- **Windows:** Credential Manager (DPAPI encryption)
- **macOS:** Keychain
- **Linux:** Secret Service (D-Bus / GNOME Keyring)

The public API is intentionally minimal:

- :func:`load_secrets_from_keychain` — inject keychain secrets into
  ``os.environ`` **before** ``load_dotenv()`` runs.
- :func:`save_secret` / :func:`delete_secret` — CRUD for managed keys.
- :func:`has_secrets` — quick check for first-launch detection.

**Precedence (highest wins):**
  1. Explicit env var (e.g. CI: ``ALPACA_API_KEY=xxx python …``)
  2. OS Keychain (via ``keyring``)
  3. ``.env.oss`` (via ``load_dotenv``)

ADR: ADR_006_LOCAL_CREDENTIAL_STORE.md (Accepted → Implemented)
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

SERVICE_NAME = "aaagents"

MANAGED_KEYS = [
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    # Live trading uses a SEPARATE Alpaca account (different keys + base_url, #1425). Stored in
    # their own slots so paper and live credentials never overwrite each other (getrennte
    # Aufbewahrung) and the operator can switch between accounts.
    "ALPACA_LIVE_API_KEY",
    "ALPACA_LIVE_SECRET_KEY",
    "GEMINI_API_KEY",
    "POLYGON_API_KEY",
    "DATABENTO_API_KEY",
]


def _get_keyring():
    """Lazy-import ``keyring`` to avoid import-time side effects (BORA-01).

    Returns the keyring module if available, otherwise ``None``.
    """
    try:
        import keyring  # noqa: WPS433 — intentional lazy import

        return keyring
    except ImportError:
        logger.warning(
            "SEC-5: keyring library not installed. "
            "Falling back to .env.oss for secrets. "
            "Install with: pip install keyring"
        )
        return None


def load_secrets_from_keychain() -> dict[str, str]:
    """Load managed secrets from OS keychain into ``os.environ``.

    Called **before** ``load_dotenv()`` in ``config.oss.py`` so that
    keychain values take precedence over ``.env.oss``, but explicit
    env vars (e.g. from CI pipelines) are never overwritten.

    Returns:
        Dict of ``{key_name: "(from keychain)"}`` for keys that were
        successfully loaded.
    """
    kr = _get_keyring()
    if kr is None:
        return {}

    loaded: dict[str, str] = {}
    for key in MANAGED_KEYS:
        # ADR-SECRETS-001: Never overwrite an explicitly set env var
        # (including empty strings, e.g. ALPACA_API_KEY="" to disable)
        if key in os.environ:
            continue
        try:
            value: Optional[str] = kr.get_password(SERVICE_NAME, key)
            if value:
                os.environ[key] = value
                loaded[key] = "(from keychain)"
        except Exception as exc:  # noqa: BLE001
            # catch-all for keyring backends
            logger.warning(
                "SEC-5: Keychain read failed for %s: %s",
                key,
                exc,
            )

    if loaded:
        logger.info(
            "SEC-5: Loaded %d secret(s) from OS keychain: %s",
            len(loaded),
            ", ".join(loaded.keys()),
        )
    return loaded


def save_secret(key: str, value: str) -> None:
    """Save a secret to the OS keychain.

    Args:
        key: One of :data:`MANAGED_KEYS`.
        value: The plaintext secret value.

    Raises:
        ValueError: If *key* is not in :data:`MANAGED_KEYS`.
        RuntimeError: If the ``keyring`` library is not available.
    """
    if key not in MANAGED_KEYS:
        raise ValueError(
            f"Unknown managed key: {key}. Allowed: {MANAGED_KEYS}"
        )  # noqa: E501
    kr = _get_keyring()
    if kr is None:
        raise RuntimeError(
            "keyring library not available. "
            "Install with: pip install keyring"  # noqa: E501
        )
    kr.set_password(SERVICE_NAME, key, value)
    logger.info("SEC-5: Saved %s to OS keychain", key)


def delete_secret(key: str) -> None:
    """Remove a secret from the OS keychain.

    Silently succeeds if the key does not exist or ``keyring``
    is not available.
    """
    kr = _get_keyring()
    if kr is None:
        return
    try:
        kr.delete_password(SERVICE_NAME, key)
        logger.info("SEC-5: Deleted %s from OS keychain", key)
    except Exception:  # noqa: BLE001
        pass  # Key may not exist — that's fine


def has_secrets() -> bool:
    """Check if the OS keychain contains at least ``ALPACA_API_KEY``.

    Used by the desktop UI for first-launch detection:
    if no secrets → show Setup Wizard.
    """
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        return kr.get_password(SERVICE_NAME, "ALPACA_API_KEY") is not None
    except Exception:  # noqa: BLE001
        return False
