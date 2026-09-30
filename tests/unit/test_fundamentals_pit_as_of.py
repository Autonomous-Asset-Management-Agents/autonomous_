"""#3145 (3) — fundamentals are read AS OF the evaluation date (no backtest look-ahead)."""

import asyncio
from datetime import date, datetime, timezone

import core.report.financials_feed as ff
import core.round_table.agents as ag


class _FakeReader:
    captured: dict = {}

    def __init__(self, close_lookup=None):
        pass

    def get_fundamentals(self, symbol, as_of):
        _FakeReader.captured["as_of"] = as_of
        return {"pe": 20.0}


def test_read_pit_uses_the_passed_as_of(monkeypatch):
    _FakeReader.captured = {}
    monkeypatch.setattr(ff, "CachedFundamentalsReader", _FakeReader)
    ag._read_pit_fundamentals("MSFT", 100.0, "2026-07-01T00:00:00+00:00")
    assert _FakeReader.captured["as_of"] == date(2026, 7, 1)


def test_read_pit_falls_back_to_today_when_absent(monkeypatch):
    _FakeReader.captured = {}
    monkeypatch.setattr(ff, "CachedFundamentalsReader", _FakeReader)
    ag._read_pit_fundamentals("MSFT", 100.0, None)  # live: no eval date -> today
    assert _FakeReader.captured["as_of"] == datetime.now(timezone.utc).date()


def test_bad_as_of_warns_and_falls_back_to_today(monkeypatch, caplog):
    """§5.6 (review POLICY-01): an unparseable eval date must WARN (never silent) and
    fall back to today, so the residual look-ahead case is visible."""
    _FakeReader.captured = {}
    monkeypatch.setattr(ff, "CachedFundamentalsReader", _FakeReader)
    with caplog.at_level("WARNING"):
        ag._read_pit_fundamentals("MSFT", 100.0, "not-a-date")
    assert _FakeReader.captured["as_of"] == datetime.now(timezone.utc).date()
    assert any("falling back to today" in r.getMessage() for r in caplog.records)


def test_agent_threads_current_time_into_reader(monkeypatch):
    seen = {}

    def _fake(symbol, close, as_of=None):
        seen["as_of"] = as_of
        return None

    monkeypatch.setattr(ag, "_read_pit_fundamentals", _fake)
    state = {
        "symbol": "X",
        "ohlc": {"close": 100.0},
        "current_time": "2026-07-01T00:00:00+00:00",
    }
    asyncio.run(ag.FundamentalsAgent().vote(state))
    assert seen["as_of"] == "2026-07-01T00:00:00+00:00"
