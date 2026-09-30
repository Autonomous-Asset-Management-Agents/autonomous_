"""Report-freeze fix: the preserve-rich-over-degraded guard must NOT freeze a STALE or DEGRADED
existing report over a fresh one.

Proven live (2026-08): a HELD symbol (PM) kept a 5-day-old report whose cached news was WRONG — "PM"
collided with a "6 p.m. newscast" headline — because ``_should_preserve_existing`` returned True even
though the existing report was itself ``degraded`` and days stale. The freshly-fetched CORRECT Philip
Morris news (the query fix works) was therefore discarded every refresh. The guard must only shield a
GOOD, FRESH report against a temporary degraded refresh — never a stale/degraded one.

Pure-function tests (no network/LLM) plus a caller integration test.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import core.specialist_registry as reg_mod
from core.specialist_registry import StockSpecialistRegistry
from core.stock_specialist import SpecialistReport


def _rich(degraded=False, age_seconds=0, sym="PM"):
    return SpecialistReport(
        symbol=sym,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        bull_case="A supported bull case with real substance.",
        investment_thesis="A supported thesis with real substance.",
        sentiment_score=60.0,
        degraded=degraded,
    )


def _degraded_incoming(sym="PM"):
    return SpecialistReport(
        symbol=sym,
        updated_at=datetime.now(timezone.utc),
        bull_case="No supported bull case from this news cycle yet.",
        degraded=True,
        sentiment_score=50.0,
    )


def test_degraded_existing_is_never_preserved():
    # The PM freeze — the existing report is ITSELF degraded, so it must not shield the fresh one.
    assert (
        reg_mod._should_preserve_existing(_rich(degraded=True), _degraded_incoming())
        is False
    )


def test_stale_existing_is_never_preserved():
    # A stale existing (older than the high-prio max age) must yield to a fresh report, even a
    # degraded one — a fresh thin report with current inputs beats a stale one with wrong news.
    assert (
        reg_mod._should_preserve_existing(
            _rich(), _degraded_incoming(), existing_stale=True
        )
        is False
    )


def test_fresh_nondegraded_rich_existing_is_still_preserved():
    # Intended behaviour unchanged: a good, fresh, non-degraded report still shields against a
    # transient degraded refresh (no flicker to worse content).
    assert (
        reg_mod._should_preserve_existing(
            _rich(), _degraded_incoming(), existing_stale=False
        )
        is True
    )


def test_refresh_overwrites_a_degraded_stale_existing_with_the_fresh_report(
    tmp_path, monkeypatch
):
    """End-to-end: a held symbol stuck on a degraded, stale report gets the fresh report written."""
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    with patch.object(reg_mod, "_persist_enabled", return_value=True):
        r = StockSpecialistRegistry(["PM"], gemini_api_key="x")
        stale_degraded = _rich(degraded=True, age_seconds=r._high_prio_max_age + 3600)
        stale_degraded.bull_case = "OLD wrong-news bull case."
        r._reports["PM"] = stale_degraded
        r._persist_report(stale_degraded)

        fresh = _degraded_incoming()  # fresh (now), correct-news, but flagged degraded
        fresh.bull_case = "FRESH correct-news bull case."
        with patch.object(reg_mod.StockSpecialistAgent, "research", return_value=fresh):
            asyncio.run(r._refresh_symbol("PM"))

        # The fresh report replaced the frozen one (RAM + disk) — no more freeze.
        assert r.get_report("PM").bull_case == "FRESH correct-news bull case."
        disk = json.loads(
            (tmp_path / "report_cache" / "PM.json").read_text(encoding="utf-8")
        )
        assert disk["bull_case"] == "FRESH correct-news bull case."
