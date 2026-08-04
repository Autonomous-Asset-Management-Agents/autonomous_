"""Unit tests for config.get_secret_str() — OSS edition.

Covers:
- Plain str passthrough
- None → empty string
- Pydantic SecretStr extraction
- TypeError on invalid input types

References: POLICY-01 from PR #1099 review.
"""

import importlib.util
import os

import pytest

# ---------------------------------------------------------------------------
# Resolve the correct config module.
#
# In the repository, *both* config.py (Enterprise) and config.oss.py (OSS)
# coexist.  When pytest runs locally from the ai_trading_bot/ directory,
# ``from config import …`` would load the Enterprise edition, making this
# test a no-op for OSS validation.
#
# Strategy: We always load config.oss.py explicitly via importlib to
# guarantee test-isolation regardless of which edition sits at config.py.
# ---------------------------------------------------------------------------
_OSS_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "config.oss.py"
)


def _load_oss_config():
    """Dynamically load config.oss.py so we always test the OSS edition."""
    abs_path = os.path.abspath(_OSS_CONFIG_PATH)
    spec = importlib.util.spec_from_file_location("config_oss", abs_path)
    assert spec and spec.loader, f"Could not find config.oss.py at {abs_path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def oss_config():
    """Module-scoped fixture providing the loaded OSS config module."""
    return _load_oss_config()


@pytest.fixture
def get_secret_str(oss_config):
    """Provide the get_secret_str function from the OSS config."""
    return oss_config.get_secret_str


class TestGetSecretStr:
    """Tests for config.get_secret_str() in the OSS edition."""

    def test_plain_string_passthrough(self, get_secret_str):
        """Plain strings are returned unchanged."""
        assert get_secret_str("my-api-key-123") == "my-api-key-123"

    def test_empty_string(self, get_secret_str):
        """Empty strings are returned as-is."""
        assert get_secret_str("") == ""

    def test_none_returns_empty_string(self, get_secret_str):
        """None input returns empty string (not None)."""
        assert get_secret_str(None) == ""

    def test_pydantic_secretstr(self, get_secret_str):
        """Pydantic SecretStr values are correctly unwrapped."""
        try:
            from pydantic import SecretStr
        except ImportError:
            pytest.skip("Pydantic not installed")

        secret = SecretStr("hunter2")
        assert get_secret_str(secret) == "hunter2"

    def test_secretstr_duck_typing(self, get_secret_str):
        """Any object with get_secret_value() is treated as SecretStr."""

        class FakeSecret:
            def get_secret_value(self) -> str:
                return "duck-typed-secret"

        assert get_secret_str(FakeSecret()) == "duck-typed-secret"

    def test_invalid_type_raises_typeerror(self, get_secret_str):
        """Non-str, non-SecretStr, non-None raises TypeError."""
        with pytest.raises(TypeError, match="expected SecretStr or str"):
            get_secret_str(12345)

    def test_invalid_type_includes_classname(self, get_secret_str):
        """TypeError message includes the actual type name."""
        with pytest.raises(TypeError, match="'int'"):
            get_secret_str(42)


class TestEnterpriseAliases:
    """Tests for Enterprise compatibility aliases in the OSS config."""

    def test_alpaca_api_key_alias(self, oss_config):
        """ALPACA_API_KEY should be an alias for API_KEY."""
        assert hasattr(oss_config, "ALPACA_API_KEY")
        assert oss_config.ALPACA_API_KEY == oss_config.API_KEY

    def test_alpaca_secret_key_alias(self, oss_config):
        """ALPACA_SECRET_KEY should be an alias for API_SECRET."""
        assert hasattr(oss_config, "ALPACA_SECRET_KEY")
        assert oss_config.ALPACA_SECRET_KEY == oss_config.API_SECRET

    def test_alpaca_base_url_alias(self, oss_config):
        """ALPACA_BASE_URL should be an alias for BASE_URL."""
        assert hasattr(oss_config, "ALPACA_BASE_URL")
        assert oss_config.ALPACA_BASE_URL == oss_config.BASE_URL

    def test_environment_default(self, oss_config):
        """ENVIRONMENT should default to 'development'."""
        assert oss_config.ENVIRONMENT == "development"

    def test_shadow_mode_default(self, oss_config):
        """SHADOW_MODE should default to False."""
        assert oss_config.SHADOW_MODE is False

    def test_staging_env_default(self, oss_config):
        """STAGING_ENV should default to False."""
        assert oss_config.STAGING_ENV is False
