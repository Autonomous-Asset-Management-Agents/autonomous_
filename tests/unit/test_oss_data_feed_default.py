"""OSS config: ALPACA_DATA_FEED defaults to 'iex' regardless of PAPER_TRADING.

The OSS desktop edition (config.oss.py) trades the operator's own capital —
MiFID II Art. 27 SIP/NBBO is N/A (see live_trading_guard.py #1424). The data
feed MUST default to 'iex' (the free Alpaca tier) for BOTH paper AND live so
a live switch never breaks on a missing SIP subscription. The Cloud/Enterprise
config.py keeps 'sip' as its live default (byte-identical, Fremdkapital).

Regression: prior to this fix, config.oss.py defaulted to 'sip' when
PAPER_TRADING=False, causing /health/deep 500 → silent paper rollback
(9 operator attempts over 3 days with no feedback).
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# The desktop snapshot renames config.oss.py → config.py, but in the repo both
# exist and config.py is the Enterprise Pydantic edition. Import config.oss
# directly (as a dotted module name) to test the OSS flat-module behaviour.
_OSS_MOD = "config.oss"  # importlib uses this as the module name


def _reload_oss_config(**env_overrides):
    """Force-reload config.oss.py with the given env vars."""
    # Purge the cached module so import picks up fresh env
    for k in list(sys.modules):
        if "config.oss" in k or k == "config_oss":
            del sys.modules[k]

    # Also ensure no leftover ALPACA_DATA_FEED from a previous test
    env = dict(os.environ)
    env.pop("ALPACA_DATA_FEED", None)
    env.update(env_overrides)

    with patch.dict(os.environ, env, clear=False):
        # importlib.util to load by path (config.oss.py is not a package)
        spec = importlib.util.spec_from_file_location(
            "config_oss_test",
            str(Path(_ROOT) / "config.oss.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


class TestOssDataFeedDefault:
    """config.oss.py ALPACA_DATA_FEED must default to 'iex' for both modes."""

    def test_paper_mode_defaults_to_iex(self):
        cfg = _reload_oss_config(PAPER_TRADING="true", DEPLOYMENT_MODE="LOCAL")
        assert (
            cfg.ALPACA_DATA_FEED == "iex"
        ), f"Paper mode must default to 'iex', got {cfg.ALPACA_DATA_FEED!r}"

    def test_live_mode_defaults_to_iex(self):
        """Key regression test: PAPER_TRADING=false must NOT auto-switch to 'sip'."""
        cfg = _reload_oss_config(PAPER_TRADING="false", DEPLOYMENT_MODE="LOCAL")
        assert cfg.ALPACA_DATA_FEED == "iex", (
            f"OSS live mode must default to 'iex' (SIP requires paid plan), "
            f"got {cfg.ALPACA_DATA_FEED!r}"
        )

    def test_explicit_sip_override_honoured(self):
        """An explicit ALPACA_DATA_FEED=sip env var must still be respected."""
        cfg = _reload_oss_config(
            PAPER_TRADING="false", DEPLOYMENT_MODE="LOCAL", ALPACA_DATA_FEED="sip"
        )
        assert (
            cfg.ALPACA_DATA_FEED == "sip"
        ), "Explicit ALPACA_DATA_FEED=sip must be honoured"
