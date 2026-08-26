# tests/unit/test_specialist_freshness.py
"""RQ-1 B2 (#1522): per-source "as of" freshness + stale badge in the report serializer.

So a stale filing is never silently shown as current: each source carries the newest filing
date it actually used, and data_stale flips when even the freshest source is older than the
freshness SLA. Flag-gated additive (SPECIALIST_FRESHNESS_ENABLED, default OFF) so the exact
key-set / bundle-parity contract holds byte-identical when off. (Epic #1516, Phase B.)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from core.engine.api_routes import _serialize_specialist_report
from core.specialist.report import SpecialistReport


class _FreshOn:
    SPECIALIST_FRESHNESS_ENABLED = True
    SPECIALIST_FRESHNESS_SLA_DAYS = 30


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _serialize(r, *, flag_on=True):
    cfg = _FreshOn() if flag_on else object()
    with patch("config.get_config", return_value=cfg):
        return _serialize_specialist_report("AAPL", r)


class TestFreshness:
    def test_as_of_is_newest_filing_per_source(self):
        recent, older = _days_ago(3), _days_ago(20)
        r = SpecialistReport(
            symbol="AAPL", insider_trades=[{"filed": older}, {"filed": recent}]
        )
        d = _serialize(r)
        assert d["insider_as_of"] == recent  # newest, not oldest
        assert d["data_stale"] is False  # within the 30d SLA

    def test_data_stale_when_freshest_source_old(self):
        old = _days_ago(200)
        r = SpecialistReport(symbol="AAPL", insider_trades=[{"filed": old}])
        d = _serialize(r)
        assert d["insider_as_of"] == old
        assert d["data_stale"] is True

    def test_no_filings_means_none_as_of_and_not_stale(self):
        r = SpecialistReport(
            symbol="AAPL", insider_trades=[], material_events=[], activist_stakes=[]
        )
        d = _serialize(r)
        assert d["insider_as_of"] is None
        assert d["data_stale"] is False

    def test_flag_off_omits_freshness_keys(self):
        """Default (flag OFF): no freshness keys -> byte-identical DTO / bundle parity."""
        r = SpecialistReport(symbol="AAPL", insider_trades=[{"filed": _days_ago(3)}])
        d = _serialize(r, flag_on=False)
        assert "insider_as_of" not in d
        assert "data_stale" not in d
