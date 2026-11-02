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

**Plaetze (#4631, nur bei ``STRATEGY_PROFILES_ENABLED``).** ``AAA_KEY_SLOT`` waehlt den Platz
einer Engine: ``paper1`` und ``live`` nutzen den heutigen Dienst ``aaagents`` mit den heutigen
Schluesseln (kein Bestandsnutzer verliert seine Keys), ``paper2``/``paper3`` eigene Dienste
``aaagents.paper2``/``aaagents.paper3`` — nur fuer ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY``.
Alle anderen Schluessel bleiben in ``aaagents``. Ein unbekannter Platz wirft ``ValueError``.

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

#: #4631: die Plaetze je Profil. ``paper1``/``live`` = ``SERVICE_NAME`` wie vor #4631.
PLAETZE = ("paper1", "paper2", "paper3", "live")
#: Nur diese Schluessel haben je Paper-Platz einen eigenen Dienst.
PLATZ_SCHLUESSEL = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")

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
    # Daily Report (private self-notification, Senior) — the user's OWN delivery secrets.
    # Keychain-managed so they are never written to plaintext .env/config (ADR_006); loaded
    # into os.environ by load_secrets_from_keychain() and read via core/daily_report.load_config.
    "WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "SMTP_PASSWORD",
]


def _platz(platz: Optional[str] = None) -> str:
    """Der geltende Platz: ``platz`` oder ``AAA_KEY_SLOT`` (nur mit Flag), sonst ``paper1``.

    Liest das Flag ueber ``core.profil_pfade`` direkt aus ``os.environ`` — ``config.oss.py``
    ruft den Schluesselbund **vor** ``import settings``.
    """
    from core import profil_pfade

    if platz is None:
        platz = (
            os.environ.get("AAA_KEY_SLOT", "paper1")
            if profil_pfade.aktiv()
            else "paper1"
        )
    if platz not in PLAETZE:
        raise ValueError(f"Unbekannter Schluessel-Platz: {platz}. Erlaubt: {PLAETZE}")
    if platz in ("paper2", "paper3") and not profil_pfade.aktiv():
        # Kein stiller Rueckfall auf aaagents: Er ueberschriebe die Keys von paper1.
        raise ValueError(
            f"Schluessel-Platz {platz} gibt es nur mit STRATEGY_PROFILES_ENABLED (#4631)."
        )
    return platz


def dienst_fuer(key: str, platz: Optional[str] = None) -> str:
    """Der Keyring-Dienst, unter dem ``key`` fuer ``platz`` liegt (#4631)."""
    gewaehlt = _platz(platz)
    if gewaehlt in ("paper2", "paper3") and key in PLATZ_SCHLUESSEL:
        return f"{SERVICE_NAME}.{gewaehlt}"
    return SERVICE_NAME


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

    # SEC-5 / Issue #2796: Isolate tests from live credentials.
    import sys

    if (
        "pytest" in sys.modules or os.environ.get("PYTEST_CURRENT_TEST")
    ) and not os.environ.get("ALLOW_KEYCHAIN_IN_TESTS"):
        return {}

    platz = _platz()  # #4631: ein unbekannter AAA_KEY_SLOT wirft, bevor gelesen wird
    loaded: dict[str, str] = {}
    for key in MANAGED_KEYS:
        # ADR-SECRETS-001: Never overwrite an explicitly set env var
        # (including empty strings, e.g. ALPACA_API_KEY="" to disable)
        if key in os.environ:
            continue
        try:
            value: Optional[str] = kr.get_password(dienst_fuer(key, platz), key)
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


def save_secret(key: str, value: str, platz: Optional[str] = None) -> None:
    """Save a secret to the OS keychain.

    Args:
        key: One of :data:`MANAGED_KEYS`.
        value: The plaintext secret value.
        platz: #4631 — one of :data:`PLAETZE`; default ``AAA_KEY_SLOT`` or ``paper1``.

    Raises:
        ValueError: If *key* is not in :data:`MANAGED_KEYS` or *platz* is unknown.
        RuntimeError: If the ``keyring`` library is not available.
    """
    if key not in MANAGED_KEYS:
        raise ValueError(
            f"Unknown managed key: {key}. Allowed: {MANAGED_KEYS}"
        )  # noqa: E501
    dienst = dienst_fuer(key, platz)
    kr = _get_keyring()
    if kr is None:
        raise RuntimeError(
            "keyring library not available. "
            "Install with: pip install keyring"  # noqa: E501
        )
    kr.set_password(dienst, key, value)
    logger.info("SEC-5: Saved %s to OS keychain (%s)", key, dienst)


def delete_secret(key: str, platz: Optional[str] = None) -> None:
    """Remove a secret from the OS keychain.

    Silently succeeds if the key does not exist or ``keyring``
    is not available. *platz* wie bei :func:`save_secret` (#4631).
    """
    dienst = dienst_fuer(key, platz)
    kr = _get_keyring()
    if kr is None:
        return
    try:
        kr.delete_password(dienst, key)
        logger.info("SEC-5: Deleted %s from OS keychain (%s)", key, dienst)
    except Exception:  # noqa: BLE001
        pass  # Key may not exist — that's fine


def has_secrets(platz: Optional[str] = None) -> bool:
    """Check if the OS keychain contains at least ``ALPACA_API_KEY``.

    Used by the desktop UI for first-launch detection:
    if no secrets → show Setup Wizard.

    #4631: *platz* wie bei :func:`save_secret`; fuer ``live`` gilt ``ALPACA_LIVE_API_KEY``.
    """
    gewaehlt = _platz(platz)
    key = "ALPACA_LIVE_API_KEY" if gewaehlt == "live" else "ALPACA_API_KEY"
    dienst = dienst_fuer(key, gewaehlt)
    kr = _get_keyring()
    if kr is None:
        return False
    try:
        return kr.get_password(dienst, key) is not None
    except Exception:  # noqa: BLE001
        return False
