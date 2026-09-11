"""BORA parity (audit #12): the paper⇄live credential swap on the edition the DESKTOP actually runs.

test_live_credentials.py exercises ``config.RuntimeConfigState`` — the Pydantic model in config.py
(the cloud/dev edition). But the shipped desktop runs config.oss.py, where the swap is a one-shot
module-level block (config.oss.py:104-111) that runs once at import, NOT the Pydantic validator. The
two are "mirrors" only by convention; nothing tests that the config.oss.py swap actually selects the
live slot, nor that its None-key fail-closed path behaves. A drift between the two editions in this
exact block would ship undetected.

These tests load config.oss.py in isolation (importlib, as test_live_entitlement_downgrade.py does)
and mirror the four config.py cases against the OSS module the desktop runs — closing the BORA gap.
They are GREEN today (documenting current OSS behaviour) and go RED the moment the desktop swap
drifts from the cloud contract.
"""

from __future__ import annotations

import importlib.util
import os
from unittest.mock import patch

_PAPER_URL = "https://paper-api.alpaca.markets"
_LIVE_URL = "https://api.alpaca.markets"


def _load_oss(**env):
    """Import config.oss.py fresh with a controlled environment so the module-level credential swap
    runs against exactly these inputs. Not registered in sys.modules — each call is independent.
    """
    import config as config_full

    oss_path = os.path.join(os.path.dirname(config_full.__file__), "config.oss.py")
    base = {
        "ALPACA_API_KEY": "",
        "ALPACA_SECRET_KEY": "",
        "ALPACA_LIVE_API_KEY": "",
        "ALPACA_LIVE_SECRET_KEY": "",
        "GEMINI_API_KEY": "",  # skip the import-time genai client
        "HITL_ENABLED": "true",  # keep the Art-14 boot gate from raising on a live import
    }
    base.update(env)
    spec = importlib.util.spec_from_file_location("config_oss_cred_parity", oss_path)
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, base, clear=False):
        # BASE_URL only follows PAPER_TRADING when ALPACA_BASE_URL is unset (config.oss.py:85) — make
        # sure a stray override from the ambient env does not fix the endpoint.
        os.environ.pop("ALPACA_BASE_URL", None)
        spec.loader.exec_module(mod)
    return mod


def test_oss_paper_account_keeps_paper_keys():
    mod = _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="true",
        ALPACA_API_KEY="paper_k",
        ALPACA_LIVE_API_KEY="live_k",
    )
    assert mod.ALPACA_API_KEY == "paper_k"
    assert mod.ALPACA_API_KEY == mod.API_KEY  # the paper slot, never the live key
    assert mod.ALPACA_BASE_URL == _PAPER_URL


def test_oss_live_account_swaps_to_live_keys():
    mod = _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
        ALPACA_LIVE_API_KEY="live_k",
        ALPACA_LIVE_SECRET_KEY="live_s",
    )
    assert mod.ALPACA_API_KEY == "live_k"
    assert mod.ALPACA_SECRET_KEY == "live_s"
    assert mod.ALPACA_API_KEY == mod.ALPACA_LIVE_API_KEY
    assert mod.ALPACA_BASE_URL == _LIVE_URL


def test_oss_live_without_live_keys_fails_closed():
    # Desktop live mode but no live keys saved → None: never the paper key, never live on a paper key
    # (downstream this feeds str(None) to the live endpoint → 401 → degrade, the intended fail-closed).
    mod = _load_oss(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
    )
    assert mod.ALPACA_API_KEY is None
    assert mod.ALPACA_SECRET_KEY is None


def test_oss_cloud_live_keeps_alpaca_api_key_bora():
    # BORA: the swap is gated on DEPLOYMENT_MODE=LOCAL. On the byte-identical cloud engine (no
    # ALPACA_LIVE_* slots, GCP-managed) it must NOT fire — the single ALPACA_API_KEY stays untouched.
    mod = _load_oss(
        DEPLOYMENT_MODE="CLOUD",
        PAPER_TRADING="false",
        ALPACA_API_KEY="cloud_live_k",
        ALPACA_SECRET_KEY="cloud_live_s",
    )
    assert mod.ALPACA_API_KEY == "cloud_live_k"
    assert (
        mod.ALPACA_API_KEY == mod.API_KEY
    )  # swap skipped → paper-slot alias == the cloud key
