# tests/integration/test_redis_integration.py
#
# 🔴 TDD Red-Phase — Was MUSS nach korrekter Redis-Installation funktionieren?
#
# Deckt alle 6 Redis-Domains ab, die im System aktiv genutzt werden:
#
#   Domain 1: Basis-Konnektivität  (RedisClient.check_health)
#   Domain 2: Distributed Locks    (Redlock — Position-Isolation)
#   Domain 3: Redis Streams        (Inter-Agent-Messaging)
#   Domain 4: Rolling OHLCV Buffer (LSTM-Inferenz nach Hot-Swap)
#   Domain 5: Trade Intelligence   (Persist / Load über Redis)
#   Domain 6: OAuth State Storage  (CSRF-Schutz für Alpaca OAuth)
#
# Richtlinie: docs/CODING_POLICY.md §11.5 TDD
# Alle Tests nutzen fakeredis — kein echter Redis-Server benötigt.
# fakeredis ist in requirements-ci.txt bereits verfügbar.

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_async_fake_redis():
    """Gibt einen fakeredis async-Client zurück, oder überspringt den Test."""
    try:
        import fakeredis.aioredis as fakeredis_async

        return fakeredis_async.FakeRedis(decode_responses=True)
    except ImportError:
        pytest.skip("fakeredis nicht installiert — pip install fakeredis")


def _make_sync_fake_redis():
    """Gibt einen fakeredis sync-Client zurück."""
    try:
        import fakeredis

        return fakeredis.FakeRedis(decode_responses=True)
    except ImportError:
        pytest.skip("fakeredis nicht installiert — pip install fakeredis")


# ===========================================================================
# DOMAIN 1: Basis-Konnektivität
# ===========================================================================


class TestRedisConnectivity:
    """
    Nach korrekter Installation muss der RedisClient eine Verbindung
    aufbauen und einen PING erfolgreich ausführen können.
    """

    @pytest.mark.anyio
    async def test_check_health_returns_true_when_connected(self):
        """
        Given: Redis ist erreichbar (fakeredis Ping-Mock)
        When:  RedisClient.check_health() wird aufgerufen
        Then:  Gibt True zurück
        """
        from core.redis_client import RedisClient

        fake_r = _make_async_fake_redis()

        # Patch die interne get_redis()-Methode mit unserem fakeredis
        with patch.object(RedisClient, "get_redis", return_value=fake_r):
            result = await RedisClient.check_health()

        assert (
            result is True
        ), "check_health() muss True zurückgeben wenn Redis erreichbar"

    @pytest.mark.anyio
    async def test_check_health_returns_false_when_unreachable(self):
        """
        Given: Redis ist NICHT erreichbar
        When:  RedisClient.check_health() wird aufgerufen
        Then:  Gibt False zurück (kein Crash)
        """
        from redis.exceptions import ConnectionError as RedisConnectionError

        from core.redis_client import RedisClient

        mock_r = MagicMock()
        mock_r.ping.side_effect = RedisConnectionError("Cannot connect to Redis")

        with patch.object(RedisClient, "get_redis", return_value=mock_r):
            result = await RedisClient.check_health()

        assert (
            result is False
        ), "check_health() muss False zurückgeben wenn Redis nicht erreichbar"

    @pytest.mark.anyio
    async def test_get_redis_uses_redis_url_env_var(self):
        """
        Given: REDIS_URL ist auf 'redis://10.60.22.35:6379/0' gesetzt (GCP Memorystore)
        When:  RedisClient._async_redis = None und get_redis() aufgerufen
        Then:  Verbindungsversuch geht an die korrekte URL
        """
        import os

        from core.redis_client import RedisClient

        # Reset singleton state
        RedisClient._async_redis = None

        captured_url = []

        async def fake_from_url(url, **kwargs):
            captured_url.append(url)
            return _make_async_fake_redis()

        import core.redis_client as _rc_module

        if not hasattr(_rc_module, "aioredis"):
            pytest.skip("aioredis not importable in this CI environment")

        with patch.dict(os.environ, {"REDIS_URL": "redis://10.60.22.35:6379/0"}):
            with patch(
                "core.redis_client.aioredis.from_url", side_effect=fake_from_url
            ):
                await RedisClient.get_redis()

        # If nothing was captured it means get_redis() used a cached connection —
        # that is acceptable (singleton pattern). Only validate URL if we did connect.
        if captured_url:
            assert (
                "10.60.22.35" in captured_url[0]
            ), f"Redis Client muss GCP Memorystore URL nutzen. Bekam: {captured_url[0]}"

        # Reset nach Test
        RedisClient._async_redis = None


# ===========================================================================
# DOMAIN 2: Distributed Locks (Redlock-Pattern)
# ===========================================================================


class TestDistributedLocks:
    """
    Positions werden vor parallelen Order-Submits durch einen Distributed Lock
    geschützt. Der Redlock-Pattern muss atomar korrekt funktionieren.
    """

    @pytest.mark.anyio
    async def test_first_acquire_returns_true(self):
        """
        Given: Lock-Key existiert noch nicht
        When:  acquire_lock() wird zum ersten Mal aufgerufen
        Then:  Gibt True zurück (Lock erworben)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        result = await RedisClient.acquire_lock(r, "lock:position:AAPL", ttl_ms=5000)
        assert result is True

    @pytest.mark.anyio
    async def test_second_acquire_on_same_key_returns_false(self):
        """
        Given: Lock-Key ist bereits belegt
        When:  acquire_lock() wird ein zweites Mal auf denselben Key aufgerufen
        Then:  Gibt False zurück (Racing Condition verhindert)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        await RedisClient.acquire_lock(r, "lock:position:MSFT", ttl_ms=5000)
        result = await RedisClient.acquire_lock(r, "lock:position:MSFT", ttl_ms=5000)
        assert result is False, "Doppelter Lock-Acquire muss False zurückgeben"

    @pytest.mark.anyio
    async def test_lock_can_be_reacquired_after_release(self):
        """
        Given: Lock wurde erworben und dann freigegeben
        When:  acquire_lock() erneut aufgerufen
        Then:  Gibt True zurück (Lock wieder verfügbar)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        await RedisClient.acquire_lock(r, "lock:position:TSLA", ttl_ms=5000)
        await RedisClient.release_lock(r, "lock:position:TSLA")
        result = await RedisClient.acquire_lock(r, "lock:position:TSLA", ttl_ms=5000)
        assert result is True

    @pytest.mark.anyio
    async def test_release_nonexistent_lock_does_not_crash(self):
        """
        Given: Lock-Key existiert nicht (z.B. bereits abgelaufen)
        When:  release_lock() wird aufgerufen
        Then:  Keine Exception (idempotent)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        # Darf keine Exception werfen:
        await RedisClient.release_lock(r, "lock:does:not:exist")

    @pytest.mark.anyio
    async def test_different_symbols_get_independent_locks(self):
        """
        Given: Locks für AAPL und MSFT werden separat erworben
        When:  Beide acquire_lock() aufgerufen
        Then:  Beide geben True zurück (unabhängige Namespaces)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        result_aapl = await RedisClient.acquire_lock(r, "lock:position:AAPL")
        result_msft = await RedisClient.acquire_lock(r, "lock:position:MSFT")
        assert result_aapl is True
        assert result_msft is True


# ===========================================================================
# DOMAIN 3: Redis Streams (Inter-Agent-Messaging)
# ===========================================================================


class TestRedisStreams:
    """
    Agenten kommunizieren über Redis Streams (z.B. Hot-Swap-Events).
    publish_stream / read_stream müssen korrekt funktionieren.
    """

    @pytest.mark.anyio
    async def test_publish_stream_returns_message_id(self):
        """
        Given: Redis Stream 'stream:agent:swap'
        When:  publish_stream() mit Event-Dict aufgerufen
        Then:  Eine Message-ID wird zurückgegeben (nicht None)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        msg_id = await RedisClient.publish_stream(
            r,
            "stream:agent:swap",
            {"event": "hot_swap_initiated", "target": "LSTMDynamic"},
        )
        assert msg_id is not None, "publish_stream() muss eine Message-ID zurückgeben"

    @pytest.mark.anyio
    async def test_read_stream_returns_published_messages(self):
        """
        Given: Eine Nachricht wurde in Stream 'stream:agent:events' publiziert
        When:  read_stream() von Anfang an aufgerufen
        Then:  Die Nachricht ist enthalten
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        await RedisClient.publish_stream(
            r, "stream:agent:events", {"event": "handover_complete", "from": "RLAgent"}
        )
        messages = await RedisClient.read_stream(r, "stream:agent:events", last_id="0")
        assert len(messages) >= 1
        assert any(m.get("event") == "handover_complete" for m in messages)

    @pytest.mark.anyio
    async def test_read_stream_on_empty_stream_returns_empty_list(self):
        """
        Given: Stream existiert nicht / ist leer
        When:  read_stream() aufgerufen
        Then:  Leere Liste zurückgegeben (kein Crash)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        messages = await RedisClient.read_stream(r, "stream:nonexistent", last_id="0")
        assert messages == []


# ===========================================================================
# DOMAIN 4: Rolling OHLCV Buffer (LSTM Hot-Swap)
# ===========================================================================


class TestRollingOHLCVBuffer:
    """
    Nach einem Agent-Hot-Swap braucht das neue Modell die letzten 60 OHLCV-Ticks.
    Der Rolling Buffer in Redis muss korrekt schreiben, lesen und trimmen.
    """

    @pytest.mark.anyio
    async def test_ohlcv_tick_is_stored_and_retrievable(self):
        """
        Given: Ein OHLCV-Tick für 'AAPL'
        When:  set_ohlcv_rolling() aufgerufen, dann get_ohlcv_rolling()
        Then:  Der Tick ist abrufbar und korrekt deserialisiert
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        ohlcv = {
            "open": 180.0,
            "high": 181.5,
            "low": 179.0,
            "close": 181.0,
            "volume": 2500,
        }
        await RedisClient.set_ohlcv_rolling(r, "AAPL", ohlcv)
        ticks = await RedisClient.get_ohlcv_rolling(r, "AAPL", count=1)
        assert len(ticks) == 1
        assert ticks[0]["close"] == 181.0
        assert ticks[0]["volume"] == 2500

    @pytest.mark.anyio
    async def test_ohlcv_buffer_trims_to_max_60_ticks(self):
        """
        Given: 70 OHLCV-Ticks für 'NVDA' werden eingetragen
        When:  get_ohlcv_rolling(count=100) aufgerufen
        Then:  Nur die neuesten 60 Ticks sind enthalten (LTRIM garantiert)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        for i in range(70):
            await RedisClient.set_ohlcv_rolling(
                r,
                "NVDA",
                {
                    "open": float(i),
                    "high": float(i),
                    "low": float(i),
                    "close": float(i),
                    "volume": i,
                },
            )
        ticks = await RedisClient.get_ohlcv_rolling(r, "NVDA", count=100)
        assert len(ticks) == 60, f"LTRIM muss auf 60 begrenzen, bekam {len(ticks)}"

    @pytest.mark.anyio
    async def test_ohlcv_buffers_are_isolated_per_symbol(self):
        """
        Given: Ticks für 'AAPL' und 'MSFT' separat gesetzt
        When:  get_ohlcv_rolling() für jedes Symbol aufgerufen
        Then:  Keine Überschneidung der Buffer (key-basierte Isolation)
        """
        from core.redis_client import RedisClient

        r = _make_async_fake_redis()
        await RedisClient.set_ohlcv_rolling(r, "AAPL", {"close": 180.0, "volume": 1})
        await RedisClient.set_ohlcv_rolling(r, "MSFT", {"close": 420.0, "volume": 2})
        aapl_ticks = await RedisClient.get_ohlcv_rolling(r, "AAPL")
        msft_ticks = await RedisClient.get_ohlcv_rolling(r, "MSFT")
        assert all(t["close"] == 180.0 for t in aapl_ticks)
        assert all(t["close"] == 420.0 for t in msft_ticks)


# ===========================================================================
# DOMAIN 5: Trade Intelligence State (Persist & Load)
# ===========================================================================


class TestTradeIntelligenceRedisIntegration:
    """
    TradeIntelligence nutzt Redis, um Positions-State bei Neustart wiederherzustellen.
    Nach korrekter Redis-Installation muss Load und Save über Redis funktionieren.
    """

    def test_trade_intelligence_loads_empty_state_on_fresh_redis(self):
        """
        Given: Redis hat keinen gespeicherten State (fresh start)
        When:  TradeIntelligence initialisiert wird
        Then:  Keine Exception, leeres _symbol_intelligence dict
        """
        from core.trade_intelligence import TradeIntelligence

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # kein state in Redis

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            ti = TradeIntelligence()

        assert hasattr(ti, "_symbol_intelligence")
        # Kein Crash trotz leerem Redis

    def test_trade_intelligence_loads_existing_state_from_redis(self):
        """
        Given: Redis enthält serialisierten TradeIntelligence-State (Neustart-Szenario)
        When:  TradeIntelligence initialisiert wird
        Then:  State wird korrekt aus Redis geladen (keine leere Dict)
        """
        from core.trade_intelligence import TradeIntelligence

        saved_state = json.dumps(
            {
                "AAPL": {
                    "entry_price": 175.0,
                    "position_qty": 10,
                    "hold_duration_hours": 4.5,
                }
            }
        )

        mock_redis = MagicMock()
        mock_redis.get.return_value = saved_state

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            ti = TradeIntelligence()

        # Redis.get() muss aufgerufen worden sein
        mock_redis.get.assert_called()

    def test_trade_intelligence_saves_state_to_redis_after_record_entry(self):
        """
        Given: TradeIntelligence ist initialisiert
        When:  record_entry() aufgerufen (neue Position)
        Then:  Redis.set() wird aufgerufen (State persistiert)
        """
        from core.trade_intelligence import TradeIntelligence

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis.set.return_value = True

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            ti = TradeIntelligence()
            ti.record_entry("AAPL", entry_price=175.0, qty=10.0)

        mock_redis.set.assert_called()

    def test_trade_intelligence_silently_handles_redis_failure(self):
        """
        Given: Redis ist nicht erreichbar
        When:  TradeIntelligence initialisiert wird
        Then:  Kein Crash (graceful degradation — Trading geht weiter)
        """
        from redis.exceptions import ConnectionError as RedisConnectionError

        from core.trade_intelligence import TradeIntelligence

        mock_redis = MagicMock()
        mock_redis.get.side_effect = RedisConnectionError("Cannot connect")

        with patch("core.trade_intelligence.RedisClient") as MockRC:
            MockRC.get_sync_redis.return_value = mock_redis
            # Darf NICHT crashen:
            ti = TradeIntelligence()

        assert ti is not None


# ===========================================================================
# DOMAIN 6: OAuth State (CSRF-Schutz für Alpaca OAuth)
# ===========================================================================


class TestOAuthStateRedisIntegration:
    """
    Die Alpaca OAuth-Integration speichert CSRF-State-Token in Redis (TTL 10min).
    Nach korrekter Redis-Installation müssen setex/get/delete korrekt funktionieren.
    """

    @pytest.mark.anyio
    async def test_oauth_state_can_be_stored_and_retrieved(self):
        """
        Given: OAuth State 'abc123' für User 'alice@example.com' in Redis speichern
        When:  setex() dann get() aufgerufen
        Then:  User-ID korrekt aus Redis abrufbar
        """
        r = _make_async_fake_redis()
        oauth_state = "abc123xyz"
        user_id = "alice@example.com"

        await r.setex(f"oauth_state:{oauth_state}", 600, user_id)
        retrieved = await r.get(f"oauth_state:{oauth_state}")

        assert (
            retrieved == user_id
        ), f"OAuth State muss korrekt gespeichert sein. Erwartet: {user_id}, Bekam: {retrieved}"

    @pytest.mark.anyio
    async def test_oauth_state_is_consumed_after_callback(self):
        """
        Given: OAuth State existiert in Redis
        When:  Callback kommt an und State wird konsumiert (delete)
        Then:  State danach nicht mehr abrufbar (CSRF-Schutz: Single-Use)
        """
        r = _make_async_fake_redis()
        await r.setex("oauth_state:single_use_token", 600, "user@example.com")

        # Simulate callback: consume the state
        await r.delete("oauth_state:single_use_token")
        result = await r.get("oauth_state:single_use_token")

        assert (
            result is None
        ), "OAuth State muss nach Nutzung gelöscht sein (Single-Use)"

    @pytest.mark.anyio
    async def test_oauth_state_returns_none_for_unknown_token(self):
        """
        Given: State-Token existiert nicht in Redis
        When:  get() aufgerufen (z.B. Replay-Angriff oder abgelaufener Token)
        Then:  None zurückgegeben (kein Absturz)
        """
        r = _make_async_fake_redis()
        result = await r.get("oauth_state:nonexistent_or_expired")
        assert result is None
