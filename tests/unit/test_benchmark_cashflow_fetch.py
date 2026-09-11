# flake8: noqa
"""Benchmark cash-flow fetch wiring (#2717): deposits/withdrawals come from the Alpaca
**account-activities API** (``GET /v2/account/activities/{CSD,CSW}``) — NOT portfolio-history's
``cashflow`` field, which comes back empty in practice (the #2718/#2735 bug). The fetcher reads the
module-global ``engine``; we patch it with a fake broker whose ``get`` returns canned activity rows.
"""

from types import SimpleNamespace

import pytest

from core.engine import api_routes

_TS_D1 = 1767312000  # 2026-01-02 UTC
_TS_D2 = 1767398400  # 2026-01-03 UTC


class _FakeApi:
    """Fake alpaca client: ``get(path)`` returns activity rows per CSD/CSW; get_portfolio_history
    returns a canned equity history (no cashflow — that's the point)."""

    def __init__(self, csd=None, csw=None, hist=None):
        self._csd = csd or []
        self._csw = csw or []
        self._hist = hist
        self.paths = []

    def get(self, path, *a, **k):
        self.paths.append(path)
        if path.endswith("/CSD"):
            return self._csd
        if path.endswith("/CSW"):
            return self._csw
        return []

    def get_portfolio_history(self, request):
        self.last_request = request
        return self._hist


def _patch(monkeypatch, api):
    monkeypatch.setattr(api_routes, "engine", SimpleNamespace(api=api))
    return api


def test_live_cashflows_reads_account_activities(monkeypatch):
    api = _patch(
        monkeypatch,
        _FakeApi(
            csd=[
                {"activity_type": "CSD", "date": "2026-08-07", "net_amount": "115.66"},
            ],
            csw=[
                {"activity_type": "CSW", "date": "2026-08-09", "net_amount": "-40"},
            ],
        ),
    )

    out = api_routes._get_live_cashflows()

    assert out == {"2026-08-07": 115.66, "2026-08-09": -40.0}
    # hit ALL activity endpoints (CSD, CSW, FEE), not portfolio-history's (empty) cashflow
    assert api.paths == [
        "/account/activities/CSD",
        "/account/activities/CSW",
        "/account/activities/FEE",
    ]


def test_live_cashflows_failsoft_without_engine(monkeypatch):
    monkeypatch.setattr(api_routes, "engine", None)
    assert api_routes._get_live_cashflows() == {}


def test_live_cashflows_one_type_failing_keeps_the_other(monkeypatch):
    class _PartialApi(_FakeApi):
        def get(self, path, *a, **k):
            if path.endswith("/CSW"):
                raise RuntimeError("boom")
            return super().get(path, *a, **k)

    _patch(
        monkeypatch,
        _PartialApi(
            csd=[{"activity_type": "CSD", "date": "2026-08-07", "net_amount": "115.66"}]
        ),
    )
    assert api_routes._get_live_cashflows() == {"2026-08-07": 115.66}


def test_inception_equity_backfill_has_no_cashflow(monkeypatch):
    # #2717: backfill points no longer carry cash-flow (portfolio-history.cashflow is empty). Real
    # CSD/CSW is attached later from account-activities. The request must NOT set cashflow_types.
    api = _patch(
        monkeypatch,
        _FakeApi(
            hist=SimpleNamespace(
                timestamp=[_TS_D1, _TS_D2], equity=[10_000.0, 15_000.0]
            )
        ),
    )

    result = api_routes._get_inception_equity()

    assert result is not None
    inception_date, inception_equity, backfill = result
    assert inception_date == "2026-01-02"
    assert inception_equity == pytest.approx(10_000.0)
    assert backfill[0] == {"date": "2026-01-02", "equity": 10_000.0, "cashflow": 0.0}
    assert backfill[1] == {"date": "2026-01-03", "equity": 15_000.0, "cashflow": 0.0}
    assert getattr(api.last_request, "cashflow_types", None) is None


def test_inception_request_uses_period_all_not_start(monkeypatch):
    # #2878: request the FULL history via period="all" (measured: returns the account from its
    # first funded day). start=<date> is NOT "from-date to now" — Alpaca returns a ~1-month window
    # from that date, so a pre-inception start yields an all-zero month and NO inception.
    api = _patch(
        monkeypatch,
        _FakeApi(hist=SimpleNamespace(timestamp=[_TS_D1], equity=[10_000.0])),
    )

    api_routes._get_inception_equity()

    req = api.last_request
    assert req.period == "all", "primary request must use period='all'"
    assert (
        getattr(req, "start", None) is None
    ), "start= must NOT be set (it is a 1-month window)"


def test_inception_falls_back_to_1A_when_all_empty(monkeypatch):
    # #2878: period="all" occasionally returns an EMPTY array (transient Alpaca quirk — the
    # "worked until yesterday" incident). Retry with a 1-year window before failing soft, so a
    # blip doesn't truncate the chart to the first local snapshot.
    class _EmptyAllApi(_FakeApi):
        def get_portfolio_history(self, request):
            self.last_request = request
            if getattr(request, "period", None) == "all":
                return SimpleNamespace(timestamp=[], equity=[])  # transient empty
            return SimpleNamespace(
                timestamp=[_TS_D1, _TS_D2], equity=[10_000.0, 15_000.0]
            )

    api = _patch(monkeypatch, _EmptyAllApi())

    result = api_routes._get_inception_equity()

    assert result is not None, "must recover via the 1A retry, not fail soft"
    inception_date, inception_equity, _backfill = result
    assert inception_date == "2026-01-02"
    assert inception_equity == pytest.approx(10_000.0)
    assert api.last_request.period == "1A", "fallback must use period='1A'"
