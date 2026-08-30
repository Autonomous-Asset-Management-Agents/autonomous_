"""GTM-2 (#1809): dual-edition config parity for the paywall master switch.

BORA: the paywall reactivation is toggled by a single build/env flag,
``PAYWALL_ENABLED`` (default OFF = the current free offline beta). It plus the
non-secret Lemon Squeezy (Merchant-of-Record) checkout config MUST exist in BOTH
editions with identical env names and identical safe defaults (False / "" / ""),
mirrored on the STRIPE_PRICE_ID_PRO idiom (config.py ↔ config.oss.py).

Secrets (LEMONSQUEEZY_API_KEY, LEMONSQUEEZY_WEBHOOK_SECRET) are intentionally NOT
config literals — they load from Secret Manager at request time, exactly like the
Stripe secret key. This test therefore asserts only the non-secret keys.
"""

import importlib.util
from pathlib import Path

from config import RuntimeConfigState

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_gtm2", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.PAYWALL_ENABLED is False
    assert cfg.LEMONSQUEEZY_STORE_ID == ""
    assert cfg.LEMONSQUEEZY_VARIANT_ID_PRO == ""
    assert cfg.LEMONSQUEEZY_CHECKOUT_URL == ""
    assert cfg.LEMONSQUEEZY_LICENSE_VALID_DAYS == 365  # annual (owner decision)
    assert cfg.LEMONSQUEEZY_PRICE_DISPLAY == "9,99 €"  # intro price (owner-confirmed)


def test_oss_defaults_match_enterprise():
    oss = _load_oss_config()
    ent = RuntimeConfigState()
    assert oss.PAYWALL_ENABLED == ent.PAYWALL_ENABLED
    assert oss.LEMONSQUEEZY_STORE_ID == ent.LEMONSQUEEZY_STORE_ID
    assert oss.LEMONSQUEEZY_VARIANT_ID_PRO == ent.LEMONSQUEEZY_VARIANT_ID_PRO
    assert oss.LEMONSQUEEZY_CHECKOUT_URL == ent.LEMONSQUEEZY_CHECKOUT_URL
    assert oss.LEMONSQUEEZY_LICENSE_VALID_DAYS == ent.LEMONSQUEEZY_LICENSE_VALID_DAYS
    assert oss.LEMONSQUEEZY_PRICE_DISPLAY == ent.LEMONSQUEEZY_PRICE_DISPLAY


def test_oss_get_config_exposes_keys():
    oss = _load_oss_config()
    cfg = oss.get_config()
    assert cfg.PAYWALL_ENABLED is False
    assert cfg.LEMONSQUEEZY_STORE_ID == ""
    assert cfg.LEMONSQUEEZY_VARIANT_ID_PRO == ""
    assert cfg.LEMONSQUEEZY_CHECKOUT_URL == ""
    assert cfg.LEMONSQUEEZY_LICENSE_VALID_DAYS == 365
    assert cfg.LEMONSQUEEZY_PRICE_DISPLAY == "9,99 €"
