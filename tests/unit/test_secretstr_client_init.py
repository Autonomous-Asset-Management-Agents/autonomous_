# tests/unit/test_secretstr_client_init.py
# INF-14 — TDD Gate: SecretStr extraction at Alpaca/Polygon client boundaries.
#
# Archon Standard: TDD mandatory. These tests MUST pass before any deployment.
# Run: pytest tests/unit/test_secretstr_client_init.py -v
#
# Covers:
#   1. config.get_secret_str()       — canonical extraction helper
#   2. api_routes._init_trading_clients() — Alpaca SDK receives plain str
#   3. news_poller _news_polling_loop()  — Polygon URL contains plain str key
#   4. data_provider.get_data()      — Polygon fetch_bars called with plain str

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# ---------------------------------------------------------------------------
# 1. config.get_secret_str() — canonical helper unit tests
# ---------------------------------------------------------------------------


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
class TestGetSecretStr:
    """config.get_secret_str() must unpack any valid input correctly."""

    def _import(self):
        import config  # noqa: PLC0415

        return config

    def test_plain_string_returned_as_is(self):
        cfg = self._import()
        assert cfg.get_secret_str("hello") == "hello"

    def test_none_returns_empty_string(self):
        cfg = self._import()
        assert cfg.get_secret_str(None) == ""

    def test_secret_str_unpacked(self):
        from pydantic import SecretStr

        cfg = self._import()
        secret = SecretStr("my-api-key-123")
        assert cfg.get_secret_str(secret) == "my-api-key-123"

    def test_invalid_type_raises_type_error(self):
        cfg = self._import()
        with pytest.raises(
            TypeError, match="get_secret_str\\(\\) expected SecretStr or str"
        ):
            cfg.get_secret_str(12345)

    def test_empty_string_returned_as_is(self):
        cfg = self._import()
        assert cfg.get_secret_str("") == ""

    def test_secret_str_not_masked(self):
        """Ensure the raw value is returned, not '**********'."""
        from pydantic import SecretStr

        cfg = self._import()
        secret = SecretStr("REAL_KEY_VALUE")
        result = cfg.get_secret_str(secret)
        assert result == "REAL_KEY_VALUE"
        assert "**" not in result


# ---------------------------------------------------------------------------
# 2. api_routes._init_trading_clients() — SecretStr never reaches Alpaca SDK
# ---------------------------------------------------------------------------


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
class TestInitTradingClientsSecretStrSafe:
    """
    _init_trading_clients must pass plain strings (not SecretStr) to
    TradingClient and StockHistoricalDataClient.

    Regression test for PR #888 / INF-14.
    """

    def test_alpaca_clients_receive_plain_strings_not_secretstr(self):
        from pydantic import SecretStr

        captured_calls = {}

        def mock_trading_client(api_key, secret_key, paper):
            captured_calls["trading_key"] = api_key
            captured_calls["trading_secret"] = secret_key
            instance = MagicMock()
            instance.get_account.return_value = MagicMock(
                status="ACTIVE", equity="10000"
            )
            return instance

        def mock_data_client(api_key, secret_key):
            captured_calls["data_key"] = api_key
            captured_calls["data_secret"] = secret_key
            return MagicMock()

        with (
            patch(
                "core.engine.api_routes.TradingClient", side_effect=mock_trading_client
            ),
            patch(
                "core.engine.api_routes.StockHistoricalDataClient",
                side_effect=mock_data_client,
            ),
            patch("core.engine.api_routes.BotEngine", return_value=MagicMock()),
            patch("core.engine.api_routes.config") as mock_config,
            patch("core.engine.api_routes.engine", None),
        ):
            mock_config.API_KEY = SecretStr("TEST_API_KEY")
            mock_config.API_SECRET = SecretStr("TEST_SECRET_KEY")
            mock_config.BASE_URL = "https://paper-api.alpaca.markets"
            mock_config.PAPER_TRADING = True
            mock_config.get_secret_str = lambda v: (
                v.get_secret_value() if hasattr(v, "get_secret_value") else (v or "")
            )

            from core.engine.api_routes import _init_trading_clients

            _init_trading_clients()

        assert captured_calls.get("trading_key") == "TEST_API_KEY", (
            "TradingClient must receive plain str, not SecretStr. "
            "This is the INF-14 regression guard."
        )
        assert captured_calls.get("trading_secret") == "TEST_SECRET_KEY"
        assert captured_calls.get("data_key") == "TEST_API_KEY"
        assert captured_calls.get("data_secret") == "TEST_SECRET_KEY"

        # Verify none of the values are SecretStr instances
        from pydantic import SecretStr as _SecretStr

        for k, v in captured_calls.items():
            assert not isinstance(v, _SecretStr), (
                f"Alpaca SDK received SecretStr for '{k}'. "
                "This causes 'Header part must be of type str or bytes' crash."
            )

    def test_no_api_key_skips_init_gracefully(self):
        """When API_KEY is None/empty, init must not crash."""
        with (
            patch("core.engine.api_routes.BotEngine", return_value=MagicMock()),
            patch("core.engine.api_routes.config") as mock_config,
            patch("core.engine.api_routes.engine", None),
        ):
            mock_config.API_KEY = None
            mock_config.API_SECRET = None
            mock_config.PAPER_TRADING = True
            mock_config.get_secret_str = lambda v: v or ""

            from core.engine.api_routes import _init_trading_clients

            # Must not raise
            _init_trading_clients()


# ---------------------------------------------------------------------------
# 3. news_poller — Polygon URL receives plain string, not SecretStr repr
# ---------------------------------------------------------------------------


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
class TestNewsPollerPolygonKeyExtraction:
    """
    The Polygon news URL must contain the raw API key, not 'SecretStr(***)'.\n
    Regression test for PR #888 / INF-14: 401 Unauthorized on Polygon News.
    """

    def test_polygon_url_built_with_plain_key(self):
        """
        Unit-test that config.get_secret_str(POLYGON_API_KEY) in _news_polling_loop
        produces a plain string that is correctly embedded in the URL.
        This directly verifies the INF-14 fix in news_poller.py line 136.
        """
        from pydantic import SecretStr

        import config as cfg

        # Simulate what _news_polling_loop does on line 136
        polygon_key_secret = SecretStr("POLYGON_REAL_KEY_XYZ")
        extracted = cfg.get_secret_str(polygon_key_secret)

        # The extracted key must be the raw string
        assert extracted == "POLYGON_REAL_KEY_XYZ", (
            f"get_secret_str returned '{extracted}' instead of raw string. "
            "INF-14 regression: SecretStr repr in URL causes 401."
        )

        # Build the URL exactly as _news_polling_loop does
        url = f"https://api.polygon.io/v2/reference/news?limit=50&apiKey={extracted}"
        assert "POLYGON_REAL_KEY_XYZ" in url
        assert "SecretStr" not in url
        assert "**" not in url

    def test_polygon_loop_calls_http_with_plain_key(self):
        """
        Integration-level: patch config inside news_poller module, invoke the
        URL-building part of _news_polling_loop by exercising it via a threading
        shutdown immediately after the first iteration.
        """
        import threading

        from pydantic import SecretStr

        captured_url = {}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.status_code = 200

        def mock_get(url, timeout):
            captured_url["url"] = url
            return mock_response

        shutdown_event = threading.Event()

        with (
            patch("core.engine.news_poller.config") as mock_config,
            patch("core.engine.news_poller.http_requests") as mock_http,
            patch("core.engine.news_poller.time.sleep", return_value=None),
        ):
            mock_config.POLYGON_API_KEY = SecretStr("POLYGON_REAL_KEY_XYZ")
            mock_config.get_secret_str = lambda v: (
                v.get_secret_value() if hasattr(v, "get_secret_value") else (v or "")
            )
            mock_http.get = mock_get

            from core.engine.news_poller import NewsPollerMixin

            class _StubPoller(NewsPollerMixin):
                def __init__(self):
                    self.news_running = threading.Event()
                    self.news_running.set()
                    self.news_processor = MagicMock()
                    self.news_processor.analyze_sentiments_batch.return_value = {}

            poller = _StubPoller()

            # Run in thread, shut down after first HTTP call completes
            def _run():
                # Signal shutdown after http.get is called once
                orig_get = mock_get

                def _one_shot(url, timeout):
                    result = orig_get(url, timeout)
                    shutdown_event.set()
                    poller.news_running.clear()
                    return result

                mock_http.get = _one_shot
                poller._news_polling_loop(shutdown_event)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=5)

        assert (
            "url" in captured_url
        ), "HTTP GET was never called — poller did not execute"
        url = captured_url["url"]

        assert "POLYGON_REAL_KEY_XYZ" in url, (
            f"Polygon URL must contain the raw API key. Got: {url}\n"
            "INF-14 regression: SecretStr repr 'SecretStr(***)' in URL causes 401."
        )
        assert (
            "SecretStr" not in url
        ), f"SecretStr object repr leaked into Polygon URL: {url}"
        assert "**" not in url, f"Masked SecretStr value leaked into Polygon URL: {url}"


# ---------------------------------------------------------------------------
# 4. data_provider — polygon_fetch_bars called with plain string
# ---------------------------------------------------------------------------


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
class TestDataProviderPolygonKeyExtraction:
    """
    polygon_fetch_bars must receive a plain str for the api_key argument.

    Regression test for PR #888 / INF-14.
    """

    def test_get_data_polygon_fallback_passes_plain_string(self, tmp_path):
        import pandas as pd
        from pydantic import SecretStr

        captured_key = {}

        def mock_polygon_fetch_bars(api_key, symbol, start_date, end_date, **kwargs):
            captured_key["key"] = api_key
            dates = pd.date_range("2024-01-01", periods=5)
            return pd.DataFrame(
                {
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "volume": 1e6,
                },
                index=dates,
            )

        from datetime import datetime

        import config as cfg_module
        from core.data_provider import HistoricalDataProvider

        with (
            # Force Polygon branch: disable all upstream sources
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.POLYGON_API_KEY", SecretStr("POLY_REAL_KEY")),
            patch("core.data_provider.DATABENTO_ENABLED", False),
            patch.object(
                cfg_module,
                "get_secret_str",
                side_effect=lambda v: (
                    v.get_secret_value()
                    if hasattr(v, "get_secret_value")
                    else (v or "")
                ),
            ),
            patch(
                "core.data_provider.polygon_fetch_bars",
                side_effect=mock_polygon_fetch_bars,
            ),
        ):
            dp = HistoricalDataProvider(api=None)
            dp._databento = None  # ensure Databento path is skipped
            # Clear in-memory cache so no cache hit occurs
            dp.data_cache.clear()
            dp.get_data("AAPL", datetime(2024, 6, 1), days=30, allow_yfinance=False)

        assert (
            "key" in captured_key
        ), "polygon_fetch_bars was never called — test setup may be wrong"
        assert captured_key["key"] == "POLY_REAL_KEY", (
            f"polygon_fetch_bars received '{captured_key['key']}' instead of raw string. "
            "INF-14 regression: SecretStr in Polygon call causes HTTP 400/401."
        )
        from pydantic import SecretStr as _SecretStr

        assert not isinstance(
            captured_key["key"], _SecretStr
        ), "SecretStr object passed to polygon_fetch_bars — must be plain str."

    def test_get_batch_data_polygon_fallback_passes_plain_string(self, tmp_path):
        """Batch path also must extract SecretStr correctly."""
        import pandas as pd
        from pydantic import SecretStr

        captured_keys = []

        def mock_polygon_fetch_bars(api_key, symbol, start_date, end_date, **kwargs):
            captured_keys.append(api_key)
            dates = pd.date_range("2024-01-01", periods=5)
            return pd.DataFrame(
                {
                    "open": 100.0,
                    "high": 105.0,
                    "low": 95.0,
                    "close": 102.0,
                    "volume": 1e6,
                },
                index=dates,
            )

        from datetime import datetime

        import config as cfg_module
        from core.data_provider import HistoricalDataProvider

        with (
            patch("core.data_provider.DATA_CACHE_DIR", str(tmp_path)),
            patch("core.data_provider.POLYGON_API_KEY", SecretStr("POLY_BATCH_KEY")),
            patch("core.data_provider.DATABENTO_ENABLED", False),
            patch.object(
                cfg_module,
                "get_secret_str",
                side_effect=lambda v: (
                    v.get_secret_value()
                    if hasattr(v, "get_secret_value")
                    else (v or "")
                ),
            ),
            patch(
                "core.data_provider.polygon_fetch_bars",
                side_effect=mock_polygon_fetch_bars,
            ),
        ):
            dp = HistoricalDataProvider(api=None)
            dp._databento = None
            dp.data_cache.clear()
            dp.get_batch_data(["AAPL", "TSLA"], datetime(2024, 6, 1))

        assert (
            len(captured_keys) > 0
        ), "polygon_fetch_bars was never called in batch path"
        for key in captured_keys:
            assert (
                key == "POLY_BATCH_KEY"
            ), f"Batch polygon call received '{key}' instead of raw string."
