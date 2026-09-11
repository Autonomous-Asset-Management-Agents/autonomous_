# tests/unit/test_compliance_exit_exemption.py
# B2 — after 10 trades the bot cannot cut a loss.
#
# REPRODUCED ON THE DEFAULT DESKTOP PATH (no flag gates it):
#   daily_trades after 10 trades : 10
#   check_order(stop-loss SELL)      -> True    every other gate passes it
#   _check_risk_limits(held exit)    -> True    the #2065 value-cap exemption fires
#   check_trade(stop-loss SELL)      -> False   REFUSED
#
# Proof chain: trading_loop.py `_run_position_stop_checks` (unconditional in the live
# loop) -> SignalEvent(action="SELL") -> order_executor resolves qty from the broker
# position -> check_order passes -> check_trade(order) with no `source` -> defaults "ai"
# -> compliance.py:442 `if source != "human_approved" and self.daily_trades >=
# self.max_daily_trades` -> return False -> approved=False -> no submit.
# Recovery only at the NY rollover via reset_daily_limit. There is NO broker-side stop:
# order_executor imports only Limit/MarketOrderRequest, so every exit is bot-issued and
# must pass this gate.
#
# COMPLIANCE_MAX_DAILY_TRADES = 10 in both editions. record_trade() is side-agnostic, so
# ANY ten trades exhaust the budget — this is not about filling ten slots.
#
# The codebase already ratified this exact principle one layer up —
# gatekeeper.py:239: "#2031: nur BUY — eine risikoreduzierende SELL darf NIE durch das
# (weiche, selbst gesetzte) Tageslimit blockiert werden." check_trade breaks that
# invariant. This restores it, behind a flag, using the SAME predicate the #2065
# exemption already uses at compliance.py:422-426 — not a second definition of "exit".

from __future__ import annotations

import pytest

from core.compliance import ComplianceGuardian


def _order(side="sell", qty=10.0, held=10.0, price=100.0):
    o = {
        "symbol": "NVDA",
        "side": side,
        "quantity": qty,
        "price": price,
        "asset_class": "us_equity",
    }
    if held is not None:
        o["held_qty"] = held
    return o


@pytest.fixture
def exhausted():
    """A guardian whose daily budget is spent — the state that blocks the stop-loss."""
    g = ComplianceGuardian()
    g.daily_trades = g.max_daily_trades
    return g


class TestTodaysBehaviourWhenTheFlagIsOff:
    def test_the_cap_still_blocks_an_exit(self, exhausted, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(),
            "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
            False,
            raising=False,
        )
        assert exhausted.check_trade(_order()) is False


class TestAnExitIsNotAnEntry:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(),
            "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
            True,
            raising=False,
        )

    def test_a_stop_loss_can_leave_a_full_book(self, exhausted):
        """The point of the whole PR: a bot that cannot sell cannot cut a loss."""
        assert exhausted.check_trade(_order()) is True

    def test_a_buy_is_still_capped(self, exhausted):
        """The cap must keep doing its job. Only risk-REDUCING trades are exempt."""
        assert exhausted.check_trade(_order(side="buy", held=0.0)) is False

    def test_a_partial_exit_is_exempt(self, exhausted):
        assert exhausted.check_trade(_order(qty=4.0, held=10.0)) is True

    def test_selling_more_than_held_is_not_an_exit(self, exhausted):
        """It would flip to short — that increases risk, so the cap must hold."""
        assert exhausted.check_trade(_order(qty=25.0, held=10.0)) is False

    def test_a_naked_short_is_not_an_exit(self, exhausted):
        assert exhausted.check_trade(_order(qty=10.0, held=0.0)) is False

    def test_a_sell_without_held_qty_is_not_an_exit(self, exhausted):
        """⚠ Fail-closed. This is the case that would silently un-cap the daily limit if
        a caller forgot the field — and base.py DID forget it (see the other test file).
        """
        assert exhausted.check_trade(_order(held=None)) is False

    @pytest.mark.parametrize("junk", ["n/a", None, ""])
    def test_unparseable_held_qty_is_not_an_exit(self, exhausted, junk):
        assert exhausted.check_trade(_order(held=junk)) is False


class TestTheCapIsUntouchedWhileBudgetRemains:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(),
            "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
            True,
            raising=False,
        )

    def test_a_buy_under_budget_still_passes(self):
        g = ComplianceGuardian()
        g.daily_trades = 0
        assert g.check_trade(_order(side="buy", held=0.0)) is True


class TestNoInfiniteChurn:
    """The worry: exempting SELLs lets the bot sell/rebuy forever. It cannot — the re-BUY
    is still capped, so each cycle costs a budget slot that is never refunded. The loop
    is bounded by the cap it never escapes."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(),
            "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
            True,
            raising=False,
        )

    def test_the_rebuy_after_an_exempt_exit_is_still_blocked(self, exhausted):
        assert exhausted.check_trade(_order()) is True  # exit out
        assert (
            exhausted.check_trade(_order(side="buy", held=0.0)) is False
        )  # cannot re-enter


class TestTheWarningDoesNotFireOnAnExemptExit:
    """compliance.py's "Max daily trades reached." string feeds an operator Slack alert
    reading "Trading halted." Emitting it for a trade we then allow would make the audit
    trail say the opposite of what happened."""

    def test_no_halt_warning_for_an_exempt_exit(self, exhausted, monkeypatch, caplog):
        import logging

        import config

        monkeypatch.setattr(
            config.get_config(),
            "COMPLIANCE_ALLOW_RISK_REDUCING_EXITS",
            True,
            raising=False,
        )
        with caplog.at_level(logging.WARNING):
            assert exhausted.check_trade(_order()) is True
        assert "Max daily trades" not in caplog.text


class TestTheExemptionReachesTheDefaultStopPath:
    """⚠ The predicate is fail-CLOSED, so a caller that omits `held_qty` gets no
    exemption. `base.py::_submit_order_safe` built its compliance order WITHOUT that key
    — so the RLAgent stop-loss (ACTIVE_STRATEGY defaults to "RLAgent") would have stayed
    blocked no matter how correct the guardian was. Correct logic plus an uninformed
    caller is a dead fix; that shape shipped twice today.
    """

    def test_base_submit_order_safe_passes_held_qty_through(self):
        import inspect

        from core.strategies.base import BaseStrategy

        sig = inspect.signature(BaseStrategy._submit_order_safe)
        assert "held_qty" in sig.parameters, (
            "_submit_order_safe cannot tell the guardian this is an exit — every SELL "
            "from the default strategy path stays capped"
        )
        assert (
            sig.parameters["held_qty"].default == 0.0
        ), "held_qty must default to 0.0 so existing BUY callers stay non-exempt"

    def test_the_rl_sell_sites_declare_the_held_quantity(self):
        """The two SELL call sites must pass it; the BUY site must not."""
        import inspect

        from core.strategies import rl_execution

        src = inspect.getsource(rl_execution)
        assert src.count("held_qty=") == 2, (
            "expected exactly the two SELL call sites to declare held_qty — a BUY that "
            "declares it would exempt an entry from the cap"
        )
