"""LSR R2 (#2253): shadow-boot terminal-vs-transient classification.

``run_shadow_boot`` is enriched to a structured ``ShadowBootResult(ok, failures:[CheckFailure(
name, reason, terminal)])`` so the boot-degrade branch can decide whether to write a compensating
WORM ``disable`` (terminal) or keep the arm and retry next boot (transient). A thin bool wrapper
``run_shadow_boot() -> bool`` keeps cloud/existing callers byte-unaffected.

Classification: Alpaca 401/403 on live creds = TERMINAL (rotated/invalid key, heals never);
Alpaca 429/5xx/timeout/network, Redis timeout, LLM-unreachable-while-live (#11 warming) = TRANSIENT.
Bias → transient (mis-flagging terminal→transient stays PAPER every boot; transient→terminal would
revoke live on a blip).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.shadow_boot as sb  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ── structured result + bool back-compat ─────────────────────────────────────


def test_all_ok_result_has_no_failures():
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=True)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        res = _run(sb.run_shadow_boot_result())
    assert res.ok is True
    assert res.failures == []


def test_bool_wrapper_backcompat_true_and_false():
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=True)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        assert _run(sb.run_shadow_boot()) is True
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=False)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        assert _run(sb.run_shadow_boot()) is False


def test_terminal_alpaca_marks_failure_terminal():
    outcome = sb.CheckOutcome(
        ok=False, terminal=True, reason="Alpaca auth rejected (HTTP 401)"
    )
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=outcome)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        res = _run(sb.run_shadow_boot_result())
    assert res.ok is False
    assert any(f.terminal for f in res.failures)
    alpaca = [f for f in res.failures if f.name == "Alpaca"][0]
    assert alpaca.terminal is True
    assert "401" in alpaca.reason


def test_transient_llm_marks_failure_transient():
    outcome = sb.CheckOutcome(
        ok=False, terminal=False, reason="Ollama unreachable (warming)"
    )
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=True)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=outcome)):
        res = _run(sb.run_shadow_boot_result())
    assert res.ok is False
    assert any(f.name == "LLM" for f in res.failures)
    assert not any(f.terminal for f in res.failures)


def test_timeout_is_transient():
    async def _hang():
        raise asyncio.TimeoutError()

    with patch.object(sb, "_check_redis", AsyncMock(side_effect=_hang)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=True)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        res = _run(sb.run_shadow_boot_result())
    assert res.ok is False
    redis = [f for f in res.failures if f.name == "Redis"][0]
    assert redis.terminal is False


def test_plain_bool_false_from_check_is_transient_by_default():
    """A legacy bool-False (e.g. a patched check) must degrade safely to a TRANSIENT failure."""
    with patch.object(sb, "_check_redis", AsyncMock(return_value=True)), patch.object(
        sb, "_check_alpaca", AsyncMock(return_value=False)
    ), patch.object(sb, "_check_llm", AsyncMock(return_value=True)):
        res = _run(sb.run_shadow_boot_result())
    assert res.ok is False
    assert not any(f.terminal for f in res.failures)  # bias → transient


# ── the REAL _check_alpaca classifies HTTP status ────────────────────────────


class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        if self._exc:
            raise self._exc
        return self._resp


def _patch_alpaca_env(monkeypatch):
    # Ensure the real _check_alpaca reads a concrete key/url (config attrs exist).
    pass


def test_check_alpaca_401_is_terminal():
    with patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(resp=_FakeResp(401, "unauthorized")),
    ):
        out = _run(sb._check_alpaca())
    assert out.ok is False and out.terminal is True


def test_check_alpaca_403_is_terminal():
    with patch(
        "httpx.AsyncClient", return_value=_FakeClient(resp=_FakeResp(403, "forbidden"))
    ):
        out = _run(sb._check_alpaca())
    assert out.ok is False and out.terminal is True


def test_check_alpaca_429_is_ok():
    with patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(resp=_FakeResp(429, "rate limited")),
    ):
        out = _run(sb._check_alpaca())
    assert bool(out.ok) is True


def test_check_alpaca_500_is_transient():
    with patch(
        "httpx.AsyncClient",
        return_value=_FakeClient(resp=_FakeResp(500, "server error")),
    ):
        out = _run(sb._check_alpaca())
    assert out.ok is False and out.terminal is False


def test_check_alpaca_network_error_is_transient():
    with patch(
        "httpx.AsyncClient", return_value=_FakeClient(exc=RuntimeError("conn reset"))
    ):
        out = _run(sb._check_alpaca())
    assert out.ok is False and out.terminal is False
