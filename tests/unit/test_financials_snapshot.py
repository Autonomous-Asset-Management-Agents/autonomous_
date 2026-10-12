"""#3145 (bundle) — offline snapshot: build from a warm cache, seed a cold one."""

import gzip
import json
import os
import tempfile

from core.report import financials_snapshot as snap


def _write(cache_dir, symbol, payload):
    with open(os.path.join(cache_dir, f"{symbol}.json"), "w", encoding="utf-8") as h:
        json.dump(payload, h)


def _payload(rev):
    return {
        "symbol": "X",
        "results": [
            {
                "filing_date": "2026-01-01",
                "financials": {"income_statement": {"revenues": {"value": rev}}},
            }
        ],
    }


def test_build_then_seed_roundtrip_gz():
    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(100))
    _write(warm, "AAPL", _payload(200))
    out = os.path.join(tempfile.mkdtemp(), "snap.json.gz")
    assert snap.build_snapshot(warm, out) == 2
    # gzip-compressed + reloads
    with gzip.open(out, "rt", encoding="utf-8") as h:
        assert set(json.load(h)) == {"MSFT", "AAPL"}

    cold = tempfile.mkdtemp()
    assert snap.seed_cache_from_snapshot(out, cold) == 2
    with open(os.path.join(cold, "MSFT.json"), encoding="utf-8") as h:
        assert (
            json.load(h)["results"][0]["financials"]["income_statement"]["revenues"][
                "value"
            ]
            == 100
        )


def test_seed_is_noop_on_warm_cache():
    """A warm cache is never overwritten — a nightly refresh always beats the bundle."""
    out = os.path.join(tempfile.mkdtemp(), "snap.json")
    src = tempfile.mkdtemp()
    _write(src, "MSFT", _payload(100))
    snap.build_snapshot(src, out)

    warm = tempfile.mkdtemp()
    _write(warm, "MSFT", _payload(999))  # already present, different value
    assert snap.seed_cache_from_snapshot(out, warm) == 0
    with open(os.path.join(warm, "MSFT.json"), encoding="utf-8") as h:
        assert (
            json.load(h)["results"][0]["financials"]["income_statement"]["revenues"][
                "value"
            ]
            == 999
        )


def test_seed_missing_snapshot_is_zero():
    assert (
        snap.seed_cache_from_snapshot("/no/such/snapshot.json.gz", tempfile.mkdtemp())
        == 0
    )
