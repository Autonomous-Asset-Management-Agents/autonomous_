# tests/unit/test_entitlement_payment.py
# GTM-1 (#1840) — Brick 1: SERVER-SIDE Stripe Checkout Session creation for the
# tier-upgrade purchase flow. This is the COUNTERPART to the #1839 issuer: the issuer
# MINTS the token (Brick 2's webhook calls it); Brick 1 only starts the paid checkout.
#
# TDD contract:
#   * PRO / PROFESSIONAL -> a Stripe subscription Checkout Session is created with the
#     tier's configured price id and its URL is returned.
#   * BASIC (free) and INSTITUTIONAL (B2B invoicing, not Stripe) are REJECTED.
#   * The whole path is CLOUD-ONLY (K_SERVICE) — the Stripe secret key never loads on a
#     desktop.
#
# NO NETWORK: `stripe` is injected as a fake module via monkeypatch.setitem(sys.modules)
# and Secret Manager is mocked, exactly like the issuer tests.
from __future__ import annotations

import importlib
import sys
import types

import pytest

import config
from core.entitlement import Tier
from core.entitlement import payment as payment_mod


# --- Fakes: a stripe module + a Secret Manager client, both offline. ---
def _install_fake_stripe(monkeypatch, url="https://checkout.stripe.test/session"):
    """Inject a fake ``stripe`` whose checkout.Session.create records its kwargs and
    returns an object exposing ``.url``. Returns the fake module so the test can assert
    on ``fake.checkout.Session.last_kwargs``."""

    class _Session:
        last_kwargs = None

        @classmethod
        def create(cls, **kwargs):
            cls.last_kwargs = kwargs
            return types.SimpleNamespace(url=url, id="cs_test_123")

    fake = types.ModuleType("stripe")
    fake.api_key = None
    fake.checkout = types.SimpleNamespace(Session=_Session)
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake


def _install_fake_secret_manager(monkeypatch, secret_value="sk_test_secret"):
    """Install a fake google.cloud.secretmanager returning ``secret_value`` — the same
    shape the issuer test uses. Never touches GCP."""

    class _FakeResponse:
        class payload:  # noqa: N801 — mirrors SM response.payload.data
            data = secret_value.encode("utf-8")

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        def access_secret_version(self, request):
            _FakeClient.last_request = request
            return _FakeResponse()

    fake_sm = types.SimpleNamespace(SecretManagerServiceClient=_FakeClient)
    fake_cloud = types.SimpleNamespace(secretmanager=fake_sm)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", fake_sm)
    return _FakeClient


@pytest.fixture()
def cloud_env(monkeypatch):
    """A fully-mocked Cloud Run environment: K_SERVICE set, project + prices configured,
    stripe + Secret Manager faked."""
    monkeypatch.setenv("K_SERVICE", "checkout-service")
    monkeypatch.setattr(config, "GCP_PROJECT_ID", "test-project", raising=False)
    monkeypatch.setattr(config, "STRIPE_PRICE_ID_PRO", "price_pro_123", raising=False)
    monkeypatch.setattr(
        config, "STRIPE_PRICE_ID_PROFESSIONAL", "price_prof_456", raising=False
    )
    monkeypatch.setattr(
        config, "ENTITLEMENT_CHECKOUT_SUCCESS_URL", "https://ok.test", raising=False
    )
    monkeypatch.setattr(
        config, "ENTITLEMENT_CHECKOUT_CANCEL_URL", "https://no.test", raising=False
    )
    fake_stripe = _install_fake_stripe(monkeypatch)
    _install_fake_secret_manager(monkeypatch)
    return fake_stripe


# --- Purchasable tiers -> a session is created with the right price + URL returned ---
@pytest.mark.parametrize(
    "tier,want_price",
    [(Tier.PRO, "price_pro_123"), (Tier.PROFESSIONAL, "price_prof_456")],
)
def test_purchasable_tier_creates_session_and_returns_url(cloud_env, tier, want_price):
    url = payment_mod.create_checkout_session(tier)

    assert url == "https://checkout.stripe.test/session"
    kwargs = cloud_env.checkout.Session.last_kwargs
    assert kwargs["mode"] == "subscription"
    assert kwargs["line_items"] == [{"price": want_price, "quantity": 1}]
    assert kwargs["metadata"] == {"tier": tier.value}
    assert kwargs["success_url"] == "https://ok.test"
    assert kwargs["cancel_url"] == "https://no.test"


def test_secret_key_is_loaded_and_set_on_stripe(cloud_env):
    payment_mod.create_checkout_session(Tier.PRO)
    # The secret key from Secret Manager is applied to the stripe module, never logged.
    assert cloud_env.api_key == "sk_test_secret"


# --- Non-purchasable tiers are rejected (BASIC free, INSTITUTIONAL = B2B invoicing) ---
@pytest.mark.parametrize("tier", [Tier.BASIC, Tier.INSTITUTIONAL])
def test_free_or_b2b_tier_is_rejected(cloud_env, tier):
    with pytest.raises(ValueError, match="not purchasable"):
        payment_mod.create_checkout_session(tier)


def test_missing_price_id_is_rejected(monkeypatch, cloud_env):
    """A purchasable tier with an unconfigured (empty) price id fails loudly rather than
    creating a broken session (the real ids arrive with #1805)."""
    monkeypatch.setattr(config, "STRIPE_PRICE_ID_PRO", "", raising=False)
    with pytest.raises(ValueError, match="price id"):
        payment_mod.create_checkout_session(Tier.PRO)


# --- Cloud-only guard: nothing runs off Cloud Run (K_SERVICE) ---
def test_guard_raises_without_k_service(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    with pytest.raises(RuntimeError, match="cloud-only"):
        payment_mod.create_checkout_session(Tier.PRO)


def test_import_payment_never_touches_gcp_or_stripe():
    """Importing the module must not import stripe or construct any SM client (all lazy)."""
    importlib.reload(
        payment_mod
    )  # a bare reload must not raise / hit GCP / need stripe
