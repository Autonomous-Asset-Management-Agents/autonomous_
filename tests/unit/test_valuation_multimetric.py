"""#3146 — ValuationAgent PEG -> P/S / P/B fallback (dark, flag-gated)."""

import asyncio
from datetime import date

import pytest

from core.report.financials_feed import derive_fundamentals
from core.round_table import agents as ag


def _run(fund, flag_on, monkeypatch):
    monkeypatch.setattr(
        ag, "_read_pit_fundamentals", lambda sym, close=None, as_of=None: fund
    )
    monkeypatch.setattr(ag, "_valuation_multimetric_enabled", lambda: flag_on)
    state = {"symbol": "X", "ohlc": {"close": 100.0}}
    return asyncio.run(ag.ValuationAgent().vote(state))


def test_pb_and_ps_in_derive_fundamentals():
    rows = [
        {
            "filing_date": "2026-01-01",
            "fiscal_period": "FY",
            "timeframe": "annual",
            "financials": {
                "income_statement": {
                    "revenues": {"value": 100},
                    "basic_average_shares": {"value": 10},
                },
                "balance_sheet": {"equity": {"value": 50}},
            },
        }
    ]
    d = derive_fundamentals(rows, date(2026, 9, 2), close=20.0)
    assert d["ps"] == pytest.approx(20 * 10 / 100)  # 2.0
    assert d["pb"] == pytest.approx(20 * 10 / 50)  # 4.0


def test_flag_on_falls_back_to_price_to_sales(monkeypatch):
    fund = {
        "pe": None,
        "eps_yoy": -0.05,
        "revenue": 1000,
        "ps": 0.8,
        "pb": 2.0,
        "filing_date": "2026-02-01",
    }
    r = _run(fund, True, monkeypatch)
    assert "price-to-sales" in r.reasoning
    assert r.score == pytest.approx(0.65)  # ps 0.8 < PS_CHEAP


def test_flag_on_falls_back_to_price_to_book_when_no_sales(monkeypatch):
    fund = {
        "pe": None,
        "eps_yoy": None,
        "revenue": None,
        "ps": None,
        "pb": 0.7,
        "filing_date": "2026-02-01",
    }
    r = _run(fund, True, monkeypatch)
    assert "price-to-book" in r.reasoning
    assert r.score == pytest.approx(0.65)  # pb 0.7 < PB_CHEAP


def test_flag_off_is_byte_identical_abstention(monkeypatch):
    fund = {
        "pe": None,
        "eps_yoy": -0.05,
        "revenue": 1000,
        "ps": 0.8,
        "pb": 2.0,
        "filing_date": "2026-02-01",
    }
    r = _run(fund, False, monkeypatch)
    assert r.weight == 0.0
    assert "EXCLUDED" in r.reasoning
    # no fallback verdict was produced (the EXCLUDED text still mentions price-to-earnings)
    assert "price-to-sales" not in r.reasoning
    assert "price-to-book" not in r.reasoning


def test_peg_path_unchanged_when_earnings_positive(monkeypatch):
    fund = {
        "pe": 20.0,
        "eps_yoy": 0.40,
        "revenue": 1000,
        "ps": 1.0,
        "pb": 2.0,
        "filing_date": "2026-02-01",
        "total_liabilities": 50,
        "total_equity": 100,
    }
    r = _run(fund, True, monkeypatch)  # flag on, but PEG is defined -> PEG wins
    assert "PEG" in r.reasoning
    assert r.score == pytest.approx(0.7)  # peg 0.5 < 1 and low debt


def test_no_metric_available_abstains_even_with_flag(monkeypatch):
    fund = {
        "pe": None,
        "eps_yoy": None,
        "revenue": None,
        "ps": None,
        "pb": None,
        "filing_date": "2026-02-01",
    }
    r = _run(fund, True, monkeypatch)
    assert r.weight == 0.0 and "EXCLUDED" in r.reasoning
