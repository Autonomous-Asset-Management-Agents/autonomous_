# tests/unit/test_data_provider_client_fallback.py
# R10 — a provider built without a data client must not silently degrade 40x.
#
# THE DEFECT: HistoricalDataProvider takes its Alpaca client as a parameter
# (data_provider.py `self.api = api`). With api=None the Alpaca branch is skipped and
# every fetch falls through to polygon_fetch_bars. The rate limits are not comparable:
#
#     Alpaca       ~200 req/min  -> 500 symbols ~=   3 min
#     Polygon free    5 req/min  -> 500 symbols ~= 100 min
#
# The engine constructs it correctly (base.py: `HistoricalDataProvider(api=self.data_api,
# trading_api=self.api)`). But the REPORT path builds its own, bare, at two sites:
#   * core/report/engine_reader.py  `self._data_provider = HistoricalDataProvider()`
#   * core/stock_specialist.py      `_DATA_PROVIDER = HistoricalDataProvider()`
#
# Why that is user-facing, not a dev nit: nothing is pre-generated and shipped — both
# caches are gitignored and absent from the desktop bundle, so EVERY user's machine
# fetches its own bars. The engine only warms its ~10-symbol live_universe; once the
# full-universe report registry is on, the remaining ~490 symbols are cache MISSES on the
# report path. At 5 req/min that is a ~100-minute first run — and worse, a throttled
# Polygon burst answers with an EMPTY FRAME rather than an error, so §5/§3 would render
# "no data" for symbols whose data exists. Measured on this machine: a bare provider gave
# a 36% empty rate that looked exactly like missing data; passing a client made it 14/14.
#
# So: build the client from config when the caller did not pass one, and say so at
# WARNING when we cannot (CODING_POLICY §5.6 — a fallback that hides is a fallback that
# rots). Passing api= explicitly still wins; that is how the engine and the tests inject.

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _no_databento(monkeypatch):
    """Keep these tests about the Alpaca client only."""
    import core.data_provider as dp

    monkeypatch.setattr(dp, "DATABENTO_ENABLED", False, raising=False)


class TestABareProviderStillGetsAClient:
    def test_no_api_argument_resolves_a_client_from_config(self, monkeypatch):
        """This is the test that pins the 40x degradation shut."""
        import core.data_provider as dp

        made = {}

        class _FakeClient:
            def __init__(self, key, secret):
                made["key"], made["secret"] = key, secret

        try:
            import core.client_factory as cf
        except ImportError:
            import ai_trading_bot.core.client_factory as cf

        monkeypatch.setattr(cf, "StockHistoricalDataClient", _FakeClient, raising=False)
        monkeypatch.setattr(dp, "ALPACA_API_KEY", "k-123", raising=False)
        monkeypatch.setattr(dp, "ALPACA_SECRET_KEY", "s-456", raising=False)

        provider = dp.HistoricalDataProvider()

        assert provider.api is not None, (
            "a bare provider kept api=None — every fetch silently falls through to "
            "Polygon at 5 req/min instead of Alpaca at ~200"
        )
        assert made == {"key": "k-123", "secret": "s-456"}

    def test_an_explicitly_passed_client_is_never_overridden(self, monkeypatch):
        """The engine injects its own; that must always win."""
        import core.data_provider as dp

        monkeypatch.setattr(dp, "ALPACA_API_KEY", "k-123", raising=False)
        monkeypatch.setattr(dp, "ALPACA_SECRET_KEY", "s-456", raising=False)
        sentinel = object()

        assert dp.HistoricalDataProvider(api=sentinel).api is sentinel


class TestNoCredentialsDegradesLOUDLY:
    """Without keys the Polygon fallback is the only option left — that is fine. What is
    NOT fine is doing it quietly: the symptom (a ~100-minute warm-up, or reports showing
    'no data' for symbols that have data) is impossible to diagnose from silence."""

    def test_missing_credentials_warn_and_do_not_raise(self, monkeypatch, caplog):
        import core.data_provider as dp

        monkeypatch.setattr(dp, "ALPACA_API_KEY", None, raising=False)
        monkeypatch.setattr(dp, "ALPACA_SECRET_KEY", None, raising=False)

        with caplog.at_level(logging.WARNING):
            provider = dp.HistoricalDataProvider()

        assert provider.api is None  # honest: no client, no pretending
        assert "polygon" in caplog.text.lower(), (
            "the degradation to Polygon's 5 req/min was not logged — a 40x slowdown "
            "must never be silent"
        )

    def test_a_failing_client_constructor_does_not_break_the_provider(
        self, monkeypatch, caplog
    ):
        """A data provider that cannot be constructed takes the whole engine down. It
        must degrade to the Polygon path instead."""
        import core.data_provider as dp

        try:
            import core.client_factory as cf
        except ImportError:
            import ai_trading_bot.core.client_factory as cf

        def _boom(*a, **kw):
            raise RuntimeError("alpaca sdk unhappy")

        monkeypatch.setattr(cf, "StockHistoricalDataClient", _boom, raising=False)
        monkeypatch.setattr(dp, "ALPACA_API_KEY", "k", raising=False)
        monkeypatch.setattr(dp, "ALPACA_SECRET_KEY", "s", raising=False)

        with caplog.at_level(logging.WARNING):
            provider = dp.HistoricalDataProvider()

        assert provider.api is None
        assert "RuntimeError" in caplog.text, "the failure type must reach the log"

    def test_the_credentials_are_never_written_to_the_log(self, monkeypatch, caplog):
        """⚠ The key and secret are passed straight to this constructor. An SDK that
        echoes its arguments back ("invalid credentials: <key>") would put the secret in
        a log file that ends up pasted into an issue. Log the type, never the message.
        """
        import core.data_provider as dp

        try:
            import core.client_factory as cf
        except ImportError:
            import ai_trading_bot.core.client_factory as cf

        def _leaky(key, secret):
            raise ValueError(f"invalid credentials: {key} / {secret}")

        monkeypatch.setattr(cf, "StockHistoricalDataClient", _leaky, raising=False)
        monkeypatch.setattr(dp, "ALPACA_API_KEY", "SECRET-KEY-XYZ", raising=False)
        monkeypatch.setattr(dp, "ALPACA_SECRET_KEY", "SECRET-VAL-ABC", raising=False)

        with caplog.at_level(logging.WARNING):
            dp.HistoricalDataProvider()

        assert "SECRET-KEY-XYZ" not in caplog.text
        assert "SECRET-VAL-ABC" not in caplog.text
