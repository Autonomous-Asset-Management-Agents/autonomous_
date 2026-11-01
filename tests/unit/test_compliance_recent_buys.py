# tests/unit/test_compliance_recent_buys.py
# Intraday buy-pacing (docs/intraday-buy-pacing/implementation_plan.md) — buy-history source.
#
# Archon CRITICAL-1: the pacing metrics must NOT read the Gate-3 wash-trade buffer
# `_recent_trades` (purged to `_wash_trade_window_seconds` = 60 s), or the 30 m cooldown
# silently expires after 60 s. A dedicated, session-scoped `_recent_buys` buffer records
# BUY timestamps for a 2 h window and is cleared at the NY daily reset.
from __future__ import annotations

import core.compliance as compliance_mod
from core.compliance import ComplianceGuardian


class TestRecentBuysBuffer:
    def test_buy_survives_the_60s_wash_trade_purge(self, monkeypatch):
        """A BUY 61 s old is gone from _recent_trades (60 s wash window) but STILL in the
        dedicated buy buffer → buys_last_hour()==1 (the CRITICAL-1 failure mode fixed).
        """
        g = ComplianceGuardian()
        t0 = 1000.0
        g._record_recent_trade({"symbol": "AAPL", "side": "buy", "timestamp": t0})

        # 61 s later a SELL arrives → wash cleanup purges the AAPL record from _recent_trades
        monkeypatch.setattr(compliance_mod.time, "time", lambda: t0 + 61)
        g._record_recent_trade({"symbol": "MSFT", "side": "sell", "timestamp": t0 + 61})

        assert all(r["symbol"] != "AAPL" for r in g._recent_trades)  # wash-purged
        assert g.buys_last_hour(now=t0 + 61) == 1  # dedicated buffer NOT purged at 60 s
        assert abs(g.minutes_since_last_buy(now=t0 + 61) - (61 / 60)) < 0.02

    def test_sell_is_never_recorded_as_a_buy(self):
        g = ComplianceGuardian()
        g._record_recent_trade({"symbol": "AAPL", "side": "sell", "timestamp": 1000.0})
        assert g.buys_last_hour(now=1000.0) == 0
        assert g.minutes_since_last_buy(now=1000.0) is None

    def test_buys_last_hour_windows_at_3600s(self):
        g = ComplianceGuardian()
        g._record_recent_trade({"symbol": "A", "side": "buy", "timestamp": 0.0})
        g._record_recent_trade({"symbol": "B", "side": "buy", "timestamp": 3000.0})
        # at t=3601: the t=0 buy is now 3601 s old (outside the hour); the t=3000 buy is in.
        assert g.buys_last_hour(now=3601.0) == 1

    def test_minutes_since_last_buy_none_when_empty(self):
        g = ComplianceGuardian()
        assert g.minutes_since_last_buy(now=1000.0) is None

    def test_reset_daily_limit_clears_the_buy_buffer(self):
        g = ComplianceGuardian()
        g._record_recent_trade({"symbol": "A", "side": "buy", "timestamp": 1000.0})
        assert g.buys_last_hour(now=1000.0) == 1
        g.reset_daily_limit()
        assert g.buys_last_hour(now=1000.0) == 0
        assert g.minutes_since_last_buy(now=1000.0) is None
