# tests/unit/test_redis_sync_facade.py
# Regression for the BORA desktop bug (#1050 / OSS-3): RedisClient.get_sync_redis()
# must return a SYNCHRONOUS handle in local/desktop mode. The LocalStateClient backend
# has async get/set/delete (coroutines); sync consumers (the /benchmark-equity route,
# the portfolio-snapshot writer in base.py, kill_switch, polygon cache, ...) call
# .get/.set/.delete and would otherwise receive un-awaited coroutines -> persistence
# silently breaks (portfolio_snapshots never written, /benchmark-equity internal_error).
# Cloud is unaffected (real redis-py .get/.set are already synchronous).
import inspect

import pytest


@pytest.fixture(autouse=True)
def mock_redis_global():
    """Opt THIS module out of the global fakeredis autouse mock (tests/conftest.py).

    That mock replaces get_sync_redis() with a *synchronous* fakeredis client, which
    would hide the very bug under test (the real local backend's async get/set/delete).
    Overriding it (same name) with a no-op gives us the REAL _SyncLocalStateFacade over
    the real LocalStateClient — so this guards the fix BY DEFAULT, no env var needed.
    """
    yield


def _local_redis(monkeypatch):
    monkeypatch.setattr("core.redis_client._is_local_mode", lambda: True)
    from core.redis_client import RedisClient

    r = RedisClient.get_sync_redis()
    # Prove we are exercising the REAL facade, not a fakeredis/mock stand-in. If the
    # global redis mock ever leaks back in, this assert catches it (test stays honest).
    assert (
        type(r).__name__ == "_SyncLocalStateFacade"
    ), f"expected the real sync facade, got {type(r).__name__} — global redis mock leaked in"
    return r


def test_get_is_synchronous_in_local_mode(monkeypatch):
    r = _local_redis(monkeypatch)
    r.delete("bora_k")
    got = r.get("bora_k")
    assert not inspect.iscoroutine(got), "get_sync_redis().get must be synchronous"
    assert got is None


def test_set_get_delete_round_trip_synchronously(monkeypatch):
    r = _local_redis(monkeypatch)
    set_res = r.set("bora_k", "v1")
    assert not inspect.iscoroutine(set_res), "get_sync_redis().set must be synchronous"
    got = r.get("bora_k")
    assert not inspect.iscoroutine(got), "get_sync_redis().get must be synchronous"
    assert got == "v1"
    deleted = r.delete("bora_k")
    assert not inspect.iscoroutine(
        deleted
    ), "get_sync_redis().delete must be synchronous"
    assert deleted == 1
    assert r.get("bora_k") is None


def test_set_with_ex_ttl_is_accepted(monkeypatch):
    # redis-py consumers pass ex=<seconds>; the facade must accept it (LocalStateClient
    # set_sync uses px=<ms>) and store synchronously without raising.
    r = _local_redis(monkeypatch)
    res = r.set("bora_ttl", "v", ex=300)
    assert not inspect.iscoroutine(res)
    assert r.get("bora_ttl") == "v"
    r.delete("bora_ttl")


def test_ping_is_synchronous(monkeypatch):
    # kill_switch.py calls r.ping() on the sync handle — must not return a coroutine.
    r = _local_redis(monkeypatch)
    pong = r.ping()
    assert not inspect.iscoroutine(pong), "get_sync_redis().ping must be synchronous"


def test_hget_hset_round_trip_synchronously(monkeypatch):
    # #1353: round_table/base_agent.py and learning/engine.py read/write agent weights
    # via r.hget/r.hset on the sync handle. These must be synchronous (LocalStateClient
    # had no hget/hset at all -> AttributeError, so desktop agent weights never worked).
    r = _local_redis(monkeypatch)
    res = r.hset("agent_weights_v2", "LSTMSignalAgent", "0.55")
    assert not inspect.iscoroutine(res), "hset must be synchronous"
    got = r.hget("agent_weights_v2", "LSTMSignalAgent")
    assert not inspect.iscoroutine(got), "hget must be synchronous"
    assert got == "0.55"
    assert r.hget("agent_weights_v2", "missing_field") is None


def test_keys_is_synchronous(monkeypatch):
    # #1353: scripts/analyze_bot.py calls r.keys("*") on the sync handle.
    r = _local_redis(monkeypatch)
    r.set("bora_keytest_1", "x")
    ks = r.keys("bora_keytest_*")
    assert not inspect.iscoroutine(ks), "keys must be synchronous"
    assert "bora_keytest_1" in ks
    r.delete("bora_keytest_1")


def test_xadd_is_synchronous(monkeypatch):
    # #1353: round_table/senate_log.py calls redis.xadd(...) on the sync handle.
    r = _local_redis(monkeypatch)
    msg_id = r.xadd("bora_stream", {"a": "1"})
    assert not inspect.iscoroutine(msg_id), "xadd must be synchronous"
    assert isinstance(msg_id, str) and "-" in msg_id
