"""Benchmark cash-flow fetch wiring (#1957): the Alpaca calls must pass ``cashflow_types`` as the
STRING ``"CSD,CSW"`` (alpaca-py 0.43.2 rejects a list) and parse the ``cashflow`` dict per day.

Both fetchers read the module-global ``engine``; we patch it with a fake broker whose
``get_portfolio_history`` records the request and returns a canned ``PortfolioHistory``-shaped
object — no network, no real SDK call path beyond request construction.
"""

from types import SimpleNamespace

import pytest

from core.engine import api_routes

_TS_D1 = 1767312000  # 2026-01-02 UTC
_TS_D2 = 1767398400  # 2026-01-03 UTC


class _FakeApi:
    def __init__(self, hist):
        self._hist = hist
        self.last_request = None

    def get_portfolio_history(self, request):
        self.last_request = request
        return self._hist


@pytest.fixture
def patched_engine(monkeypatch):
    def _install(hist):
        api = _FakeApi(hist)
        monkeypatch.setattr(api_routes, "engine", SimpleNamespace(api=api))
        return api

    return _install


def test_live_cashflows_parses_and_uses_string_param(patched_engine):
    hist = SimpleNamespace(
        timestamp=[_TS_D1, _TS_D2],
        equity=[10_000.0, 15_000.0],
        cashflow={"CSD": [0.0, 5_000.0], "CSW": [0.0, 0.0]},
    )
    api = patched_engine(hist)

    out = api_routes._get_live_cashflows()

    assert out == {"2026-01-02": 0.0, "2026-01-03": 5_000.0}
    # Regression guard: a list here would raise pydantic ValidationError in alpaca-py 0.43.2.
    assert isinstance(api.last_request.cashflow_types, str)
    assert api.last_request.cashflow_types == "CSD,CSW"


def test_live_cashflows_failsoft_without_engine(monkeypatch):
    monkeypatch.setattr(api_routes, "engine", None)
    assert api_routes._get_live_cashflows() == {}


def test_inception_equity_backfill_carries_cashflow(patched_engine):
    hist = SimpleNamespace(
        timestamp=[_TS_D1, _TS_D2],
        equity=[10_000.0, 15_000.0],
        cashflow={"CSD": [0.0, 5_000.0]},
    )
    api = patched_engine(hist)

    result = api_routes._get_inception_equity()

    assert result is not None
    inception_date, inception_equity, backfill = result
    assert inception_date == "2026-01-02"
    assert inception_equity == pytest.approx(10_000.0)
    # every backfill point now carries the day's net cash-flow for TWR + markers
    assert backfill[0] == {"date": "2026-01-02", "equity": 10_000.0, "cashflow": 0.0}
    assert backfill[1] == {
        "date": "2026-01-03",
        "equity": 15_000.0,
        "cashflow": 5_000.0,
    }
    assert api.last_request.cashflow_types == "CSD,CSW"
