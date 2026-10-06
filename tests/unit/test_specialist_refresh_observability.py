"""Robustness/observability for the specialist report-refresh loop.

Root cause (HIG live incident, 2026-08): a held position had NO cached report, so the
SpecialistAlphaAgent silently abstained (weight 0) in that symbol's Round-Table vote. The
report DID generate fine in isolation — the miss was an operational gap: ``_refresh_symbol``
swallowed a per-symbol failure into a bare WARNING with NO durable, queryable marker, so a
symbol could lack a report indefinitely and nobody could see it.

This locks in:
  1. a per-symbol refresh-failure record (count + last error), cleared on the next success;
  2. ``refresh_failures()`` + a ``refresh_failures`` count in ``get_status`` (observability);
  3. ``symbols_missing_reports()`` — the "high-priority (e.g. held) symbol with no report yet"
     signal, so the exact HIG situation is detectable instead of silent.

Fully deterministic: a stub agent is injected into ``_agents`` (no network / LLM / GPU).
Report persistence is OFF by default (REPORT_CACHE_PERSIST_ENABLED), so no disk I/O.
"""

import asyncio
import types

from core.specialist_registry import StockSpecialistRegistry


def _registry(symbols):
    # report_only=False → _refresh_symbol takes the research() branch we stub out.
    return StockSpecialistRegistry(list(symbols), "", report_only=False)


class _RaisingAgent:
    async def research(self):
        raise RuntimeError("boom: news fetch timed out")


class _GoodAgent:
    def __init__(self, symbol):
        self._symbol = symbol

    async def research(self):
        return types.SimpleNamespace(
            symbol=self._symbol,
            escalate=False,
            escalate_reason="",
            recommendation="hold",
            sentiment_score=50.0,
        )


def test_refresh_failure_is_recorded_and_visible():
    reg = _registry(["HIG"])
    reg._agents["HIG"] = _RaisingAgent()

    asyncio.run(reg._refresh_symbol("HIG"))

    failures = reg.refresh_failures()
    assert "HIG" in failures
    assert failures["HIG"]["count"] == 1
    assert "boom" in failures["HIG"]["last_error"]
    # surfaced to the monitoring/API status payload
    assert reg.get_status()["refresh_failures"] == 1


def test_repeated_failures_increment_count():
    reg = _registry(["HIG"])
    reg._agents["HIG"] = _RaisingAgent()

    asyncio.run(reg._refresh_symbol("HIG"))
    asyncio.run(reg._refresh_symbol("HIG"))

    assert reg.refresh_failures()["HIG"]["count"] == 2


def test_successful_refresh_clears_prior_failure():
    reg = _registry(["HIG"])
    reg._agents["HIG"] = _RaisingAgent()
    asyncio.run(reg._refresh_symbol("HIG"))
    assert "HIG" in reg.refresh_failures()

    # next cycle succeeds → the stale failure marker must be cleared and the report cached
    reg._agents["HIG"] = _GoodAgent("HIG")
    asyncio.run(reg._refresh_symbol("HIG"))

    assert "HIG" not in reg.refresh_failures()
    assert reg.get_report("HIG") is not None
    assert reg.get_status()["refresh_failures"] == 0


def test_symbols_missing_reports_flags_high_priority_without_report():
    reg = _registry(["HIG", "AAPL"])
    # AAPL has a report; HIG does not. Both are high-priority (as held positions would be).
    reg._agents["AAPL"] = _GoodAgent("AAPL")
    asyncio.run(reg._refresh_symbol("AAPL"))
    reg.update_priority(["HIG", "AAPL"])

    missing = reg.symbols_missing_reports()
    assert missing == ["HIG"]
    # also reflected in the status payload
    assert reg.get_status()["high_priority_missing_reports"] == ["HIG"]
