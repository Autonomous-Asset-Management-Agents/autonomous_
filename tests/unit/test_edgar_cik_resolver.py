# tests/unit/test_edgar_cik_resolver.py
"""RQ-1 B1 (#1521): ticker->CIK resolver + 24h TTL cache.

resolve_cik is a pure sync dict lookup (safe to call from async); maybe_refresh does the
out-of-band network refresh with single-flight, 24h TTL, last-known-good on failure, and a
bundled snapshot as the offline cold-start floor. Unknown ticker -> None (fail-closed ->
degraded, never raises). (Epic #1516, Phase B.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.specialist import edgar_cik

_COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla, Inc."},
}


def _run(coro):
    return asyncio.run(coro)


def _fake_client(json_payload=None, status=200, raise_exc=None):
    """A patched httpx.AsyncClient factory: async context manager with an async .get()."""
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=json_payload or {})
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = (
        AsyncMock(side_effect=raise_exc) if raise_exc else AsyncMock(return_value=resp)
    )
    return MagicMock(return_value=client), client


@pytest.fixture(autouse=True)
def _reset_state():
    """The resolver cache is a module-level singleton — reset it around every test."""
    for attr, val in (
        ("_map", None),
        ("_loaded_at", None),
        ("_refresh_inflight", False),
    ):
        setattr(edgar_cik, attr, val)
    yield
    for attr, val in (
        ("_map", None),
        ("_loaded_at", None),
        ("_refresh_inflight", False),
    ):
        setattr(edgar_cik, attr, val)


def _seed(map_dict, *, age_hours=0.0):
    edgar_cik._map = dict(map_dict)
    edgar_cik._loaded_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)


class TestResolver:
    def test_resolve_cik_known_returns_zero_padded(self):
        _seed({"AAPL": "0000320193"})
        assert edgar_cik.resolve_cik("aapl") == "0000320193"  # case-insensitive
        assert len(edgar_cik.resolve_cik("AAPL")) == 10

    def test_resolve_cik_unknown_returns_none(self):
        _seed({"AAPL": "0000320193"})
        assert edgar_cik.resolve_cik("ZZZZ") is None
        assert edgar_cik.resolve_cik("SPY") is None

    def test_resolve_cik_serves_snapshot_offline(self):
        """Cold start (map None) loads the bundled snapshot — zero network."""
        factory, _ = _fake_client()
        with patch("core.specialist.edgar_cik.httpx.AsyncClient", factory):
            cik = edgar_cik.resolve_cik("AAPL")
        factory.assert_not_called()
        assert cik == "0000320193"  # from the committed snapshot

    def test_maybe_refresh_fresh_does_not_fetch(self):
        _seed({"AAPL": "0000320193"}, age_hours=1.0)  # fresh
        factory, client = _fake_client(_COMPANY_TICKERS)
        with patch("core.specialist.edgar_cik.httpx.AsyncClient", factory):
            _run(edgar_cik.maybe_refresh())
            _run(edgar_cik.maybe_refresh())
        assert client.get.await_count == 0

    def test_maybe_refresh_ttl_expiry_refreshes(self):
        _seed({"AAPL": "0000000001"}, age_hours=25.0)  # stale + expired
        factory, client = _fake_client(_COMPANY_TICKERS)
        with patch("core.specialist.edgar_cik.httpx.AsyncClient", factory):
            _run(edgar_cik.maybe_refresh())
        assert client.get.await_count == 1
        assert edgar_cik.resolve_cik("AAPL") == "0000320193"  # v2 now served

    def test_refresh_failure_serves_last_known_good(self, caplog):
        _seed({"AAPL": "0000320193"}, age_hours=25.0)
        factory, _ = _fake_client(raise_exc=RuntimeError("boom"))
        with patch("core.specialist.edgar_cik.httpx.AsyncClient", factory):
            _run(edgar_cik.maybe_refresh())
        assert edgar_cik.resolve_cik("AAPL") == "0000320193"  # LKG, not None
        assert any(
            r.levelname == "WARNING" and "last-known-good" in r.getMessage().lower()
            for r in caplog.records
        )

    def test_single_flight_under_concurrency(self):
        _seed({"AAPL": "0000000001"}, age_hours=25.0)
        factory, client = _fake_client(_COMPANY_TICKERS)

        async def _many():
            await asyncio.gather(*[edgar_cik.maybe_refresh() for _ in range(6)])

        with patch("core.specialist.edgar_cik.httpx.AsyncClient", factory):
            _run(_many())
        assert client.get.await_count == 1  # single-flight guard
