# tests/unit/test_report_cache_persist.py
# REPORT_CACHE_PERSIST (2026-07-23): restart shows the SAME cards.
#
# Szenario: Persist + Reload (rund)
#   Angenommen ein frischer Report wird persistiert
#   Wenn eine neue Registry mit Flag ON bootet
#   Dann liegt derselbe Report im Speicher (Prosa identisch)
# Szenario: Stale wird NICHT wiederbelebt
#   Angenommen der persistierte Report ist aelter als die High-Prio-Frist
#   Dann startet die Registry ohne ihn (regeneriert planmaessig)
# Szenario: Flag OFF ist byte-identisch
#   Dann wird weder geschrieben noch geladen
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import allure

import core.specialist_registry as reg_mod
from core.specialist_registry import StockSpecialistRegistry
from core.stock_specialist import SpecialistReport


def _mk_report(sym="AAPL", age_seconds=0):
    return SpecialistReport(
        symbol=sym,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        bull_case="The upgrade cycle strengthens the bull case.",
        sentiment_score=64.0,
    )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Report Cache Persistence")
class TestReportCachePersist:
    def test_persist_and_reload_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        with patch.object(reg_mod, "_persist_enabled", return_value=True):
            r1 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            r1._persist_report(_mk_report())
            assert (tmp_path / "report_cache" / "AAPL.json").exists()
            r2 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            got = r2.get_report("AAPL")
            assert got is not None
            assert got.bull_case == "The upgrade cycle strengthens the bull case."
            assert got.sentiment_score == 64.0

    def test_stale_persisted_report_not_revived(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        with patch.object(reg_mod, "_persist_enabled", return_value=True):
            r1 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            stale_age = r1._high_prio_max_age + 3600
            r1._persist_report(_mk_report(age_seconds=stale_age))
            r2 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            assert r2.get_report("AAPL") is None

    def test_flag_off_writes_and_loads_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        with patch.object(reg_mod, "_persist_enabled", return_value=False):
            r1 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            r1._persist_report(_mk_report())
            assert not (tmp_path / "report_cache").exists()
            # seed a file manually — flag OFF must not load it either
            d = tmp_path / "report_cache"
            d.mkdir(parents=True)
            (d / "AAPL.json").write_text(
                json.dumps(
                    {
                        "symbol": "AAPL",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                encoding="utf-8",
            )
            r2 = StockSpecialistRegistry(["AAPL"], gemini_api_key="x")
            assert r2.get_report("AAPL") is None
