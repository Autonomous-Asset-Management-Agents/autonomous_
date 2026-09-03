"""#3145 — the nightly prefetch producer can source from SEC EDGAR.

``prefetch_universe`` takes an injectable ``fetch_results`` (symbol -> Optional[list]);
main() wires a SEC-EDGAR fetcher when ``FUNDAMENTALS_SOURCE=sec`` (the default). The
legacy Polygon path stays byte-identical when no fetcher is injected.
"""

import json
import os
import tempfile
from datetime import date

import scripts.prefetch_financials_universe as pf


def test_sec_producer_writes_cache_with_source_label():
    tmp = tempfile.mkdtemp()
    rows = [
        {
            "filing_date": "2026-07-29",
            "fiscal_period": "FY",
            "timeframe": "annual",
            "financials": {"income_statement": {"revenues": {"value": 100}}},
        }
    ]

    def fake(symbol):
        return rows if symbol == "MSFT" else None  # NOPE -> honest gap

    stats = pf.prefetch_universe(
        cache_dir=tmp,
        symbols=["MSFT", "NOPE"],
        as_of=date(2026, 9, 1),
        api_key=None,
        limit=8,
        timeframe="annual",
        throttle_seconds=0,
        skip_fresh_days=0,
        force=True,
        dry_run=False,
        fetch_results=fake,
        source_label="sec edgar companyfacts",
        sleep=lambda _s: None,
    )

    assert stats["written"] == 1
    assert stats["failed"] == 1
    with open(os.path.join(tmp, "MSFT.json"), encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["source"] == "sec edgar companyfacts"
    assert payload["results"] == rows
    assert not os.path.exists(os.path.join(tmp, "NOPE.json"))


def test_default_producer_still_uses_polygon(monkeypatch):
    """No injected fetcher -> the legacy Polygon fetch is used (back-compat)."""
    seen = {}

    def _stub(symbol, as_of, **kwargs):
        seen["symbol"] = symbol
        return []

    monkeypatch.setattr(pf, "fetch_raw_financials", _stub)
    tmp = tempfile.mkdtemp()
    pf.prefetch_universe(
        cache_dir=tmp,
        symbols=["AAPL"],
        as_of=date(2026, 9, 1),
        api_key="key",
        limit=8,
        timeframe="annual",
        throttle_seconds=0,
        skip_fresh_days=0,
        force=True,
        dry_run=False,
        sleep=lambda _s: None,
    )
    assert seen["symbol"] == "AAPL"
