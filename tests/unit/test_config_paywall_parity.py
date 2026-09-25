import os
import sys

from settings import RuntimeConfigState


def _load_oss_config():
    import importlib.util

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    oss_path = os.path.join(repo_root, "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_paywall", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_enterprise_defaults():
    cfg = RuntimeConfigState()
    assert cfg.PAYWALL_ENABLED is False
    assert cfg.LEMONSQUEEZY_STORE_ID == ""
    assert cfg.LEMONSQUEEZY_VARIANT_ID_PRO == ""
    assert cfg.LEMONSQUEEZY_CHECKOUT_URL == ""
    assert cfg.LEMONSQUEEZY_LICENSE_VALID_DAYS == 365
    assert cfg.LEMONSQUEEZY_PRICE_DISPLAY == "9,99 \u20ac"


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
    assert cfg.LEMONSQUEEZY_PRICE_DISPLAY == "9,99 \u20ac"
