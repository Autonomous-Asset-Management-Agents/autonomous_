import os
from unittest.mock import patch

import pytest

from settings import RuntimeConfigState, _clean_env

pytestmark = pytest.mark.vc0


@pytest.mark.vc0
def test_clean_env_empty():
    with patch.dict(os.environ, {"TEST_KEY": "   "}):
        assert _clean_env("TEST_KEY", default="def") == "def"

    with patch.dict(os.environ, {"TEST_KEY": ""}):
        assert _clean_env("TEST_KEY", default="def") == "def"

    with patch.dict(os.environ, {"TEST_KEY": "val"}):
        assert _clean_env("TEST_KEY", default="def") == "val"


@pytest.mark.vc0
def test_strip_and_parse_bools():
    with patch.dict(
        os.environ,
        {"PAPER_TRADING": "false", "VALUATION_MULTIMETRIC_ENABLED": "True"},
    ):
        config = RuntimeConfigState()
        assert config.PAPER_TRADING is False
        assert config.VALUATION_MULTIMETRIC_ENABLED is True


@pytest.mark.vc0
def test_select_alpaca_account_paper():
    with patch.dict(
        os.environ,
        {
            "DEPLOYMENT_MODE": "LOCAL",
            "PAPER_TRADING": "true",
            "ALPACA_API_KEY": "paper_key",
            "ALPACA_SECRET_KEY": "paper_secret",
            "ALPACA_LIVE_API_KEY": "live_key",
            "ALPACA_LIVE_SECRET_KEY": "live_secret",
        },
    ):
        config = RuntimeConfigState()
        assert config.ALPACA_API_KEY.get_secret_value() == "paper_key"
        assert config.ALPACA_SECRET_KEY.get_secret_value() == "paper_secret"


@pytest.mark.vc0
def test_select_alpaca_account_live():
    with patch.dict(
        os.environ,
        {
            "DEPLOYMENT_MODE": "LOCAL",
            "PAPER_TRADING": "false",
            "ALPACA_API_KEY": "paper_key",
            "ALPACA_SECRET_KEY": "paper_secret",
            "ALPACA_LIVE_API_KEY": "live_key",
            "ALPACA_LIVE_SECRET_KEY": "live_secret",
        },
    ):
        config = RuntimeConfigState()
        assert config.ALPACA_API_KEY.get_secret_value() == "live_key"
        assert config.ALPACA_SECRET_KEY.get_secret_value() == "live_secret"


@pytest.mark.vc0
def test_default_symbols_not_mutable():
    config1 = RuntimeConfigState()
    config1.DEFAULT_SYMBOLS.append("TEST")
    config2 = RuntimeConfigState()
    assert "TEST" not in config2.DEFAULT_SYMBOLS


@pytest.mark.vc0
def test_config_py_getattr():
    import config

    attr_name = "PAPER_TRADING"
    assert hasattr(config, attr_name)
    assert getattr(config, attr_name) in [True, False]


@pytest.mark.vc0
def test_compliance_max_order_value_clamped():
    from core.governance.iron_dome_policy import MAX_ORDER_VALUE_CEILING

    with patch.dict(
        os.environ,
        {"COMPLIANCE_MAX_ORDER_VALUE": str(MAX_ORDER_VALUE_CEILING + 1000.0)},
    ):
        config = RuntimeConfigState()
        assert config.COMPLIANCE_MAX_ORDER_VALUE == MAX_ORDER_VALUE_CEILING
