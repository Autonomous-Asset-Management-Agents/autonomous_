# core/secret_manager_utils.oss.py — OSS OS-Keychain Integration
#
# The OSS edition persists OAuth tokens to the local OS Keychain
# (Windows Credential Manager / macOS Keychain / Linux Secret Service).
# This provides secure local storage without needing a cloud database.
#
# This implementation preserves the same public API surface as the Enterprise
# OAuthSecretManager (GCP Secret Manager) so that module imports do not
# break.
#
# Enterprise edition: uses GCP Secret Manager (core/secret_manager_utils.py).
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class OAuthSecretManager:
    """
    OSS Local Keychain implementation.

    Saves and retrieves OAuth tokens using the local OS credential store.
    This replaces the GCP Secret Manager backend used in the Enterprise
    edition.
    """

    def __init__(self, project_id: Optional[str] = None):
        # project_id accepted for API compatibility; ignored in OSS.
        logger.debug(
            "OAuthSecretManager (OSS) initialised. "
            "OAuth tokens will be stored in the OS keychain."
        )

    def save_tokens(self, user_id: str, access_token: str, refresh_token: str) -> str:
        from core.keychain import _get_keyring

        kr = _get_keyring()
        if kr is None:
            raise RuntimeError(
                "keyring library not available. " "Install with: pip install keyring"
            )
        kr.set_password("aaagents", f"OAUTH_{user_id}_ACCESS", access_token)
        kr.set_password("aaagents", f"OAUTH_{user_id}_REFRESH", refresh_token)
        logger.info("OAuth tokens saved to OS keychain for user %s", user_id)
        return f"keychain:{user_id}"

    def get_tokens(self, secret_id: str) -> Optional[dict]:
        from core.keychain import _get_keyring

        kr = _get_keyring()
        if kr is None:
            return None
        user_id = secret_id.replace("keychain:", "")
        access = kr.get_password("aaagents", f"OAUTH_{user_id}_ACCESS")
        refresh = kr.get_password("aaagents", f"OAUTH_{user_id}_REFRESH")
        if access:
            return {"access_token": access, "refresh_token": refresh}
        return None


# Singleton — matches the Enterprise module's export surface.
# Kept as a module-level object so existing imports don't fail at load time.
oauth_secrets = OAuthSecretManager()
