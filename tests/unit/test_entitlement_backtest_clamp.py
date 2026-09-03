# tests/unit/test_entitlement_backtest_clamp.py
# GTM-1 (#1800) — Brick-5: clamp the backtest start_date to the tier's licensed look-back
# window, but ONLY on the LOCAL desktop. Cloud/Dev/CI (unlimited) are unchanged.
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.engine.simulation_runner import _clamp_backtest_start
from core.entitlement.tier import Entitlement, Tier


def _ent(months):
    return Entitlement(
        tier=Tier.BASIC,
        agent_names=("DrawdownGuardAgent",),
        allow_live=False,
        backtest_months=months,
        xai_enabled=False,
        max_order_value=1000.0,
    )


@pytest.fixture()
def local(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")


def test_non_local_never_clamped(monkeypatch):
    """Cloud/Dev/CI: start_date passes through untouched regardless of tier."""
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    old = "2000-01-01"
    assert _clamp_backtest_start(old) == old


def test_local_unlimited_tier_not_clamped(local):
    """A tier with backtest_months=None (unlimited) does not clamp."""
    with patch(
        "core.engine.simulation_runner.resolve_entitlement", return_value=_ent(None)
    ):
        assert _clamp_backtest_start("2000-01-01") == "2000-01-01"


def test_local_clamps_too_early_start(local):
    """A start_date earlier than now - backtest_months is pulled forward to the boundary."""
    with patch(
        "core.engine.simulation_runner.resolve_entitlement", return_value=_ent(12)
    ):
        clamped = _clamp_backtest_start("2000-01-01")
        boundary = (datetime.now(timezone.utc) - timedelta(days=12 * 30)).date()
        # Clamped to no earlier than the boundary (allow a small drift for the 30-day month).
        assert date.fromisoformat(clamped) >= boundary - timedelta(days=2)
        assert date.fromisoformat(clamped) > date(2000, 1, 1)


def test_local_recent_start_not_moved(local):
    """A start_date already inside the window is left as-is."""
    recent = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    with patch(
        "core.engine.simulation_runner.resolve_entitlement", return_value=_ent(12)
    ):
        assert _clamp_backtest_start(recent) == recent


def test_unparseable_start_returned_unchanged(local):
    """A start_date we cannot parse is returned unchanged (never crash a backtest)."""
    with patch(
        "core.engine.simulation_runner.resolve_entitlement", return_value=_ent(12)
    ):
        assert _clamp_backtest_start("not-a-date") == "not-a-date"
