"""Contract: GET /health.paper_trading is the ONLY truth the desktop account switcher reconciles on.

The console reads `/health.paper_trading` as the single source of truth for the Paper⇄Live toggle
(LiveTradingSwitchCard.tsx:58-61 `reconcilePaper`, comment at :29 "never an optimistic guess"). The
engine returns it at api_routes.py:339 as ``getattr(config, "PAPER_TRADING", True)``.

Audit finding #04 (coverage-gap): NO test asserted this contract. test_health.py checks
status/version/strategy_running but never that `paper_trading` is present or tracks
config.PAPER_TRADING. So a refactor that drops the key, hardcodes it, or lets the ``, True`` default
mask an unresolved config would make /health report Paper on a real-money engine (or Live on paper)
with the whole suite green — the exact class of mislabel INC-2 (#2213) was created to fix.

These tests LOCK the ready-branch contract (green). Audit #08 (LSR R4, #2251 — now CLOSED): the
"starting" body used to omit paper_trading, so reconcile read ``undefined`` and defaulted to Paper
during the post-restart boot window. The startup-branch tests below now assert as a HARD contract
that the "starting" body carries paper_trading and tracks config.PAPER_TRADING in both True and False.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod


@pytest.fixture
def client():
    return TestClient(api_routes_mod.app)


@contextlib.contextmanager
def _config_paper(value: bool):
    """Force ``config.PAPER_TRADING`` for the /health read without leaking into the suite.

    config.py exposes PAPER_TRADING via module ``__getattr__`` → _config_state (no real module
    attribute). Setting a real attribute shadows the delegation, so on exit we pop it (when it did
    not exist before) to restore the dynamic behaviour rather than freezing a stale value.
    """
    import config

    had = "PAPER_TRADING" in config.__dict__
    old = config.__dict__.get("PAPER_TRADING")
    config.PAPER_TRADING = value
    try:
        yield
    finally:
        if had:
            config.PAPER_TRADING = old
        else:
            config.__dict__.pop("PAPER_TRADING", None)


@patch(
    "core.engine.api_routes.RedisClient.check_health",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("core.engine.api_routes.engine")
def test_health_ready_reports_paper_true_from_config(mock_engine, _redis, client):
    mock_engine.strategy_running.is_set.return_value = True
    with _config_paper(True):
        data = client.get("/health").json()
    assert data["status"] == "healthy"
    # The switcher's single source of truth must be PRESENT (its `?? true` fallback otherwise
    # silently invents "Paper").
    assert "paper_trading" in data
    assert data["paper_trading"] is True


@patch(
    "core.engine.api_routes.RedisClient.check_health",
    new_callable=AsyncMock,
    return_value=True,
)
@patch("core.engine.api_routes.engine")
def test_health_ready_reports_paper_false_when_live(mock_engine, _redis, client):
    mock_engine.strategy_running.is_set.return_value = True
    with _config_paper(False):
        data = client.get("/health").json()
    # A live engine MUST report paper_trading=False — not the getattr(..., True) default. If this
    # ever regresses, the console shows "Paper — no real capital at risk" on a real-money engine.
    assert data["paper_trading"] is False


@patch("core.engine.api_routes.engine", new=None)
def test_health_starting_branch_carries_paper_trading_true(client):
    """Audit #08 (LSR R4, #2251 — FIXED): the /health 'starting' branch (api_routes.py:309-317) —
    engine still None during the post-restart boot window — MUST carry paper_trading. The console's
    reconcile reads `h.paper_trading ?? true`; a field-less starting body silently defaults to Paper,
    mislabelling a booting engine (audit #05/#16). config.PAPER_TRADING is fixed at spawn time, so the
    starting body can and must report it. Tracks config.PAPER_TRADING=True."""
    with _config_paper(True):
        data = client.get("/health").json()
    assert data["status"] == "starting"
    assert "paper_trading" in data
    assert data["paper_trading"] is True


@patch("core.engine.api_routes.engine", new=None)
def test_health_starting_branch_carries_paper_trading_false_when_live(client):
    """A live-armed engine that is still booting (engine None) MUST report paper_trading=False in the
    'starting' body — NOT the getattr(..., True) default. Otherwise the post-restart boot window
    mislabels a live arm as "Paper — no capital at risk" until the 10s background poll (audit #05/#16),
    the exact timing hazard LSR R4 closes at the source."""
    with _config_paper(False):
        data = client.get("/health").json()
    assert data["status"] == "starting"
    assert data["paper_trading"] is False
