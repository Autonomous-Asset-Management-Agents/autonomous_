# tests/unit/test_gatekeeper_pacing.py
# Intraday buy-pacing (docs/intraday-buy-pacing/implementation_plan.md) — clean re-home of #2546.
#
# BUY-only pacing on the Iron-Dome gate: OpeningNoiseGuard + BuyPacingGuard (cooldown +
# max/hour). SELL / risk-exits always bypass. The gatekeeper reads pre-computed context
# keys (config is read in build_portfolio_context, not here). Fail-open: absent field → no veto.
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from core.round_table.gatekeeper import ComplianceGatekeeper

_ROOT = Path(__file__).resolve().parents[2]
_BUY = 0.90  # > BUY_THRESHOLD (0.65)
_SELL = 0.10  # < SELL_THRESHOLD (0.35)


def _ctx(**over):
    """A base portfolio_context that passes every OTHER gate; override the pacing keys."""
    base = {
        "position_locked": False,
        "day_trades_last_5d": 0,
        "symbol_weights": {},
        "sector_weights": {},
        "symbol_sector_map": {},
        "current_daily_trades": 0,
        "max_daily_trades": 50,
        "symbol_churn_guard_active": False,
        # pacing (defaults = inert)
        "intraday_timing_active": True,
        "no_buy_opening_minutes": 30,
        "global_buy_cooldown_minutes": 30,
        "max_buys_per_hour": 2,
        "minutes_since_market_open": None,
        "last_buy_elapsed_minutes": None,
        "buys_last_hour": None,
    }
    base.update(over)
    return base


class TestPacingGuard:
    @pytest.mark.anyio
    async def test_opening_noise_blocks_buy(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(minutes_since_market_open=10))
        assert d.approved is False and "OpeningNoise" in d.reason

    @pytest.mark.anyio
    async def test_opening_noise_passes_after_window(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(minutes_since_market_open=45))
        assert d.approved is True

    @pytest.mark.anyio
    async def test_cooldown_blocks_buy(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(last_buy_elapsed_minutes=5))
        assert d.approved is False and "Pacing" in d.reason

    @pytest.mark.anyio
    async def test_cooldown_passes_when_elapsed(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(last_buy_elapsed_minutes=31))
        assert d.approved is True

    @pytest.mark.anyio
    async def test_max_buys_per_hour_blocks(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(buys_last_hour=2))
        assert d.approved is False and "hour" in d.reason.lower()

    @pytest.mark.anyio
    async def test_under_quota_passes(self):
        gk = ComplianceGatekeeper()
        d = await gk.check("AAPL", _BUY, _ctx(buys_last_hour=1))
        assert d.approved is True

    @pytest.mark.anyio
    async def test_sell_bypasses_every_pacing_guard(self):
        gk = ComplianceGatekeeper()
        d = await gk.check(
            "AAPL",
            _SELL,
            _ctx(
                minutes_since_market_open=1,
                last_buy_elapsed_minutes=1,
                buys_last_hour=9,
            ),
        )
        assert d.approved is True  # a risk-reducing SELL is never paced

    @pytest.mark.anyio
    async def test_fail_open_when_context_absent(self):
        gk = ComplianceGatekeeper()
        # active but all three measured fields None → no veto (byte-identical to today)
        d = await gk.check("AAPL", _BUY, _ctx())
        assert d.approved is True

    @pytest.mark.anyio
    async def test_flag_off_never_paces(self):
        gk = ComplianceGatekeeper()
        d = await gk.check(
            "AAPL",
            _BUY,
            _ctx(
                intraday_timing_active=False,
                minutes_since_market_open=1,
                buys_last_hour=9,
            ),
        )
        assert d.approved is True


class TestMinutesSinceOpenHelper:
    """Archon WARNING-2: defensive clock handling — closed / missing clock → None; when
    open, minutes since today's 09:30 America/New_York regular session open."""

    def test_open_returns_elapsed(self):
        from datetime import datetime

        import pytz

        from core.engine.portfolio_context import _minutes_since_market_open

        ny = pytz.timezone("America/New_York")
        now = ny.localize(
            datetime(2026, 7, 28, 9, 45, 0)
        ).timestamp()  # 15 m after open
        clock = type("C", (), {"is_open": True})()
        assert abs(_minutes_since_market_open(clock, now=now) - 15.0) < 0.5

    def test_closed_market_returns_none(self):
        from core.engine.portfolio_context import _minutes_since_market_open

        clock = type("C", (), {"is_open": False})()
        assert _minutes_since_market_open(clock, now=1.0) is None

    def test_none_clock_returns_none(self):
        from core.engine.portfolio_context import _minutes_since_market_open

        assert _minutes_since_market_open(None, now=1.0) is None


class TestConfigParity:
    def _load_oss(self):
        spec = importlib.util.spec_from_file_location(
            "config_oss_pacing", str(_ROOT / "config.oss.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    @pytest.mark.parametrize(
        "name,default",
        [
            ("INTRADAY_TIMING_ENABLED", True),
            ("NO_BUY_OPENING_MINUTES", 30),
            ("GLOBAL_BUY_COOLDOWN_MINUTES", 30),
            ("MAX_BUYS_PER_HOUR", 2),
        ],
    )
    def test_flag_parity_and_default(self, name, default):
        import os

        import config as ent

        oss = self._load_oss()
        ent_val = getattr(ent.get_config(), name)
        oss_val = getattr(oss, name)
        assert ent_val == oss_val, f"{name}: ent={ent_val!r} oss={oss_val!r}"
        # default only asserted when the env override is absent
        if os.getenv(name) is None:
            assert ent_val == default
