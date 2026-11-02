# tests/unit/test_sec_earnings_dates.py
"""#3349 — SEC ``submissions`` -> earnings report dates (pure parse + fail modes).

Pins that an 8-K Item 2.02 counts as the earnings release, a non-2.02 8-K does
not, 10-Q is the fallback when no 2.02 exists, and the fetch-failure path returns
the fail-CLOSED marker while a clean empty panel fails open.
"""

import json

from core.report.sec_fundamentals import (
    earnings_report_dates,
    parse_earnings_dates,
    refresh_earnings_cache,
)


def _subs(rows):
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "filingDate": [r[1] for r in rows],
                "items": [r[2] for r in rows],
            }
        }
    }


def test_prefers_8k_item_202():
    subs = _subs(
        [
            ("8-K", "2026-07-28", "2.02,9.01"),  # earnings release
            ("8-K", "2026-08-17", "5.02,7.01"),  # NOT earnings (exec change)
            ("10-Q", "2026-07-28", ""),  # periodic — ignored when 2.02 present
        ]
    )
    assert parse_earnings_dates(subs) == ["2026-07-28"]


def test_falls_back_to_periodic_when_no_202():
    subs = _subs(
        [
            ("8-K", "2026-08-17", "5.02"),
            ("10-Q", "2026-05-01", ""),
            ("10-K", "2026-02-01", ""),
        ]
    )
    assert parse_earnings_dates(subs) == ["2026-05-01", "2026-02-01"]  # sorted desc


def test_empty_panel_returns_empty():
    assert parse_earnings_dates(None) == []
    assert parse_earnings_dates({"filings": {"recent": {}}}) == []


def test_report_dates_fetch_failure_fails_closed():
    dates, failed = earnings_report_dates(320193, fetch=lambda cik: None)
    assert dates is None and failed is True


def test_report_dates_success():
    subs = _subs([("8-K", "2026-07-28", "2.02")])
    dates, failed = earnings_report_dates(1071739, fetch=lambda cik: subs)
    assert failed is False and dates == ["2026-07-28"]


def test_refresh_cache_stamps_error_and_dates(tmp_path):
    subs = _subs([("8-K", "2026-07-28", "2.02")])

    def fake_fetch(cik):
        return None if str(cik) == "999" else subs  # 999 = fetch failure

    out = tmp_path / "earnings_dates.json"
    blob = refresh_earnings_cache(
        {"CNC": "1071739", "DOWN": "999"}, str(out), fetch=fake_fetch
    )
    assert blob["CNC"] == {"dates": ["2026-07-28"], "error": False}
    assert blob["DOWN"] == {"error": True}
    assert json.loads(out.read_text(encoding="utf-8")) == blob
