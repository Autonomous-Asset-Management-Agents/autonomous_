"""TDD (ADR-OBS-01 / PR E): ``data_providers`` subsystem (VC-1 data-feed health).  # noqa: E501

Invariants under test:
  (a) The per-source OHLCV waterfall counters bump on a SUCCESSFUL fetch and on a  # noqa: E501
      FAILING fetch (ok/fail counts move, last_error_ts stamps only on failure).  # noqa: E501
  (b) SAFETY — the counter bump is PURE OBSERVATION: if the bump itself raises
      (monkeypatched to blow up), the real fetch STILL returns its normal result and  # noqa: E501
      the fallback waterfall STILL runs — the failure is swallowed, never perturbs  # noqa: E501
      the data path.
  (c) The ``data_providers`` subsystem appears in /engine-diagnostics and is fail-soft  # noqa: E501
      (a raising collector degrades to ``{"_error": ...}``, endpoint stays 200).  # noqa: E501
  (d) PRIVACY — no symbol / price / order text ever appears in the counters or the  # noqa: E501
      response (machine-only: source names, counts, timestamps, booleans, ages).  # noqa: E501

Auth is bypassed via ``app.dependency_overrides`` (same pattern as test_engine_diagnostics.py).  # noqa: E501
"""

import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_dp_telemetry():
    from core import data_provider_telemetry as t

    t.reset_data_provider_telemetry()
    yield
    t.reset_data_provider_telemetry()


# --- (a) per-source counters bump on ok + on fail -----------------------------  # noqa: E501


def test_source_counter_bumps_on_ok_and_fail():
    from core.data_provider_telemetry import bump_source, get_data_source_stats

    bump_source("alpaca", ok=True)
    bump_source("alpaca", ok=True)
    bump_source("polygon", ok=False)

    stats = get_data_source_stats()
    assert stats["alpaca"]["ok"] == 2
    assert stats["alpaca"]["fail"] == 0
    assert stats["alpaca"]["last_error_ts"] is None
    assert stats["polygon"]["ok"] == 0
    assert stats["polygon"]["fail"] == 1
    assert stats["polygon"]["last_error_ts"] is not None


def test_alpaca_fetch_success_bumps_ok_counter(monkeypatch):
    """A successful live Alpaca OHLCV fetch bumps the alpaca ok-counter (real fetch path)."""  # noqa: E501
    from core.data_provider import HistoricalDataProvider
    from core.data_provider_telemetry import get_data_source_stats

    idx = pd.to_datetime(["2026-01-02", "2026-01-03"])
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [10, 20],
        },
        index=idx,
    )

    class _Bars:
        @property
        def df(self):
            return df

    class _Api:
        def get_stock_bars(self, req):
            return _Bars()

    provider = HistoricalDataProvider(api=_Api())
    # Force the disk-cache miss branch: use a throwaway cache dir key.
    monkeypatch.setattr(provider, "data_cache", {})
    out = provider.get_data(
        "AAPL", pd.Timestamp("2026-01-05"), days=5, use_case="live"
    )  # noqa: E501
    assert not out.empty

    stats = get_data_source_stats()
    assert stats["alpaca"]["ok"] >= 1


def test_alpaca_fetch_exception_bumps_fail_counter(monkeypatch):
    """A raising Alpaca fetch bumps the alpaca fail-counter and still returns empty."""  # noqa: E501
    from core.data_provider import HistoricalDataProvider
    from core.data_provider_telemetry import get_data_source_stats

    class _Api:
        def get_stock_bars(self, req):
            raise RuntimeError("alpaca down")

    provider = HistoricalDataProvider(api=_Api())
    monkeypatch.setattr(provider, "data_cache", {})
    # No polygon key configured → waterfall exhausts, returns empty (byte-identical).  # noqa: E501
    out = provider.get_data(
        "AAPL", pd.Timestamp("2026-01-05"), days=5, use_case="live"
    )  # noqa: E501
    assert out.empty

    stats = get_data_source_stats()
    assert stats["alpaca"]["fail"] >= 1
    assert stats["alpaca"]["last_error_ts"] is not None


# --- (b) SAFETY: a counter failure must NEVER break the fetch / fallback -------  # noqa: E501


def test_counter_failure_does_not_break_fetch(monkeypatch):
    """Sabotage the bump → the real Alpaca fetch STILL returns its normal result."""  # noqa: E501
    import core.data_provider_telemetry as dpt
    from core.data_provider import HistoricalDataProvider

    idx = pd.to_datetime(["2026-01-02", "2026-01-03"])
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [1.0, 2.0],
            "low": [1.0, 2.0],
            "close": [1.0, 2.0],
            "volume": [10, 20],
        },
        index=idx,
    )

    class _Bars:
        @property
        def df(self):
            return df

    class _Api:
        def get_stock_bars(self, req):
            return _Bars()

    def _sabotage(*a, **k):
        raise RuntimeError("counter blew up")

    # Replace the low-level recorder used by the call-site guard.
    monkeypatch.setattr(dpt, "bump_source", _sabotage)

    provider = HistoricalDataProvider(api=_Api())
    monkeypatch.setattr(provider, "data_cache", {})
    out = provider.get_data(
        "AAPL", pd.Timestamp("2026-01-05"), days=5, use_case="live"
    )  # noqa: E501
    # Result is UNCHANGED despite the counter exploding.
    assert not out.empty
    assert list(out["close"]) == [1.0, 2.0]


def test_counter_failure_does_not_break_fallback_waterfall(monkeypatch):
    """Sabotage the bump AND fail Alpaca → the empty-fallback path STILL completes."""  # noqa: E501
    import core.data_provider_telemetry as dpt
    from core.data_provider import HistoricalDataProvider

    class _Api:
        def get_stock_bars(self, req):
            raise RuntimeError("alpaca down")

    def _sabotage(*a, **k):
        raise RuntimeError("counter blew up")

    monkeypatch.setattr(dpt, "bump_source", _sabotage)

    provider = HistoricalDataProvider(api=_Api())
    monkeypatch.setattr(provider, "data_cache", {})
    out = provider.get_data(
        "AAPL", pd.Timestamp("2026-01-05"), days=5, use_case="live"
    )  # noqa: E501
    # The waterfall still exhausts cleanly and returns the empty frame (byte-identical).  # noqa: E501
    assert out.empty


# --- (c) data_providers subsystem present + fail-soft -------------------------  # noqa: E501


def test_data_providers_subsystem_present(client_authed):
    body = client_authed.get("/engine-diagnostics").json()
    assert "data_providers" in body, "missing subsystem: data_providers"
    dp = body["data_providers"]
    assert "sources" in dp
    assert "alpaca" in dp["sources"]
    assert "vix_present" in dp
    assert "vix_regime_age_seconds" in dp
    assert "universe_source" in dp
    assert "universe_count" in dp


def test_data_providers_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_data_providers", _boom)
    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    assert r.json()["data_providers"] == {"_error": "RuntimeError"}


# --- VIX / universe / specialist telemetry ------------------------------------  # noqa: E501


def test_regime_freshness_reads_cached_state():
    from core.data_provider_telemetry import (  # noqa: E501
        get_regime_freshness,
        mark_regime_update,
    )

    before = get_regime_freshness()
    assert before["vix_present"] is False
    assert before["vix_regime_age_seconds"] is None

    mark_regime_update(vix_present=True)
    after = get_regime_freshness()
    assert after["vix_present"] is True
    assert after["vix_regime_age_seconds"] is not None
    assert after["vix_regime_age_seconds"] >= 0


def test_universe_state_records_source_and_count():
    from core.data_provider_telemetry import get_universe_state, mark_universe

    mark_universe("wikipedia", 503)
    st = get_universe_state()
    assert st["universe_source"] == "wikipedia"
    assert st["universe_count"] == 503


def test_available_symbols_records_universe(monkeypatch):
    """get_available_symbols records the universe source + count as a pure side-effect."""  # noqa: E501
    from core.data_provider import HistoricalDataProvider
    from core.data_provider_telemetry import get_universe_state

    provider = HistoricalDataProvider()
    # No trading_api → alpaca returns None → wikipedia path. Stub wiki to a fixed list.  # noqa: E501
    monkeypatch.setattr(
        provider, "get_sp500_symbols", lambda: ["AAA", "BBB", "CCC"]
    )  # noqa: E501
    provider.get_available_symbols()

    st = get_universe_state()
    assert st["universe_source"] in ("alpaca", "wikipedia", "fallback")
    assert isinstance(st["universe_count"], int)
    assert st["universe_count"] >= 3


def test_specialist_source_stats_bump_and_bounded():
    from core.data_provider_telemetry import (
        bump_specialist_source,
        get_specialist_source_stats,
    )

    bump_specialist_source("edgar_form4", ok=True)
    bump_specialist_source("edgar_form4", ok=False)
    bump_specialist_source("polygon_news", ok=True)

    stats = get_specialist_source_stats()
    assert stats["edgar_form4"] == {"ok": 1, "fail": 1}
    assert stats["polygon_news"] == {"ok": 1, "fail": 0}

    # Bounded: flooding with distinct names never exceeds the cap.
    for i in range(50):
        bump_specialist_source(f"junk_{i}", ok=True)
    assert len(get_specialist_source_stats()) <= 16


def test_specialist_bump_is_fail_safe(monkeypatch):
    """A broken specialist store must never raise into the specialist gather."""  # noqa: E501
    import core.data_provider_telemetry as dpt

    monkeypatch.setattr(dpt, "_specialist_stats", None)
    dpt.bump_specialist_source("edgar_form4", ok=True)  # must not raise


# --- (d) PRIVACY: no symbol / price text anywhere -----------------------------  # noqa: E501


def test_privacy_no_symbol_or_price_in_body(client_authed):
    """The data_providers body is machine-only — source names + counts, no symbol/price."""  # noqa: E501
    from core.data_provider_telemetry import bump_source, mark_universe

    bump_source("alpaca", ok=True)
    mark_universe("wikipedia", 500)

    body = client_authed.get("/engine-diagnostics").json()
    serialized = json.dumps(body)
    # A specific symbol / price value must never leak into the machine view.
    assert "TSLA-SECRET" not in serialized
    for forbidden in ("price", "equity", "user_id", "order_content"):
        assert forbidden not in serialized
