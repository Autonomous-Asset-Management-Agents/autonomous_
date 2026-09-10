import asyncio
from unittest.mock import patch

import pytest

from scripts.shadow_boot import run_shadow_boot


def test_shadow_boot_success():
    """Tier 0: Tests the happy path where all components are healthy and reachable."""
    with patch("scripts.shadow_boot._check_redis", return_value=True), patch(
        "scripts.shadow_boot._check_alpaca", return_value=True
    ), patch("scripts.shadow_boot._check_llm", return_value=True):

        result = asyncio.run(run_shadow_boot())
        assert result is True


def test_shadow_boot_redis_timeout():
    """Tier 0: Tests that a timeout exception correctly fails the shadow boot."""

    async def timeout_coro():
        # Raise standard asyncio timeout to verify error handling
        raise asyncio.TimeoutError()

    with patch("scripts.shadow_boot._check_redis", side_effect=timeout_coro), patch(
        "scripts.shadow_boot._check_alpaca", return_value=True
    ), patch("scripts.shadow_boot._check_llm", return_value=True):

        result = asyncio.run(run_shadow_boot())
        assert result is False


def test_shadow_boot_alpaca_auth_fail():
    """Tier 0: Tests that an authorization failure (HTTP 401) correctly fails the shadow boot."""
    with patch("scripts.shadow_boot._check_redis", return_value=True), patch(
        "scripts.shadow_boot._check_alpaca", return_value=False
    ), patch("scripts.shadow_boot._check_llm", return_value=True):

        result = asyncio.run(run_shadow_boot())
        assert result is False
