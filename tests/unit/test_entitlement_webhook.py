# tests/unit/test_entitlement_webhook.py
# GTM-1 (#1840) — Brick 2: Stripe webhook -> mint -> persist. TDD RED first.
#
# The webhook is the ONLY unauthenticated entitlement path: Stripe cannot send the engine
# key, so authentication is EXCLUSIVELY the Stripe signature (construct_event). A missing or
# invalid signature must fail-closed (no mint, no row). `stripe` is a cloud-only dependency
# and is NOT installed in this env, so it is lazy-imported inside the handler and MOCKED here
# via sys.modules. `mint_tier_token` is mocked so no signing key / Secret Manager is touched.
#
# BORA: every persistence assertion runs on an in-memory SQLite engine built from the ORM
# (Base.metadata.create_all) — no Docker, no Postgres, no Alembic execution. Each test body is
# a single ``asyncio.run`` coroutine so the StaticPool :memory: connection lives for its span
# (Python 3.14 no longer auto-creates a loop for get_event_loop()).

import asyncio
import sys
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# Import the DB session module at collection time — BEFORE the autouse fixture sets K_SERVICE.
# session.py builds its `engine` singleton at import and, on Cloud Run (K_SERVICE) without a
# DATABASE_URL, fail-closes to guard against silent audit loss. The handler patches
# AsyncSessionLocal with an in-memory factory, so this real (off-cloud) engine is never used.
import core.database.session  # noqa: E402,F401
from core.database.models import Base, EntitlementToken


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
async def _make_sqlite_factory():
    """Build an in-memory SQLite engine + session factory with the ORM schema created.

    StaticPool keeps a single shared connection so the :memory: DB survives across sessions
    within one event loop (mirrors test_iron_dome_audit_mirror.py)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _rows(factory):
    async with factory() as s:
        return (await s.execute(sa.select(EntitlementToken))).scalars().all()


def _fake_event(tier="PRO", session_id="cs_test_123", customer="cus_abc"):
    """A minimal stand-in for a Stripe checkout.session.completed Event object."""
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": session_id,
                "customer": customer,
                "metadata": {"tier": tier},
            }
        },
    }


def _install_fake_stripe():
    """Install a fake ``stripe`` module so the handler's lazy `import stripe` resolves.

    Returns the fake module; the caller wires ``Webhook.construct_event`` per-test."""
    fake = MagicMock(name="stripe")
    fake.error = MagicMock()
    fake.error.SignatureVerificationError = type(
        "SignatureVerificationError", (Exception,), {}
    )
    sys.modules["stripe"] = fake
    return fake


# --------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _cloud_env(monkeypatch):
    """Default: pretend we are on Cloud Run so the cloud-only guard passes.

    The off-cloud test overrides this by deleting K_SERVICE."""
    monkeypatch.setenv("K_SERVICE", "aaa-backend")


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    """Stub the webhook-secret loader so no Secret Manager / GCP call happens."""
    monkeypatch.setattr(
        "core.entitlement.payment._load_webhook_secret",
        lambda: "whsec_test",
        raising=False,
    )


# --------------------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------------------
def test_valid_signature_mints_and_persists_a_row():
    """A valid signature + checkout.session.completed mints exactly once and persists a row."""
    fake = _install_fake_stripe()
    fake.Webhook.construct_event.return_value = _fake_event(
        tier="PRO", session_id="cs_test_valid"
    )

    from core.entitlement import payment

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            payment, "mint_tier_token", return_value="TOKEN.ABC"
        ) as mint:
            result = await payment.handle_stripe_webhook(
                b'{"raw":"body"}', "sig_header_value"
            )
            rows = await _rows(factory)
        await engine.dispose()
        return result, mint, rows

    result, mint, rows = asyncio.run(run())

    assert result["status"] == "minted"
    mint.assert_called_once()
    assert mint.call_args.kwargs["tier"].value == "PRO"  # Tier enum passed through

    assert len(rows) == 1
    row = rows[0]
    assert row.tier == "PRO"
    assert row.token == "TOKEN.ABC"
    assert row.stripe_session_id == "cs_test_valid"
    assert row.issued_to_hash  # non-empty hash of the customer/session
    assert row.expires_at is not None


def test_invalid_signature_returns_400_no_mint_no_row():
    """An invalid signature fails closed: HTTP 400, no mint, no persisted row."""
    from fastapi import HTTPException

    fake = _install_fake_stripe()
    fake.Webhook.construct_event.side_effect = fake.error.SignatureVerificationError(
        "bad sig"
    )

    from core.entitlement import payment

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            payment, "mint_tier_token"
        ) as mint:
            with pytest.raises(HTTPException) as exc:
                await payment.handle_stripe_webhook(b'{"raw":"body"}', "bad_sig")
            rows = await _rows(factory)
        await engine.dispose()
        return exc.value, mint, rows

    err, mint, rows = asyncio.run(run())
    assert err.status_code == 400
    mint.assert_not_called()
    assert rows == []


def test_missing_signature_header_returns_400():
    """A missing Stripe-Signature header (None) fails closed with 400 before any mint."""
    from fastapi import HTTPException

    fake = _install_fake_stripe()
    fake.Webhook.construct_event.side_effect = fake.error.SignatureVerificationError(
        "no sig"
    )

    from core.entitlement import payment

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            payment, "mint_tier_token"
        ) as mint:
            with pytest.raises(HTTPException) as exc:
                await payment.handle_stripe_webhook(b'{"raw":"body"}', None)
        await engine.dispose()
        return exc.value, mint

    err, mint = asyncio.run(run())
    assert err.status_code == 400
    mint.assert_not_called()


def test_idempotent_duplicate_session_mints_once_one_row():
    """Stripe retries: the same stripe_session_id delivered twice -> one mint, one row."""
    fake = _install_fake_stripe()
    fake.Webhook.construct_event.return_value = _fake_event(
        tier="PROFESSIONAL", session_id="cs_dupe"
    )

    from core.entitlement import payment

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            payment, "mint_tier_token", return_value="TOK.DUP"
        ) as mint:
            first = await payment.handle_stripe_webhook(b"{}", "sig")
            second = await payment.handle_stripe_webhook(b"{}", "sig")
            rows = await _rows(factory)
        await engine.dispose()
        return first, second, mint, rows

    first, second, mint, rows = asyncio.run(run())
    assert first["status"] == "minted"
    assert second["status"] == "duplicate"  # second delivery is a no-op
    mint.assert_called_once()  # minted exactly once
    assert len(rows) == 1  # exactly ONE row despite two deliveries


def test_require_cloud_guard_raises_off_cloud(monkeypatch):
    """Off Cloud Run (K_SERVICE unset) the handler refuses before touching Stripe."""
    monkeypatch.delenv("K_SERVICE", raising=False)
    _install_fake_stripe()

    from core.entitlement import payment

    async def run():
        with patch.object(payment, "mint_tier_token") as mint:
            with pytest.raises(RuntimeError):
                await payment.handle_stripe_webhook(b"{}", "sig")
            return mint

    mint = asyncio.run(run())
    mint.assert_not_called()


def test_raw_body_is_passed_to_construct_event_unmodified():
    """construct_event must receive the EXACT raw bytes (re-serialised JSON breaks the sig)."""
    fake = _install_fake_stripe()
    fake.Webhook.construct_event.return_value = _fake_event(session_id="cs_raw")
    raw = b'{"id":"evt_1","spaced":  "value","order":[3,1,2]}'  # non-canonical JSON

    from core.entitlement import payment

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            payment, "mint_tier_token", return_value="T"
        ):
            await payment.handle_stripe_webhook(raw, "sig_x")
        await engine.dispose()
        return fake.Webhook.construct_event.call_args

    call = asyncio.run(run())
    # First positional arg to construct_event is the untouched raw payload bytes.
    passed_payload = call.args[0] if call.args else call.kwargs.get("payload")
    passed_sig = call.args[1] if len(call.args) > 1 else call.kwargs.get("sig_header")
    assert passed_payload == raw  # exact bytes, not re-serialised
    assert isinstance(passed_payload, (bytes, bytearray))
    assert passed_sig == "sig_x"
