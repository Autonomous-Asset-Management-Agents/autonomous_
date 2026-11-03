"""#3668 — Compliance-Zeitfenster und Clock-Port laufen auf der Engine-Uhr.

Unter SIM_MODE ist die Engine-Uhr die Sim-Uhr; sonst die Wanduhr (byte-identisch). Vorher las
``ComplianceGuardian`` ``time.time()``: Sim-Tag 1 und Sim-Tag 2 lagen real Sekunden auseinander ⇒
jeder Loss-Cut-SELL am Folgetag war ein „Potential Wash Trade", und ``BuyPacingGuard`` rechnete
negative Minuten (Sim-Uhr minus Wanduhr).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.vc0]

import core.compliance as compliance_mod  # noqa: E402
from core.compliance import ComplianceGuardian  # noqa: E402
from core.sim import clock  # noqa: E402


class _Cfg:
    def __init__(self, sim):
        self.SIM_MODE = sim


def _sim(monkeypatch, sim_date="2026-06-05", window="09:30-10:30 ET", cadence="15m"):
    sc = clock.SimClock(sim_date=sim_date, sim_window=window, sim_cadence=cadence)
    monkeypatch.setattr(clock, "get_config", lambda: _Cfg(True))
    monkeypatch.setattr(clock, "get_sim_clock", lambda: sc)
    return sc


class TestEngineEpoch:
    def test_engine_epoch_folgt_sim_uhr(self, monkeypatch):
        sc = _sim(monkeypatch)
        expected = clock.engine_now(timezone.utc).timestamp()
        assert abs(clock.engine_epoch() - expected) < 1e-6
        assert abs(clock.engine_epoch() - time.time()) > 3600  # Sim-Tag, nicht heute
        assert sc is not None

    def test_engine_epoch_ist_wanduhr_ohne_sim_mode(self, monkeypatch):
        monkeypatch.setattr(clock, "get_config", lambda: _Cfg(False))
        assert abs(clock.engine_epoch() - time.time()) < 5


class TestWashTradeAufSimUhr:
    def test_sell_am_folgetag_ist_kein_wash_trade(self, monkeypatch):
        sc = _sim(monkeypatch)
        g = ComplianceGuardian()
        # BUY am Sim-Tag 1 10:00 ET
        sc.current_time = datetime(2026, 6, 5, 10, 0, tzinfo=sc.current_time.tzinfo)
        g._record_recent_trade(
            {
                "symbol": "TXN",
                "side": "buy",
                "timestamp": clock.engine_epoch(),
                "user_id": "global",
            }
        )
        # SELL am Sim-Tag 2 09:30 ET (real: Sekunden spaeter)
        sc.current_time = datetime(2026, 6, 8, 9, 30, tzinfo=sc.current_time.tzinfo)
        assert g._detect_wash_trade("TXN", "sell", user_id="global") is False

    def test_sell_binnen_60s_sim_bleibt_wash_trade(self, monkeypatch):
        sc = _sim(monkeypatch)
        g = ComplianceGuardian()
        sc.current_time = datetime(2026, 6, 5, 10, 0, tzinfo=sc.current_time.tzinfo)
        g._record_recent_trade(
            {
                "symbol": "TXN",
                "side": "buy",
                "timestamp": clock.engine_epoch(),
                "user_id": "global",
            }
        )
        sc.current_time = datetime(2026, 6, 5, 10, 0, 30, tzinfo=sc.current_time.tzinfo)
        assert g._detect_wash_trade("TXN", "sell", user_id="global") is True

    def test_cleanup_purgt_auf_sim_uhr(self, monkeypatch):
        sc = _sim(monkeypatch)
        g = ComplianceGuardian()
        sc.current_time = datetime(2026, 6, 5, 10, 0, tzinfo=sc.current_time.tzinfo)
        g._record_recent_trade(
            {
                "symbol": "TXN",
                "side": "buy",
                "timestamp": clock.engine_epoch(),
                "user_id": "global",
            }
        )
        sc.current_time = datetime(2026, 6, 8, 9, 30, tzinfo=sc.current_time.tzinfo)
        g._record_recent_trade(
            {
                "symbol": "MSI",
                "side": "buy",
                "timestamp": clock.engine_epoch(),
                "user_id": "global",
            }
        )
        assert [r["symbol"] for r in g._recent_trades] == ["MSI"]


class TestHftThrottleAufSimUhr:
    def test_hft_stempel_ist_engine_epoch(self, monkeypatch):
        """Der Stempel des Trade-Records im HFT-Throttle ist die Engine-Uhr, nicht die Wanduhr."""
        _sim(monkeypatch)
        src = open(compliance_mod.__file__, encoding="utf-8").read()
        assert (
            "now = time.time()"
            not in src.split("def _log_audit")[0].split("# 5. HFT Throttle")[1]
        ), "HFT-Throttle liest noch time.time()"


class TestPacingNieNegativ:
    def test_minutes_since_last_buy_nie_negativ_im_sim(self, monkeypatch):
        sc = _sim(monkeypatch)
        g = ComplianceGuardian()
        sc.current_time = datetime(2026, 6, 5, 10, 0, tzinfo=sc.current_time.tzinfo)
        g._record_recent_trade(
            {
                "symbol": "TXN",
                "side": "buy",
                "timestamp": clock.engine_epoch(),
                "user_id": "global",
            }
        )
        sc.current_time = datetime(2026, 6, 5, 10, 45, tzinfo=sc.current_time.tzinfo)
        now = clock.engine_now(timezone.utc).timestamp()
        assert abs(g.minutes_since_last_buy(now) - 45.0) < 0.01


class TestClockPortFolgtEngineUhr:
    def test_system_clock_liefert_sim_uhr_unter_sim_mode(self, monkeypatch):
        sc = _sim(monkeypatch)
        from core.adapters.system_clock import SystemClock

        n = SystemClock().now()
        assert n.tzinfo is not None
        assert n == sc.current_time.astimezone(timezone.utc)
        assert abs(SystemClock().time() - sc.current_time.timestamp()) < 1e-6

    def test_system_clock_ist_wanduhr_ohne_sim_mode(self, monkeypatch):
        monkeypatch.setattr(clock, "get_config", lambda: _Cfg(False))
        from core.adapters.system_clock import SystemClock

        assert (
            abs((SystemClock().now() - datetime.now(timezone.utc)).total_seconds()) < 5
        )
        assert abs(SystemClock().time() - time.time()) < 5
