# tests/unit/test_report_financials_feed.py
# RPT-2 (#2001, Epic #1998) — Polygon vX fundamentals feed.
#
# Test surfaces (fully deterministic — the network is MOCKED, never hit):
#   1. GOLDEN: derive_fundamentals reproduces the audited NVDA FY2026 benchmark
#      number-exact from a committed REAL vX response fixture
#      (215.94B / +65.5%, 120.07B / +64.7%, diluted EPS 4.90, P/E 40.5).
#   2. Point-in-time (PIT): a filing_date AFTER as_of is excluded; the prior
#      annual period drives YoY; no PIT-valid filing -> honest None.
#   3. TTM-drop: synthetic trailing-twelve-month rows are ignored.
#   4. Network seam: mocked 200 -> dict; timeout / HTTP error / no-key -> None.
#   5. Rate-limit backoff: 429 then 200 retries (5/min free tier); exhausted -> None.
#   6. Reader adapter: CachedFundamentalsReader reads the nightly cache and feeds
#      RPT-1's FactSet.fundamentals via the FactSetReader DI seam.

import json
import os
from datetime import date

import pytest

from core.report.fact_set import InMemoryDataContext, build_fact_set
from core.report.financials_feed import (
    CachedFundamentalsReader,
    cache_path,
    derive_fundamentals,
    fetch_financials_vX,
    fetch_raw_financials,
)

# ---------------------------------------------------------------------------
# Fixtures (committed REAL NVDA vX response — 2 annual periods)
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
NVDA_CLOSE = 198.45  # RPT-1 as-of close (2026-05-01) — used for P/E & P/S
AS_OF = date(2026, 5, 1)  # after the FY2026 filing (2026-02-25)


def _load_results():
    with open(os.path.join(_FX, "nvda_financials_vx.json"), encoding="utf-8") as f:
        return json.load(f)["results"]


# ---------------------------------------------------------------------------
# Mock HTTP plumbing — the ONLY substitute for the network in this suite.
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    """Returns queued responses; records the params of each .get call."""

    def __init__(self, responses, raise_exc=None):
        self._responses = list(responses)
        self._raise = raise_exc
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self._raise is not None:
            raise self._raise
        return self._responses.pop(0)


def _ok_session(body_results):
    return _FakeSession([_FakeResponse(200, {"status": "OK", "results": body_results})])


# ===========================================================================
# 1. GOLDEN — deterministic NVDA benchmark reproduction
# ===========================================================================
def test_derive_reproduces_nvda_benchmark():
    f = derive_fundamentals(_load_results(), AS_OF, close=NVDA_CLOSE)
    assert f is not None
    assert f["fiscal_year"] == "2026"  # faithful pass-through (Polygon returns a str)
    assert f["filing_date"] == "2026-02-25"
    assert f["revenue"] == 215938000000.0
    assert f["net_income"] == 120067000000.0
    assert f["eps_diluted"] == 4.9
    # margins
    assert round(f["gross_margin"] * 100, 1) == 71.1
    assert round(f["operating_margin"] * 100, 1) == 60.4
    assert round(f["net_margin"] * 100, 1) == 55.6
    # YoY vs FY2025 (real prior period in the fixture)
    assert round(f["revenue_yoy"] * 100, 1) == 65.5
    assert round(f["net_income_yoy"] * 100, 1) == 64.7
    assert round(f["eps_yoy"], 3) == 0.667
    # valuation (needs close)
    assert round(f["pe"], 1) == 40.5
    assert round(f["ps"], 1) == 22.5
    # balance sheet (real reported values)
    assert f["total_assets"] == 206803000000.0
    assert f["total_liabilities"] == 49510000000.0
    assert f["total_equity"] == 157293000000.0


def test_determinism_identical_input():
    a = derive_fundamentals(_load_results(), AS_OF, close=NVDA_CLOSE)
    b = derive_fundamentals(_load_results(), AS_OF, close=NVDA_CLOSE)
    assert a == b


def test_pe_ps_none_without_close():
    f = derive_fundamentals(_load_results(), AS_OF)  # no close
    assert f["pe"] is None
    assert f["ps"] is None
    # price-independent facts still present
    assert f["revenue"] == 215938000000.0
    assert round(f["net_margin"] * 100, 1) == 55.6


# ===========================================================================
# 2. Point-in-time (PIT)
# ===========================================================================
def test_pit_excludes_future_filing():
    # as_of BEFORE the FY2026 filing (2026-02-25) but after FY2025 (2025-02-26):
    # the FY2026 row must be excluded; FY2025 becomes current, no prior -> YoY None.
    f = derive_fundamentals(_load_results(), date(2026, 1, 1), close=NVDA_CLOSE)
    assert f["fiscal_year"] == "2025"
    assert f["filing_date"] == "2025-02-26"
    assert f["revenue"] == 130497000000.0
    assert f["revenue_yoy"] is None  # no prior period in-window
    assert f["net_income_yoy"] is None


def test_pit_no_valid_filing_returns_none():
    assert derive_fundamentals(_load_results(), date(2020, 1, 1)) is None


def test_pit_empty_results_returns_none():
    assert derive_fundamentals([], AS_OF) is None


# ===========================================================================
# 3. TTM-drop
# ===========================================================================
def test_ttm_rows_dropped():
    results = _load_results()
    # inject a synthetic TTM row filed most-recently — it must be ignored.
    ttm = {
        "timeframe": "ttm",
        "filing_date": "2026-04-30",
        "fiscal_year": 2026,
        "financials": {"income_statement": {"revenues": {"value": 999.0}}},
    }
    f = derive_fundamentals([ttm] + results, AS_OF, close=NVDA_CLOSE)
    assert f["timeframe"] == "annual"
    assert f["revenue"] == 215938000000.0  # annual, not the 999.0 TTM


# ===========================================================================
# 4. Network seam (mocked)
# ===========================================================================
def test_fetch_returns_dict_and_sends_pit_params():
    sess = _ok_session(_load_results())
    f = fetch_financials_vX(
        "NVDA", AS_OF, api_key="TESTKEY", close=NVDA_CLOSE, session=sess
    )
    assert f["revenue"] == 215938000000.0
    # the request must carry the PIT + timeframe query params
    params = sess.calls[0]["params"]
    assert params["ticker"] == "NVDA"
    assert params["filing_date.lte"] == "2026-05-01"
    assert params["timeframe"] == "annual"
    assert params["apiKey"] == "TESTKEY"


def test_fetch_timeout_returns_none():
    sess = _FakeSession([], raise_exc=TimeoutError("timed out"))
    assert fetch_financials_vX("NVDA", AS_OF, api_key="K", session=sess) is None


def test_fetch_http_error_returns_none():
    sess = _FakeSession([_FakeResponse(500, {"error": "boom"})])
    assert fetch_financials_vX("NVDA", AS_OF, api_key="K", session=sess) is None


def test_no_api_key_returns_none(monkeypatch):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    sess = _ok_session(_load_results())
    assert fetch_financials_vX("NVDA", AS_OF, api_key=None, session=sess) is None
    assert sess.calls == []  # never hit the network without a key


# ===========================================================================
# 5. Rate-limit backoff (5/min free tier)
# ===========================================================================
def test_rate_limit_backoff_then_success():
    sess = _FakeSession(
        [
            _FakeResponse(429, headers={"Retry-After": "0"}),
            _FakeResponse(429, headers={"Retry-After": "0"}),
            _FakeResponse(200, {"status": "OK", "results": _load_results()}),
        ]
    )
    slept = []
    f = fetch_financials_vX(
        "NVDA", AS_OF, api_key="K", session=sess, sleep=slept.append, backoff_base=0.01
    )
    assert f["revenue"] == 215938000000.0
    assert len(slept) == 2  # backed off twice before the 200


def test_rate_limit_exhausted_returns_none():
    sess = _FakeSession(
        [_FakeResponse(429, headers={"Retry-After": "0"}) for _ in range(6)]
    )
    slept = []
    out = fetch_raw_financials(
        "NVDA",
        AS_OF,
        api_key="K",
        session=sess,
        max_retries=3,
        sleep=slept.append,
        backoff_base=0.01,
    )
    assert out is None
    assert len(slept) == 3  # exactly max_retries backoffs, then give up


# ===========================================================================
# 6. Reader adapter -> FactSet DI seam
# ===========================================================================
def _write_cache(tmp_path, symbol, results):
    p = cache_path(str(tmp_path), symbol)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"symbol": symbol, "results": results}, f)
    return p


def test_cached_reader_reads_and_pit(tmp_path):
    _write_cache(tmp_path, "NVDA", _load_results())
    reader = CachedFundamentalsReader(str(tmp_path), close_lookup={"NVDA": NVDA_CLOSE})
    f = reader.get_fundamentals("NVDA", AS_OF)
    assert f["revenue"] == 215938000000.0
    assert round(f["pe"], 1) == 40.5
    # PIT applies at read time too
    older = reader.get_fundamentals("NVDA", date(2026, 1, 1))
    assert older["fiscal_year"] == "2025"


def test_cached_reader_missing_symbol_returns_none(tmp_path):
    reader = CachedFundamentalsReader(str(tmp_path))
    assert reader.get_fundamentals("ZZZZ", AS_OF) is None


def test_reader_feeds_factset_fundamentals(tmp_path):
    """RPT-2 wires RPT-1's optional fundamentals field via the FactSetReader seam."""
    _write_cache(tmp_path, "NVDA", _load_results())
    fund_reader = CachedFundamentalsReader(
        str(tmp_path), close_lookup={"NVDA": NVDA_CLOSE}
    )

    # Compose: everything else in-memory, fundamentals delegated to the feed reader.
    class _Composite(InMemoryDataContext):
        def get_fundamentals(self, symbol, as_of):
            return fund_reader.get_fundamentals(symbol, as_of)

    ctx = _Composite(bars=[(date(2026, 5, 1), 198.45)])
    fs = build_fact_set("NVDA", AS_OF, ctx)
    assert fs.fundamentals.value["revenue"] == 215938000000.0
    assert "polygon vX financials" in fs.fundamentals.src


def test_factset_fundamentals_honest_gap_when_unwired():
    """Without a wired feed, the field stays an honest None (RPT-1 contract)."""
    ctx = InMemoryDataContext(bars=[(date(2026, 5, 1), 198.45)])
    fs = build_fact_set("NVDA", AS_OF, ctx)
    assert fs.fundamentals.value is None
    assert "not wired" in fs.fundamentals.src


@pytest.mark.parametrize("bad", [None, {}, {"financials": {}}])
def test_derive_graceful_on_missing_statements(bad):
    if bad is None:
        assert derive_fundamentals([], AS_OF) is None
        return
    row = {"timeframe": "annual", "filing_date": "2026-02-25", "fiscal_year": 2026}
    row.update(bad)
    f = derive_fundamentals([row], AS_OF, close=NVDA_CLOSE)
    # a filing exists -> a dict is returned, but derived numbers are honest Nones
    assert f is not None
    assert f["revenue"] is None
    assert f["gross_margin"] is None
    assert f["pe"] is None


# ===========================================================================
# 7. Nightly prefetch orchestration (mocked feed + injected sleep — no network)
# ===========================================================================
from scripts import prefetch_financials_universe as prefetch  # noqa: E402


def test_prefetch_fetches_writes_and_throttles(tmp_path, monkeypatch):
    monkeypatch.setattr(
        prefetch, "fetch_raw_financials", lambda *a, **k: _load_results()
    )
    slept = []
    stats = prefetch.prefetch_universe(
        cache_dir=str(tmp_path),
        symbols=["NVDA", "AAPL"],
        as_of=AS_OF,
        api_key="K",
        limit=8,
        timeframe="annual",
        throttle_seconds=12.0,
        skip_fresh_days=25,
        force=False,
        dry_run=False,
        sleep=slept.append,
    )
    assert stats["fetched"] == 2 and stats["written"] == 2
    assert os.path.exists(cache_path(str(tmp_path), "NVDA"))
    # throttle fires between symbols, not after the last one
    assert slept == [12.0]


def test_prefetch_skips_fresh(tmp_path, monkeypatch):
    _write_cache(tmp_path, "NVDA", _load_results())
    # stamp it fresh
    p = cache_path(str(tmp_path), "NVDA")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"results": _load_results(), "fetched_at": datetime_now_iso()}, f)

    def _boom(*a, **k):  # must NOT be called for a fresh symbol
        raise AssertionError("fetch_raw_financials called for a fresh symbol")

    monkeypatch.setattr(prefetch, "fetch_raw_financials", _boom)
    stats = prefetch.prefetch_universe(
        cache_dir=str(tmp_path),
        symbols=["NVDA"],
        as_of=AS_OF,
        api_key="K",
        limit=8,
        timeframe="annual",
        throttle_seconds=12.0,
        skip_fresh_days=25,
        force=False,
        dry_run=False,
        sleep=lambda s: None,
    )
    assert stats["skipped"] == 1 and stats["fetched"] == 0


def test_prefetch_honest_gap_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch, "fetch_raw_financials", lambda *a, **k: None)
    stats = prefetch.prefetch_universe(
        cache_dir=str(tmp_path),
        symbols=["ZZZZ"],
        as_of=AS_OF,
        api_key="K",
        limit=8,
        timeframe="annual",
        throttle_seconds=0.0,
        skip_fresh_days=0,
        force=False,
        dry_run=False,
        sleep=lambda s: None,
    )
    assert stats["failed"] == 1
    assert not os.path.exists(cache_path(str(tmp_path), "ZZZZ"))


def test_write_if_changed_is_idempotent(tmp_path):
    p = cache_path(str(tmp_path), "NVDA")
    assert prefetch._write_if_changed(p, "NVDA", _load_results(), AS_OF) is True
    # same results -> no rewrite
    assert prefetch._write_if_changed(p, "NVDA", _load_results(), AS_OF) is False


def datetime_now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
