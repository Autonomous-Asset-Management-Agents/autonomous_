"""LSR R1 (#2252, under #2251 §R1 / #2213): ``POST /api/live/disable`` is an IMMEDIATE,
atomic, in-process halt — not just a WORM note that only bites on the next restart (audit #02).

Root cause pinned by these tests:
  * ``live_disable`` (api_routes.py) must TRIP the existing kill-switch (blocks new submits
    synchronously + mass-cancels in-flight orders) AND ``force_paper_trading`` AND keep the WORM
    ``disable`` write — in that order, synchronously, without a restart.
  * ``force_paper_trading`` ALONE does NOT redirect the boot-built live broker client, so the
    trip() is the load-bearing stop (T4 regression guard).
  * the FALLBACK submit-boundary gap (order_executor.py): a trip during the displacement
    ``asyncio.sleep(0.5)`` must still abort the live BUY via a SECOND ``check_halt`` right
    before the submit; a trip before the fallback displacement SELL must abort that too.

Async is driven via ``asyncio.run`` (no pytest-anyio needed); async broker interfaces are
``unittest.mock.AsyncMock`` (CODING_POLICY §5.2 — never ``async def`` stubs). Auth: X-Engine-Key.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_KEY = "test-engine-key-r1"
_REASON = "live trading disabled by operator"


# --------------------------------------------------------------------------------------
# order_executor drivers (reuse the harness style from test_displacement_recovery.py)
# --------------------------------------------------------------------------------------
def _make_decision_context(**kwargs):
    ctx = MagicMock()
    ctx.lstm_prediction = kwargs.get("lstm_prediction", 0.0)
    ctx.risk_approved = kwargs.get("risk_approved", True)
    ctx.portfolio_approved = kwargs.get("portfolio_approved", True)
    ctx.client_order_id = kwargs.get("client_order_id", "test-uuid")
    ctx.current_price = kwargs.get("current_price", 150.0)
    ctx.conviction_score = kwargs.get("conviction_score", 0.8)
    ctx.alpaca_order_id = None
    return ctx


def _make_signal(action="BUY", symbol="AAPL", qty=2.0):
    from core.events import SignalEvent

    ctx = _make_decision_context()
    event = MagicMock(spec=SignalEvent)
    event.action = action
    event.symbol = symbol
    event.suggested_quantity = qty
    event.decision_context = ctx
    event.is_simulation = False
    return event


def _make_executor(api=None):
    from core.engine.order_executor import OrderExecutorMixin

    executor = OrderExecutorMixin.__new__(OrderExecutorMixin)
    executor.api = api or MagicMock()
    executor.compliance_guardian = None
    executor._log_strategy_thought = MagicMock()
    executor.cloud_logger = MagicMock()
    executor.cloud_logger.log_decision = MagicMock()
    executor.live_universe = []
    executor.tenant_portfolio_managers = {}
    return executor


class _KillSwitchIsolation(unittest.TestCase):
    """Reset the kill-switch singleton and silence trip() side effects (Slack + the
    fire-and-forget mass-cancel thread) — neither is under test here."""

    def setUp(self):
        from core.kill_switch import kill_switch

        self.ks = kill_switch
        self._orig_redis = self.ks.redis_client
        self.ks.redis_client = None
        self.ks.reset()

        def _cleanup():
            self.ks.reset()
            self.ks.redis_client = self._orig_redis

        self.addCleanup(_cleanup)
        p1 = patch("core.kill_switch.send_slack_alert", lambda *a, **k: None)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = patch.object(
            type(self.ks), "_run_async_mass_cancel", lambda self, *a, **k: None
        )
        p2.start()
        self.addCleanup(p2.stop)


# ======================================================================================
# T1 / T5 — the /api/live/disable endpoint
# ======================================================================================
class LiveDisableEndpoint(_KillSwitchIsolation):
    def setUp(self):
        super().setUp()
        import config

        # force_paper_trading() mutates os.environ + rebuilds config._config_state — snapshot
        # both so the singleton is pristine for the next test.
        self._orig_cfg_state = config._config_state
        self.addCleanup(setattr, config, "_config_state", self._orig_cfg_state)

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        env = patch.dict(
            os.environ, {"ENGINE_API_KEY": _KEY, "SENATE_LOG_DIR": self.tmp}
        )
        env.start()
        self.addCleanup(env.stop)

        import core.hitl_gate as hg
        import core.round_table.runner as runner

        self._old_senate = runner._senate
        runner._senate = None
        self.addCleanup(setattr, runner, "_senate", self._old_senate)
        hg._fallback_audit_logger = None
        self.addCleanup(setattr, hg, "_fallback_audit_logger", None)

    def _client(self, raise_server_exceptions=True):
        from fastapi.testclient import TestClient

        import core.engine.api_routes as api_routes

        return TestClient(
            api_routes.app, raise_server_exceptions=raise_server_exceptions
        )

    def _live_events(self):
        out = []
        for f in sorted(Path(self.tmp).glob("audit_log_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    e = json.loads(line)
                    if e.get("event_type") == "live_enablement":
                        out.append(e)
        return out

    def test_disable_trips_halt_forces_paper_and_writes_worm(self):
        import config

        client = self._client()
        with patch(
            "config.force_paper_trading", wraps=config.force_paper_trading
        ) as fp:
            r = client.post(
                "/api/live/disable",
                headers={"X-Engine-Key": _KEY},
                json={"acknowledgment": "stop live now", "nonce": "d-1"},
            )
        self.assertEqual(r.status_code, 201)
        # 1) immediate in-process halt (blocks every subsequent submit synchronously)
        self.assertTrue(
            self.ks.is_halted(),
            "disable MUST trip the kill-switch (immediate in-process halt)",
        )
        # 2) future clients select paper
        fp.assert_called_once()
        self.assertTrue(config.PAPER_TRADING)
        # 3) the existing WORM disable record is still written
        self.assertEqual([e["action"] for e in self._live_events()], ["disable"])

    def test_disable_requires_engine_key_and_does_not_trip(self):
        client = self._client()
        r = client.post("/api/live/disable", json={"acknowledgment": "x", "nonce": "n"})
        self.assertIn(r.status_code, (401, 403))
        self.assertFalse(
            self.ks.is_halted(), "an unauthenticated call must NOT trip the halt"
        )


# ======================================================================================
# T2 / T3 / T3b — the submit-boundary guards in order_executor
# ======================================================================================
class SubmitBoundaryHalt(_KillSwitchIsolation):
    def setUp(self):
        super().setUp()
        import config

        self._orig_shadow_mode = getattr(config, "SHADOW_MODE", False)
        config.SHADOW_MODE = False
        self.addCleanup(setattr, config, "SHADOW_MODE", self._orig_shadow_mode)

    def test_tenant_path_trip_blocks_submit_zero_broker_submits(self):
        # T2 (regression): tenant BUY reaches the guard at order_executor.py:947 immediately
        # before the submit — a tripped switch there means client.submit_order is NEVER awaited.
        api = MagicMock()
        api.get_account.return_value = MagicMock(
            multiplier="1", buying_power=100000.0, cash=100000.0
        )
        executor = _make_executor(api=api)

        pm = MagicMock()
        pm.should_open_new_position.return_value = (True, "ok", None)  # no displacement
        rm = MagicMock()
        rm.calculate_position_size.return_value = 2.0

        self.ks.trip(_REASON)  # operator disabled live mid-cycle

        with (
            patch.object(executor, "_get_tenant_portfolio_manager", return_value=pm),
            patch.object(executor, "_get_tenant_risk_manager", return_value=rm),
            patch("core.engine.order_executor.RedisClient") as rc,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis",
                new=AsyncMock(),
            ),
            patch(
                "core.engine.order_executor.persist_pm_state_to_redis",
                new=AsyncMock(),
            ),
        ):
            rc.get_redis = AsyncMock(return_value=None)
            event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
            tenant = {"user_id": "tenant-1", "client": api, "equity": 10000.0}
            asyncio.run(executor._execute_tenant_order(tenant, event))

        api.submit_order.assert_not_called()

    def test_fallback_buy_gap_trip_during_sleep_blocks_live_buy(self):
        # T3 (the gap): fallback displacement SELL fires, then the operator disables live
        # DURING asyncio.sleep(0.5) — the NEW check_halt right before the BUY submit (:1982)
        # aborts it. SELL submitted once; BUY never.
        api = MagicMock()
        api.get_account.return_value = MagicMock(multiplier="2", buying_power=1000.0)
        api.get_open_position.return_value = MagicMock(
            symbol="MSFT", qty="5", current_price="100.0"
        )
        api.submit_order.return_value = MagicMock(id="sell-1")  # the displacement SELL

        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.should_open_new_position.return_value = (True, "swap", "MSFT")
        strat = MagicMock()
        strat.portfolio_manager = pm
        executor.active_strategy = strat

        def _trip_during_sleep(*a, **k):
            if threading.current_thread() is threading.main_thread():
                self.ks.trip(_REASON)

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.RedisClient") as rc,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis",
                new=AsyncMock(),
            ),
            patch(
                "core.engine.order_executor.persist_pm_state_to_redis",
                new=AsyncMock(),
            ),
            patch(
                "core.engine.order_executor.asyncio.sleep",
                new=AsyncMock(side_effect=_trip_during_sleep),
            ),
            patch(
                "core.engine.order_executor.config.DISPLACEMENT_BUY_FIRST",
                False,
                create=True,
            ),
        ):
            rc.get_redis = AsyncMock(return_value=None)
            event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
            asyncio.run(executor._process_signal_event(event))

        self.assertEqual(
            api.submit_order.call_count,
            1,
            "only the displacement SELL may submit — the live BUY must be halt-blocked",
        )

    def test_fallback_sell_guard_trip_before_sell_blocks_all_submits(self):
        # T3b: the operator disables live in the window BETWEEN the :1841 gate and the SELL
        # (here: during the displacement pre-check's get_account) — the NEW check_halt before
        # the fallback SELL (:1943) aborts it, and the BUY guard aborts the BUY. Zero submits.
        api = MagicMock()
        api.get_account.return_value = MagicMock(
            multiplier="2", buying_power=1000.0, cash=500.0
        )
        api.get_open_position.return_value = MagicMock(
            symbol="MSFT", qty="5", current_price="100.0"
        )
        api.submit_order.return_value = MagicMock(id="sell-1")

        # The displacement pre-check's get_account (order_executor.py:1853) is the first
        # get_account on this fallback path and sits AFTER the :1841 gate but BEFORE the SELL —
        # exactly the guarded window. Trip there so the NEW check_halt before the SELL bites.
        def _acct(*a, **k):
            self.ks.trip(_REASON)
            return MagicMock(multiplier="2", buying_power=1000.0, cash=500.0)

        api.get_account.side_effect = _acct

        executor = _make_executor(api=api)
        pm = MagicMock()
        pm.should_open_new_position.return_value = (True, "swap", "MSFT")
        strat = MagicMock()
        strat.portfolio_manager = pm
        executor.active_strategy = strat

        with (
            patch.object(
                executor, "get_active_tenant_clients", new=AsyncMock(return_value=[])
            ),
            patch("core.engine.order_executor.RedisClient") as rc,
            patch(
                "core.engine.order_executor.restore_pm_state_from_redis",
                new=AsyncMock(),
            ),
            patch(
                "core.engine.order_executor.persist_pm_state_to_redis",
                new=AsyncMock(),
            ),
        ):
            rc.get_redis = AsyncMock(return_value=None)
            event = _make_signal(action="BUY", symbol="AAPL", qty=2.0)
            asyncio.run(executor._process_signal_event(event))

        self.assertEqual(
            api.submit_order.call_count,
            0,
            "a trip before the fallback SELL must block the SELL and the BUY (0 submits)",
        )

    def test_market_closed_persists_offhours_decision_issue_2481(self):
        """#2481: When the market is closed, BUY/SELL decisions must still call log_decision
        and _capture_outcome before the early return, ensuring off-hours/weekend research
        decisions are persisted to the decisions and decision_outcomes tables.
        """
        api = MagicMock()
        api.get_clock.return_value = MagicMock(is_open=False)
        executor = _make_executor(api=api)

        event = _make_signal(action="BUY", symbol="AAPL", qty=10.0)

        with patch("core.engine.order_executor._capture_outcome") as mock_capture:
            asyncio.run(executor._process_signal_event(event))

        executor.cloud_logger.log_decision.assert_called_once_with(
            event.decision_context
        )
        mock_capture.assert_called_once_with(event.decision_context)


# ======================================================================================
# T4 — regression: force_paper_trading ALONE does not stop a boot-built live client
# ======================================================================================
class ForcePaperDoesNotRedirectBootClient(unittest.TestCase):
    def setUp(self):
        import config

        self._orig_cfg_state = config._config_state
        self.addCleanup(setattr, config, "_config_state", self._orig_cfg_state)
        env = patch.dict(os.environ, {})
        env.start()
        self.addCleanup(env.stop)

    def test_force_paper_does_not_redirect_already_built_live_client(self):
        from alpaca.trading.client import TradingClient

        import config

        # The engine builds its broker ONCE at boot with paper=is_paper (api_routes.py:145-153).
        live_client = TradingClient("k", "s", paper=False)
        before = live_client._base_url
        self.assertNotIn("paper", str(getattr(before, "value", before)).lower())

        config.force_paper_trading(reason="t4-regression")

        after = live_client._base_url
        # force_paper_trading rebuilds config only — the boot-built client stays LIVE-bound.
        self.assertEqual(before, after)
        self.assertNotIn(
            "paper",
            str(getattr(after, "value", after)).lower(),
            "force_paper_trading alone must NOT redirect the boot-live client — "
            "so kill_switch.trip() is the load-bearing stop",
        )


if __name__ == "__main__":
    unittest.main()
