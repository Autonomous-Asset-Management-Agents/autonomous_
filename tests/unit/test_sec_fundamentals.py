"""#3145 — SEC EDGAR -> financials_cache adapter (pure normalizer).

The adapter turns a SEC ``companyfacts`` payload into the same Polygon-vX ``results``
rows the existing ``financials_feed.derive_fundamentals`` already consumes, so the
FundamentalsAgent/ValuationAgent read real numbers with NO agent/reader change. Free,
no API key, public-domain source. These tests pin the row shape + the tag-synonym /
20-F / short-history / restatement handling against the real derivation.
"""

from datetime import date

import pytest

from core.report import sec_fundamentals as sec
from core.report.financials_feed import derive_fundamentals


def _pt(val, fy, filed, end, form="10-K", fp="FY"):
    return {"val": val, "fy": fy, "fp": fp, "form": form, "filed": filed, "end": end}


def _facts(concepts):
    """concepts: {tag: (unit, [points])} -> companyfacts skeleton."""
    return {
        "cik": 789019,
        "entityName": "TESTCO",
        "facts": {
            "us-gaap": {
                tag: {"units": {unit: pts}} for tag, (unit, pts) in concepts.items()
            }
        },
    }


def _two_year_msft():
    return _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [
                    _pt(245_000, 2025, "2025-07-30", "2025-06-30"),
                    _pt(289_000, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "NetIncomeLoss": (
                "USD",
                [
                    _pt(88_000, 2025, "2025-07-30", "2025-06-30"),
                    _pt(116_000, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "EarningsPerShareDiluted": (
                "USD/shares",
                [
                    _pt(13.5, 2025, "2025-07-30", "2025-06-30"),
                    _pt(17.95, 2026, "2026-07-29", "2026-06-30"),
                ],
            ),
            "Liabilities": ("USD", [_pt(315_000, 2026, "2026-07-29", "2026-06-30")]),
            "StockholdersEquity": (
                "USD",
                [_pt(442_000, 2026, "2026-07-29", "2026-06-30")],
            ),
        }
    )


def test_rows_feed_derive_fundamentals_end_to_end():
    rows = sec.rows_from_companyfacts(_two_year_msft())
    d = derive_fundamentals(rows, as_of=date(2026, 9, 1), close=502.50)
    assert d is not None
    assert d["revenue_yoy"] == pytest.approx(289_000 / 245_000 - 1, rel=1e-6)
    assert d["net_margin"] == pytest.approx(116_000 / 289_000, rel=1e-6)
    assert d["eps_yoy"] == pytest.approx(17.95 / 13.5 - 1, rel=1e-6)
    assert d["pe"] == pytest.approx(502.50 / 17.95, rel=1e-6)
    assert d["total_liabilities"] == 315_000
    assert d["total_equity"] == 442_000
    assert d["filing_date"] == "2026-07-29"


def test_revenue_tag_synonym_is_resolved():
    """Revenue only under the plain ``Revenues`` tag (not the contract tag) is still read."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    _pt(100, 2025, "2025-02-01", "2024-12-31"),
                    _pt(130, 2026, "2026-02-01", "2025-12-31"),
                ],
            ),
            "NetIncomeLoss": ("USD", [_pt(26, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["revenue_yoy"] == pytest.approx(0.30, rel=1e-6)
    assert d["net_margin"] == pytest.approx(0.20, rel=1e-6)


def test_foreign_filer_20f_is_covered():
    f = _facts(
        {
            "RevenueFromContractWithCustomerExcludingAssessedTax": (
                "USD",
                [
                    _pt(10, 2025, "2025-03-01", "2024-12-31", form="20-F"),
                    _pt(12, 2026, "2026-03-01", "2025-12-31", form="20-F"),
                ],
            ),
            "NetIncomeLoss": (
                "USD",
                [_pt(3, 2026, "2026-03-01", "2025-12-31", form="20-F")],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    assert len(rows) == 2
    d = derive_fundamentals(rows, as_of=date(2026, 9, 1), close=None)
    assert d["revenue_yoy"] == pytest.approx(0.20, rel=1e-6)


def test_equity_synonym_including_noncontrolling():
    f = _facts(
        {
            "Revenues": ("USD", [_pt(100, 2026, "2026-02-01", "2025-12-31")]),
            "Liabilities": ("USD", [_pt(80, 2026, "2026-02-01", "2025-12-31")]),
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": (
                "USD",
                [_pt(40, 2026, "2026-02-01", "2025-12-31")],
            ),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["total_liabilities"] == 80
    assert d["total_equity"] == 40


def test_same_period_uses_as_reported_earliest_filed():
    """Two filings for the SAME period end -> the earliest-filed (as-originally-
    reported) value wins, and the row's filing_date is that original date. This is
    point-in-time correct: a later comparative / 10-K/A restatement must not rewrite
    what was knowable at the original filing time."""
    f = _facts(
        {
            "Revenues": (
                "USD",
                [
                    _pt(100, 2026, "2026-02-01", "2025-12-31"),
                    _pt(
                        105, 2026, "2026-08-01", "2025-12-31", form="10-K/A"
                    ),  # later restatement
                ],
            ),
        }
    )
    rows = sec.rows_from_companyfacts(f)
    assert len(rows) == 1
    assert rows[0]["financials"]["income_statement"]["revenues"]["value"] == 100
    assert rows[0]["filing_date"] == "2026-02-01"


def test_short_history_one_year_still_yields_margin():
    f = _facts(
        {
            "Revenues": ("USD", [_pt(100, 2026, "2026-02-01", "2025-12-31")]),
            "NetIncomeLoss": ("USD", [_pt(25, 2026, "2026-02-01", "2025-12-31")]),
        }
    )
    d = derive_fundamentals(
        sec.rows_from_companyfacts(f), as_of=date(2026, 9, 1), close=None
    )
    assert d["revenue_yoy"] is None  # no prior year
    assert d["net_margin"] == pytest.approx(
        0.25, rel=1e-6
    )  # FundamentalsAgent can still vote


def test_no_gaap_facts_yields_no_rows():
    assert sec.rows_from_companyfacts({"facts": {}}) == []
    assert (
        derive_fundamentals(
            sec.rows_from_companyfacts({"facts": {}}), as_of=date(2026, 9, 1)
        )
        is None
    )


def test_build_cache_payload_shape():
    payload = sec.build_cache_payload(_two_year_msft())
    assert set(payload.keys()) == {"results"}
    assert isinstance(payload["results"], list) and len(payload["results"]) == 2
