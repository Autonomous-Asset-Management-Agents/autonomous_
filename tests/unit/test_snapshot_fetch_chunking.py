# tests/unit/test_snapshot_fetch_chunking.py
# Snapshot-fetch chunking (docs/snapshot-fetch-chunking/implementation_plan.md)
# P1 / trading-path. TDD Red->Green.
#
# The per-cycle universe snapshot fetch was ONE request for ~500 symbols; a single
# timeout / RemoteDisconnect set snapshots={} -> update_lstm_rankings skipped every symbol
# -> empty panel -> all-HOLD. _fetch_snapshots_chunked splits the fetch into CONCURRENT
# chunks (asyncio.gather) so a failing chunk drops only ITS symbols while the rest still
# populate, and total wall-clock stays bounded by ONE per-chunk timeout, not N x.
from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"


def _mixin():
    from core.engine.trading_loop import TradingLoopMixin

    return TradingLoopMixin.__new__(TradingLoopMixin)


class _FakeSnapshotAPI:
    """Thread-safe fake. Symbols are named ``S{n}`` and chunks are contiguous, so the
    chunk index of a request is ``int(first_symbol[1:]) // chunk_size`` — DETERMINISTIC
    regardless of the concurrent order requests actually arrive in. Fails / hangs on the
    given chunk indices."""

    def __init__(self, *, fail=(), hang=(), chunk_size=100):
        self._lock = threading.Lock()
        self.calls = []
        self._fail = set(fail)
        self._hang = set(hang)
        self._chunk_size = chunk_size

    def get_stock_snapshot(self, request):
        syms = list(request.symbol_or_symbols)
        with self._lock:
            self.calls.append(syms)
        chunk_idx = int(syms[0][1:]) // self._chunk_size
        if chunk_idx in self._fail:
            raise RuntimeError("RemoteDisconnected")
        if chunk_idx in self._hang:
            time.sleep(1.5)  # exceed the per-chunk timeout used in the test
        return {s: SimpleNamespace(symbol=s) for s in syms}


def _syms(n):
    return [f"S{i}" for i in range(n)]


def _patch_size(n):
    return patch(
        "core.engine.trading_loop.get_config",
        return_value=SimpleNamespace(SNAPSHOT_FETCH_CHUNK_SIZE=n),
    )


class TestChunking:
    @pytest.mark.anyio
    async def test_splits_into_ceil_chunks_and_merges_all(self):
        api = _FakeSnapshotAPI(chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked(_syms(450))
        assert len(api.calls) == 5  # ceil(450 / 100)
        assert len(res) == 450

    @pytest.mark.anyio
    async def test_one_failing_chunk_keeps_the_rest(self):
        # THE FIX: chunk index 1 fails -> its 100 symbols missing, the other 350 present.
        api = _FakeSnapshotAPI(fail=(1,), chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked(_syms(450))
        assert res  # NON-empty (the pre-fix single request would return {})
        assert len(res) == 350
        assert "S100" not in res  # the failed chunk
        assert "S0" in res and "S449" in res  # neighbours survived

    @pytest.mark.anyio
    async def test_one_timeout_chunk_keeps_the_rest(self):
        api = _FakeSnapshotAPI(hang=(0,), chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked(_syms(300), per_chunk_timeout=1.0)
        assert len(res) == 200  # chunk 0 timed out; chunks 1 + 2 merged

    @pytest.mark.anyio
    async def test_concurrent_a_hung_chunk_does_not_serialize_the_rest(self):
        # CONCURRENCY (Archon WARNING-1): with 3 chunks each hung 1.5s, a SEQUENTIAL
        # implementation would take ~4.5s; concurrent gather resolves in ~one timeout.
        api = _FakeSnapshotAPI(hang=(0, 1, 2), chunk_size=100)
        m = _mixin()
        m.data_api = api
        t0 = time.perf_counter()
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked(_syms(300), per_chunk_timeout=1.0)
        elapsed = time.perf_counter() - t0
        assert res == {}  # all three hung past the timeout
        # bounded by ~ONE timeout, not 3x. Generous ceiling to stay non-flaky.
        assert elapsed < 2.5, f"chunks did not run concurrently (took {elapsed:.2f}s)"

    @pytest.mark.anyio
    async def test_all_chunks_fail_returns_empty_and_never_raises(self):
        api = _FakeSnapshotAPI(fail=(0, 1, 2), chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked(_syms(250))
        assert res == {}

    @pytest.mark.anyio
    async def test_size_zero_is_a_single_request(self):
        api = _FakeSnapshotAPI(chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(0):
            res = await m._fetch_snapshots_chunked(_syms(450))
        assert len(api.calls) == 1  # byte-identical single-request rollback
        assert len(res) == 450

    @pytest.mark.anyio
    async def test_empty_symbols_makes_no_call(self):
        api = _FakeSnapshotAPI(chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100):
            res = await m._fetch_snapshots_chunked([])
        assert res == {}
        assert api.calls == []


class TestLoggingLevels:
    """Archon WARNING-2 / AGENTS.md Rule 5: a partial chunk failure is a GRACEFUL
    fallback (others still populate) → WARNING, never ERROR. ERROR is reserved for a
    TOTAL outage (every chunk failed)."""

    @pytest.mark.anyio
    async def test_partial_failure_logs_warning_not_error(self, caplog):
        api = _FakeSnapshotAPI(fail=(1,), chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100), caplog.at_level(logging.WARNING):
            await m._fetch_snapshots_chunked(_syms(450))
        recs = [r for r in caplog.records if "chunk" in r.getMessage().lower()]
        assert any(r.levelno == logging.WARNING for r in recs)
        # a graceful, recovered-from partial failure must NOT be logged at ERROR
        assert not any(r.levelno == logging.ERROR for r in recs)

    @pytest.mark.anyio
    async def test_total_outage_logs_error(self, caplog):
        api = _FakeSnapshotAPI(fail=(0, 1, 2), chunk_size=100)
        m = _mixin()
        m.data_api = api
        with _patch_size(100), caplog.at_level(logging.WARNING):
            await m._fetch_snapshots_chunked(_syms(250))
        assert any(
            r.levelno == logging.ERROR for r in caplog.records
        ), "a total snapshot outage (every chunk failed) must surface at ERROR"


class TestConfigParity:
    def _load_oss(self):
        spec = importlib.util.spec_from_file_location(
            "config_oss_snap_test", _OSS_CONFIG
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_enterprise_default_100(self):
        import config as enterprise

        assert enterprise.get_config().SNAPSHOT_FETCH_CHUNK_SIZE == 100

    def test_oss_default_100(self):
        assert self._load_oss().get_config().SNAPSHOT_FETCH_CHUNK_SIZE == 100

    def test_both_editions_agree(self):
        import config as enterprise

        ent = enterprise.get_config().SNAPSHOT_FETCH_CHUNK_SIZE
        oss = self._load_oss().get_config().SNAPSHOT_FETCH_CHUNK_SIZE
        assert ent == oss == 100
