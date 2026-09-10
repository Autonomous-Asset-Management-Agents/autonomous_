"""#2452 (CRITICAL) BORA: on a desktop LIVE boot the engine's Alpaca client must
use the LIVE keys.

The live-arm bug: the OSS ``API_KEY``/``BASE_URL`` globals are the *paper source*
(``force_paper_trading`` resets the active account from them), while the swapped
active account lives in ``ALPACA_API_KEY``/``ALPACA_SECRET_KEY``/``ALPACA_BASE_URL``.
``_init_trading_clients`` read the paper-source ``API_KEY`` against the LIVE
``BASE_URL`` → the PAPER key hit the live endpoint → ``self.api = None`` → live could
never start. Enterprise resolves ``API_KEY -> ALPACA_API_KEY`` via ``__getattr__``,
so the active-alias read is edition-neutral.

Two layers pinned:
  1. config.oss.py: the ACTIVE alias (ALPACA_*) reflects the live account on a live
     boot, while API_KEY stays the (immutable) paper source.
  2. api_routes._init_trading_clients: builds the TradingClient from the ACTIVE alias.
"""

from __future__ import annotations

import importlib.util
import os
from unittest.mock import MagicMock, patch

_HERE = os.path.dirname(__file__)
_OSS_CONFIG_PATH = os.path.abspath(
    os.path.join(_HERE, os.pardir, os.pardir, "config.oss.py")
)

# A REAL desktop live boot carries HITL_ENABLED=true (the shell sets it, INC-1); without it
# the Art-14 boot gate fail-safes back to paper — include it so we exercise the live path.
_LIVE_ENV = {
    "DEPLOYMENT_MODE": "LOCAL",
    "PAPER_TRADING": "false",
    "HITL_ENABLED": "true",
    "ALPACA_API_KEY": "pk-PAPER",
    "ALPACA_SECRET_KEY": "sk-PAPER",
    "ALPACA_LIVE_API_KEY": "pk-LIVE",
    "ALPACA_LIVE_SECRET_KEY": "sk-LIVE",
}
_PAPER_ENV = {**_LIVE_ENV, "PAPER_TRADING": "true"}


def _load_with_env(env: dict):
    keys = set(env) | {"ALPACA_BASE_URL"}
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(env)
        spec = importlib.util.spec_from_file_location(
            "config_oss_livekey", _OSS_CONFIG_PATH
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _plain(v):
    return v.get_secret_value() if hasattr(v, "get_secret_value") else v


# ── Layer 1: config.oss.py active alias reflects the live account ──────────────
def test_live_boot_active_alias_is_live():
    mod = _load_with_env(_LIVE_ENV)
    assert (
        mod.PAPER_TRADING is False
    ), "live boot with HITL should stay live (no fail-safe downgrade)"
    # The ACTIVE account the client must use:
    assert _plain(mod.ALPACA_API_KEY) == "pk-LIVE"
    assert _plain(mod.ALPACA_SECRET_KEY) == "sk-LIVE"
    assert mod.ALPACA_BASE_URL == "https://api.alpaca.markets"
    # API_KEY stays the immutable PAPER source (force_paper_trading's reset origin).
    assert _plain(mod.API_KEY) == "pk-PAPER"


def test_paper_boot_active_alias_is_paper():
    mod = _load_with_env(_PAPER_ENV)
    assert _plain(mod.ALPACA_API_KEY) == "pk-PAPER"
    assert mod.ALPACA_BASE_URL == "https://paper-api.alpaca.markets"


# ── Layer 2: the engine's client builder uses the ACTIVE alias ────────────────
def test_init_trading_clients_uses_active_live_alias():
    """_init_trading_clients must build the TradingClient from ALPACA_API_KEY
    (live) — not the paper-source API_KEY — so a live boot connects live."""
    from core.engine import api_routes as ar

    fake_account = MagicMock(status="ACTIVE", equity="1000")
    made = {}

    def _fake_client(key, secret, paper=True):
        made["key"], made["secret"], made["paper"] = key, secret, paper
        c = MagicMock()
        c.get_account.return_value = fake_account
        return c

    with patch.object(
        ar.config, "ALPACA_API_KEY", "pk-LIVE", create=True
    ), patch.object(
        ar.config, "ALPACA_SECRET_KEY", "sk-LIVE", create=True
    ), patch.object(
        ar.config, "ALPACA_BASE_URL", "https://api.alpaca.markets", create=True
    ), patch.object(
        ar.config, "API_KEY", "pk-PAPER", create=True
    ), patch.object(
        ar.config, "get_secret_str", lambda v: v
    ), patch.object(
        ar, "create_trading_client", _fake_client
    ), patch.object(
        ar, "create_data_client", lambda *a, **k: MagicMock()
    ), patch.object(
        ar, "BotEngine", lambda **k: MagicMock()
    ):
        ar._init_trading_clients()

    assert (
        made["key"] == "pk-LIVE"
    ), "the engine must connect with the LIVE key, not the paper source"
    assert made["paper"] is False, "live BASE_URL → paper=False"
