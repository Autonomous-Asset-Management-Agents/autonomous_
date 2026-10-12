# tests/unit/test_entitlement_lemonsqueezy.py
# GTM-2 (#1809) T7: Lemon Squeezy webhook -> mint OUR offline Ed25519 token -> persist,
# and T6 lookup by order identifier for the success page. TDD RED first.
#
# The webhook is unauthenticated by engine key (LS cannot send one): authentication is
# EXCLUSIVELY the X-Signature HMAC-SHA256 over the RAW body (config: signing secret). A
# missing/invalid signature fails closed (HTTP 400, no mint, no row). mint_tier_token is
# mocked so no signing key / Secret Manager is touched. The whole path is CLOUD-ONLY.
#
# BORA: persistence runs on an in-memory SQLite engine (Base.metadata.create_all) — no
# Docker/Postgres/Alembic. Each test body is one asyncio.run coroutine (StaticPool :memory:).

import asyncio
import hashlib
import hmac
import json
import sys
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import core.database.session  # noqa: E402,F401  (import before K_SERVICE is set)
from core.database.models import Base, LemonSqueezyLicense

_SECRET = "ls_whsec_test"


async def _make_sqlite_factory():
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
        return (await s.execute(sa.select(LemonSqueezyLicense))).scalars().all()


def _sign(body: bytes) -> str:
    return hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _order_body(
    identifier="ord-uuid-1", event="order_created", email="buyer@example.com"
) -> bytes:
    return json.dumps(
        {
            "meta": {"event_name": event},
            "data": {
                "id": "42",
                "attributes": {"identifier": identifier, "user_email": email},
            },
        }
    ).encode("utf-8")


@pytest.fixture(autouse=True)
def _cloud_env(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "aaa-backend")


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(
        "core.entitlement.lemonsqueezy._load_webhook_secret",
        lambda: _SECRET,
        raising=False,
    )


# --------------------------------------------------------------------------- #
# Signature verification (pure)
# --------------------------------------------------------------------------- #
def test_verify_signature_accepts_valid_rejects_tampered_and_missing():
    from core.entitlement import lemonsqueezy as ls

    body = _order_body()
    assert ls.verify_ls_signature(body, _sign(body), _SECRET) is True
    assert ls.verify_ls_signature(body, "deadbeef", _SECRET) is False
    assert ls.verify_ls_signature(body, None, _SECRET) is False


# --------------------------------------------------------------------------- #
# Webhook mint + persist
# --------------------------------------------------------------------------- #
def test_valid_signature_mints_pro_and_persists_row():
    from core.entitlement import lemonsqueezy as ls

    body = _order_body(identifier="ord-valid")

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            ls, "mint_tier_token", return_value="LS.TOKEN"
        ) as mint:
            result = await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            rows = await _rows(factory)
        await engine.dispose()
        return result, mint, rows

    result, mint, rows = asyncio.run(run())
    assert result["status"] == "minted"
    mint.assert_called_once()
    assert mint.call_args.kwargs["tier"].value == "PRO"
    assert mint.call_args.kwargs["valid_days"] == 365  # annual (owner decision)
    assert len(rows) == 1
    assert rows[0].order_identifier == "ord-valid"
    assert rows[0].token == "LS.TOKEN"
    assert rows[0].tier == "PRO"
    assert rows[0].issued_to_hash
    assert rows[0].expires_at is not None


def test_invalid_signature_returns_400_no_mint_no_row():
    from fastapi import HTTPException

    from core.entitlement import lemonsqueezy as ls

    body = _order_body()

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            ls, "mint_tier_token"
        ) as mint:
            with pytest.raises(HTTPException) as exc:
                await ls.handle_lemonsqueezy_webhook(body, "bad_sig")
            rows = await _rows(factory)
        await engine.dispose()
        return exc.value, mint, rows

    err, mint, rows = asyncio.run(run())
    assert err.status_code == 400
    mint.assert_not_called()
    assert rows == []


def test_idempotent_duplicate_identifier_mints_once():
    from core.entitlement import lemonsqueezy as ls

    body = _order_body(identifier="ord-dupe")

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            ls, "mint_tier_token", return_value="T.DUP"
        ) as mint:
            first = await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            second = await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            rows = await _rows(factory)
        await engine.dispose()
        return first, second, mint, rows

    first, second, mint, rows = asyncio.run(run())
    assert first["status"] == "minted"
    assert second["status"] == "duplicate"
    mint.assert_called_once()
    assert len(rows) == 1


def test_unhandled_event_is_ignored_no_mint():
    from core.entitlement import lemonsqueezy as ls

    body = _order_body(event="subscription_updated")

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            ls, "mint_tier_token"
        ) as mint:
            result = await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            rows = await _rows(factory)
        await engine.dispose()
        return result, mint, rows

    result, mint, rows = asyncio.run(run())
    assert result["status"] == "ignored"
    mint.assert_not_called()
    assert rows == []


def test_require_cloud_guard_raises_off_cloud(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    from core.entitlement import lemonsqueezy as ls

    body = _order_body()

    async def run():
        with patch.object(ls, "mint_tier_token") as mint:
            with pytest.raises(RuntimeError):
                await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            return mint

    mint = asyncio.run(run())
    mint.assert_not_called()


# --------------------------------------------------------------------------- #
# T6 lookup for the success page
# --------------------------------------------------------------------------- #
def test_lookup_returns_token_for_known_identifier_else_none():
    from core.entitlement import lemonsqueezy as ls

    body = _order_body(identifier="ord-look")

    async def run():
        engine, factory = await _make_sqlite_factory()
        with patch("core.database.session.AsyncSessionLocal", factory), patch.object(
            ls, "mint_tier_token", return_value="LOOK.TOKEN"
        ):
            await ls.handle_lemonsqueezy_webhook(body, _sign(body))
            hit = await ls.lookup_license("ord-look")
            miss = await ls.lookup_license("ord-does-not-exist")
        await engine.dispose()
        return hit, miss

    hit, miss = asyncio.run(run())
    assert hit == {"tier": "PRO", "token": "LOOK.TOKEN"}
    assert miss is None
