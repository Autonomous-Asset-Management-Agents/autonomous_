"""#3145 (2b) — persistent cache dir + cold-cache seed from the bundled snapshot."""

import gzip
import json
import os

from core.report import financials_feed as ff
from core.report import financials_snapshot as fs


def test_cache_dir_uses_userdata_when_env_set(monkeypatch, tmp_path):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    assert ff._resolve_cache_dir() == os.path.join(str(tmp_path), "financials_cache")


def test_cache_dir_falls_back_cwd_relative_when_unset(monkeypatch):
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    assert ff._resolve_cache_dir() == os.path.join("data", "financials_cache")


def test_seed_bundled_fundamentals_cold_then_noop(tmp_path):
    snap = tmp_path / "financials_snapshot.json.gz"
    with gzip.open(snap, "wt", encoding="utf-8") as h:
        json.dump(
            {"MSFT": {"results": [{"filing_date": "2026-01-01", "financials": {}}]}}, h
        )
    cache = tmp_path / "financials_cache"

    seeded = fs.seed_bundled_fundamentals(cache_dir=str(cache), snapshot_path=str(snap))
    assert seeded == 1
    assert (cache / "MSFT.json").exists()

    # warm cache -> no-op
    assert (
        fs.seed_bundled_fundamentals(cache_dir=str(cache), snapshot_path=str(snap)) == 0
    )


def test_bundled_snapshot_ships_and_seeds_real_symbols(tmp_path):
    """The committed bundle snapshot is present, non-trivial, and seeds a cold cache."""
    assert os.path.exists(
        fs.DEFAULT_SNAPSHOT_PATH
    ), "bundled snapshot missing from data/"
    snap = fs.load_snapshot(fs.DEFAULT_SNAPSHOT_PATH)
    assert snap and len(snap) > 400  # ~476 S&P symbols
    cache = tmp_path / "financials_cache"
    seeded = fs.seed_bundled_fundamentals(
        cache_dir=str(cache), snapshot_path=fs.DEFAULT_SNAPSHOT_PATH
    )
    assert seeded == len(snap)
    assert (cache / "MSFT.json").exists()
