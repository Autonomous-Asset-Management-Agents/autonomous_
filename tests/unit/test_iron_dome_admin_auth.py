# ADR-SEC-06 (#1583) · sub-issue #1595 — admin-endpoint auth. TDD RED first.
# The OSS write-path posture (SEC-01 from the ADR review): loopback/private IPs are
# accepted (so the Docker-bridge reverse proxy works), public/external IPs are rejected,
# and a configured IRON_DOME_ADMIN_TOKEN is required (fail-closed when unset).

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from core.governance.iron_dome_admin_auth import (
    ip_is_allowed,
    require_iron_dome_admin,
    token_is_valid,
)


def test_loopback_ip_allowed():
    assert ip_is_allowed("127.0.0.1") is True


def test_ipv6_loopback_allowed():
    assert ip_is_allowed("::1") is True


def test_private_proxy_ip_allowed():
    # Docker-bridge proxy IP — must be allowed (proxy-safe; a strict 127.0.0.1 check
    # would wrongly block the console request in containerized setups).
    assert ip_is_allowed("172.18.0.5") is True


def test_public_ip_rejected():
    assert ip_is_allowed("8.8.8.8") is False


def test_empty_host_rejected():
    assert ip_is_allowed("") is False


def test_malformed_host_rejected():
    assert ip_is_allowed("not-an-ip") is False


def test_token_valid_when_matches():
    assert token_is_valid("s3cret", "s3cret") is True


def test_token_invalid_when_mismatch():
    assert token_is_valid("wrong", "s3cret") is False


def test_token_invalid_when_missing():
    assert token_is_valid(None, "s3cret") is False


def test_token_invalid_when_not_configured():
    # Fail-closed: a request can never be valid if no admin token is configured.
    assert token_is_valid("anything", "") is False


@pytest.mark.vc4
def test_require_iron_dome_admin_raises_501_when_token_unset():
    with patch.dict(os.environ, {"IRON_DOME_ADMIN_TOKEN": ""}):
        with pytest.raises(HTTPException) as exc_info:
            require_iron_dome_admin(request=MagicMock())
        assert exc_info.value.status_code == 501
        assert "Desktop edition single-tenant posture" in exc_info.value.detail
