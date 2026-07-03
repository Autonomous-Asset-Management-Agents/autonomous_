"""#1425 (LIVE-1 T2): paper and live Alpaca credentials are stored + selected
independently on the DESKTOP/OSS edition. config.RuntimeConfigState swaps the
active Alpaca keys to the SEPARATE live slots when PAPER_TRADING is off — never
the paper keys, no fallback. BORA: gated to DEPLOYMENT_MODE=LOCAL so the
byte-identical cloud engine (GCP secrets, no live slots) is untouched.
"""

import os
from unittest import mock


def _build(**env):
    import config

    with mock.patch.dict(os.environ, env):
        return config.RuntimeConfigState()


def test_live_slots_are_managed_keys():
    from core.keychain import MANAGED_KEYS

    assert "ALPACA_LIVE_API_KEY" in MANAGED_KEYS
    assert "ALPACA_LIVE_SECRET_KEY" in MANAGED_KEYS


def test_paper_account_keeps_paper_keys():
    s = _build(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="true",
        ALPACA_API_KEY="paper_k",
        ALPACA_LIVE_API_KEY="live_k",
    )
    assert s.ALPACA_API_KEY.get_secret_value() == "paper_k"
    assert s.ALPACA_BASE_URL == "https://paper-api.alpaca.markets"


def test_live_account_swaps_to_live_keys():
    s = _build(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
        ALPACA_LIVE_API_KEY="live_k",
        ALPACA_LIVE_SECRET_KEY="live_s",
    )
    assert s.ALPACA_API_KEY.get_secret_value() == "live_k"
    assert s.ALPACA_SECRET_KEY.get_secret_value() == "live_s"
    assert s.ALPACA_BASE_URL == "https://api.alpaca.markets"


def test_live_without_live_keys_fails_closed():
    # Desktop live mode but no live keys saved → None: never the paper key, never live on a paper key.
    s = _build(
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="false",
        ALPACA_API_KEY="paper_k",
        ALPACA_SECRET_KEY="paper_s",
    )
    assert s.ALPACA_API_KEY is None
    assert s.ALPACA_SECRET_KEY is None


def test_cloud_live_keeps_alpaca_api_key_bora():
    # BORA: the byte-identical cloud engine has NO ALPACA_LIVE_* slots (GCP-managed). The swap must
    # NOT fire there — the single ALPACA_API_KEY (the cloud's live key) stays untouched.
    s = _build(
        DEPLOYMENT_MODE="CLOUD",
        PAPER_TRADING="false",
        ALPACA_API_KEY="cloud_live_k",
        ALPACA_SECRET_KEY="cloud_live_s",
    )
    assert s.ALPACA_API_KEY.get_secret_value() == "cloud_live_k"
    assert s.ALPACA_SECRET_KEY.get_secret_value() == "cloud_live_s"
