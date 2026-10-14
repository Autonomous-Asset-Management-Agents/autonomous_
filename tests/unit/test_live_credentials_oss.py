"""BORA parity (audit #12)..."""

import copy
import importlib.util
import os
from contextlib import contextmanager
from unittest.mock import patch

_PAPER_URL = "https://paper-api.alpaca.markets"
_LIVE_URL = "https://api.alpaca.markets"


@contextmanager
def _load_oss(**env):
    import sys

    import config as config_full

    oss_path = os.path.join(os.path.dirname(config_full.__file__), "config.oss.py")
    base = {
        "ALPACA_API_KEY": "",
        "ALPACA_SECRET_KEY": "",
        "ALPACA_LIVE_API_KEY": "",
        "ALPACA_LIVE_SECRET_KEY": "",
        "GEMINI_API_KEY": "",
        "HITL_ENABLED": "true",
    }
    base.update(env)
    spec = importlib.util.spec_from_file_location("config_oss_cred_parity", oss_path)
    mod = importlib.util.module_from_spec(spec)

    import settings

    old_state = settings._config_state
    old_environ = os.environ.copy()

    try:
        os.environ.update(base)
        # Create a new config state with the patched env, WITHOUT reloading the module
        settings._config_state = settings.RuntimeConfigState(_env_file=None, **base)
        os.environ.pop("ALPACA_BASE_URL", None)

        spec.loader.exec_module(mod)
        yield mod
    finally:
        # Restore the original config state
        settings._config_state = old_state
        os.environ.clear()
        os.environ.update(old_environ)


def test_oss_paper_account_keeps_paper_keys():
    with _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="true",
        ALPACA_API_KEY="paper_k",
        ALPACA_LIVE_API_KEY="live_k",
    ) as mod:
        assert mod.ALPACA_API_KEY.get_secret_value() == "paper_k"
        assert mod.ALPACA_API_KEY.get_secret_value() == mod.API_KEY.get_secret_value()
        assert mod.ALPACA_BASE_URL == _PAPER_URL


def test_oss_live_account_swaps_to_live_keys():
    with _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
        ALPACA_LIVE_API_KEY="live_k",
        ALPACA_LIVE_SECRET_KEY="live_s",
    ) as mod:
        assert mod.ALPACA_API_KEY.get_secret_value() == "live_k"
        assert mod.ALPACA_SECRET_KEY.get_secret_value() == "live_s"
        assert (
            mod.ALPACA_API_KEY.get_secret_value()
            == mod.ALPACA_LIVE_API_KEY.get_secret_value()
        )
        assert mod.ALPACA_BASE_URL == _LIVE_URL


def test_oss_live_without_live_keys_fails_closed():
    with _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
    ) as mod:
        val = (
            mod.ALPACA_API_KEY.get_secret_value()
            if hasattr(mod.ALPACA_API_KEY, "get_secret_value")
            else mod.ALPACA_API_KEY
        )
        assert val in (None, "")
        val_sec = (
            mod.ALPACA_SECRET_KEY.get_secret_value()
            if hasattr(mod.ALPACA_SECRET_KEY, "get_secret_value")
            else mod.ALPACA_SECRET_KEY
        )
        assert val_sec in (None, "")


def test_oss_cloud_live_keeps_alpaca_api_key_bora():
    with _load_oss(
        DEPLOYMENT_MODE="CLOUD",
        PAPER_TRADING="false",
        ALPACA_API_KEY="cloud_live_k",
        ALPACA_SECRET_KEY="cloud_live_s",
    ) as mod:
        assert mod.ALPACA_API_KEY.get_secret_value() == "cloud_live_k"
        assert mod.ALPACA_API_KEY.get_secret_value() == mod.API_KEY.get_secret_value()
