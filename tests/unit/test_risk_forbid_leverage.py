# tests/unit/test_risk_forbid_leverage.py
# ADR-RISK-LEV-01 — the cash cap switches itself OFF exactly where it should bind hardest.
#
# THE HOLE (core/risk_manager.py, the cash branch):
#
#     if current_price > 0 and account_cash > 0:        # <-- outer guard
#         slots = max(1, int(num_stocks_in_strategy or 1))
#         max_shares_by_cash = ((account_cash - 50) / slots) / current_price
#         ...
#         if num_shares > max_shares_by_cash:
#             num_shares = max_shares_by_cash
#
# `account_cash > 0` means: once cash is NEGATIVE — i.e. the account has already borrowed —
# the entire cash constraint is SKIPPED. Not clamped, not warned: skipped. The one state
# where "you cannot afford this" is unambiguously true is the one state the check does not
# run in.
#
# The inner arithmetic even has a `if max_shares_by_cash < 0: max_shares_by_cash = 0` guard
# — dead code, because negative cash never reaches it.
#
# GEORG'S CALL (2026-07-17), and it is the better fix: forbid leverage outright rather than
# repair the arithmetic. "sollte es so oder so sein" — a long-only retail bot has no business
# on margin. Alpaca hands a paper account 2x buying power; nothing in the code declines it.
#
# Why a hard cash rule beats repairing the exposure cap: the cap hangs on a broker client
# (`and self.client is not None` — absent client, no cap, no warning) and measures against
# `self.total_capital`, a construction-time snapshot. A cash rule hangs on neither.
#
# DEFAULT ON (`RISK_FORBID_LEVERAGE`, set false to allow margin again). ONE reason, and it is
# the only one that survived scrutiny:
#
#   ONE-SIDED BY CONSTRUCTION — it can only ever REDUCE a position, never grow one.
#   TestTheRuleCanOnlyEverShrink proves it across the cash x slots grid instead of asserting
#   it in prose; an adversarial review brute-forced 53,760 combinations (client present and
#   absent, negative market_value, cash from -1e9 to 1e6 incl. +/-inf and NaN, slots -5..1e4,
#   4 prices, 2 ATRs, 3 convictions, allow_fractional, 2 SL multipliers) and found zero
#   violations. Every stage after the cash block is monotone non-decreasing in num_shares.
#   Worst case: a missed trade. Never additional risk.
#
# ⚠ A SECOND JUSTIFICATION WAS CLAIMED HERE AND IS REFUTED — it is recorded because the
# refutation is the useful part. The claim: "not a strategy change; it binds only when
# cash < 0, i.e. exposure > 100%, which MAX_TOTAL_EXPOSURE_PCT already forbids, so it is a
# no-op whenever that cap works." FALSE. The cap's denominator is the frozen total_capital
# while cash is live, so the two come apart. Executed counter-example: RM built at $100k
# equity, market falls to equity $60k / positions $60k / cash $0.00, client present, no
# exception — the cap still grants $35,000 of headroom:
#     flag OFF -> 99.99 shares ($9,999)      flag ON -> 0.0
# The rule binds while the cap is FUNCTIONING. Blocking is right there (you cannot buy
# $9,999 with $0), but this IS a live behaviour change and is not to be sold as a no-op.
#
# ⚠ SO IS THIS, deliberately kept: rl_execution.py:765-767 catches a get_account failure,
# logs, sets `cash = 0.0` and falls THROUGH to the sizer (both return-guards sit inside the
# try). There, cash=0.0 also means "broker unreachable". With the flag ON that becomes
# "no new buy" instead of today's "buy blind, sized off a capital snapshot, without having
# read the account". Fail-CLOSED, per CLAUDE.md 5.6 / BUG-AI-105 — but a change, and an
# earlier draft of this file denied it existed.
#
# ⚠ NOT built on a measured incident. A local account sits at ~194% exposure, but it belongs
# to a DIFFERENT program and is only used for testing — it is NOT evidence our bot leverages.
# What IS measured: with account_cash = -50,000 the sizer logs "final $15,750 (157.50 shares)
# [capped by max%]" — it allocates fresh capital on an account $50k in debt and believes
# itself constrained. The code path is real and executed; the damage in the wild is not
# demonstrated. This ships as a guarantee, not as a repair.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.risk_manager import RiskManager


def _rm(equity: float = 100_000.0):
    """A RiskManager with no broker client — the exposure cap is skipped in this shape
    (its outer guard needs `self.client is not None`), which isolates the CASH path."""
    return RiskManager(None, equity)


_PRICE = 100.0
_ATR = 2.0
_SL_MULT = 3.0


def _size(rm, cash, slots=1, **kw):
    # Keyword-only on purpose: the third POSITIONAL parameter is `confidence: str`,
    # not `current_price` — passing the price positionally silently sizes off a
    # confidence of "100.0" and the cash path never sees a price.
    return rm.calculate_position_size(
        stop_loss_atr_multiplier=_SL_MULT,
        atr=_ATR,
        current_price=_PRICE,
        account_cash=cash,
        num_stocks_in_strategy=slots,
        **kw,
    )


class TestBorrowedCashCannotBuyMore:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", True, raising=False)

    def test_negative_cash_buys_nothing(self):
        """The whole point. Cash below zero means the account is already borrowing; adding
        exposure on top is the definition of leverage."""
        assert _size(_rm(), cash=-50_000.0) == 0.0

    def test_zero_cash_buys_nothing(self):
        assert _size(_rm(), cash=0.0) == 0.0

    def test_an_order_may_not_exceed_available_cash(self):
        """$1,000 cash at $100/share: at most ~9 shares after the $50 buffer — never 50."""
        n = _size(_rm(), cash=1_000.0)
        assert (
            n * _PRICE <= 1_000.0
        ), f"{n} shares x ${_PRICE} exceeds ${1_000.0} of cash"

    def test_ample_cash_is_not_constrained_by_this_rule(self):
        """The rule must not become a second, tighter cap. With plenty of cash the size is
        decided by risk/ATR as before, not by us."""
        rich = _size(_rm(), cash=1_000_000.0)
        assert rich > 0


class TestTheRuleCanOnlyEverShrink:
    """⚠ THE SAFETY AXIOM — the reason this may default ON without validation. Flipping it
    must be incapable of increasing a position in ANY input combination."""

    @pytest.mark.parametrize(
        "cash", [-100_000.0, -1.0, 0.0, 49.0, 51.0, 5_000.0, 1_000_000.0]
    )
    @pytest.mark.parametrize("slots", [1, 10, 500])
    def test_never_increases(self, monkeypatch, cash, slots):
        import config

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", False, raising=False)
        legacy = _size(_rm(), cash=cash, slots=slots)
        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", True, raising=False)
        guarded = _size(_rm(), cash=cash, slots=slots)
        assert guarded <= legacy + 1e-9, (
            f"the guard INCREASED size (cash={cash}, slots={slots}): {legacy} -> {guarded}. "
            f"It must only ever shrink."
        )


class TestTheAxiomHoldsWithABrokerClientToo:
    """⚠ Found by adversarial review: every case above uses RiskManager(None, ...), which by
    its own docstring SKIPS the exposure cap. So the suite proved the axiom only on the path
    where the cap is absent — and the cash x exposure-cap interaction, precisely where a
    violation could hide, went untested. It holds; now it is pinned."""

    @pytest.mark.parametrize("cash", [-50_000.0, 0.0, 5_000.0, 1_000_000.0])
    def test_never_increases_while_the_exposure_cap_is_live(self, monkeypatch, cash):
        import config

        monkeypatch.setattr(config, "MAX_TOTAL_EXPOSURE_PCT", 0.95, raising=False)

        def _rm_with_client():
            client = MagicMock()
            client.get_all_positions.return_value = [{"market_value": "40000.0"}]
            return RiskManager(client, 100_000.0)

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", False, raising=False)
        legacy = _size(_rm_with_client(), cash=cash)
        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", True, raising=False)
        guarded = _size(_rm_with_client(), cash=cash)
        assert guarded <= legacy + 1e-9, (
            f"the guard INCREASED size with the exposure cap live (cash={cash}): "
            f"{legacy} -> {guarded}"
        )


class TestTheRefusalSaysSo:
    """⚠ Found by adversarial review, and it matters more than it looks. The INFO log at
    risk_manager.py is gated on `num_shares > 0` — so it never fires for the one case this
    rule creates: a zeroed order. Without a log of its own, ADR-R10 would be a NEW silent
    producer of zeros, in a system where 1013 orders already died unexplained on 2026-07-16
    (order_executor.py `if qty > 0:` has no else). Refusing a trade in silence is the bug
    we are fixing elsewhere; shipping one here would be indefensible."""

    def test_a_refused_order_logs_a_warning(self, monkeypatch, caplog):
        import logging

        import config

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", True, raising=False)
        with caplog.at_level(logging.WARNING):
            n = _size(_rm(), cash=-50_000.0)

        assert n == 0.0
        assert any(
            "ADR-R10" in r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING
        ), (
            "the guard zeroed an order and said nothing — CLAUDE.md 5.6, and the whole "
            "reason the 194% incident was unreadable"
        )

    def test_an_ordinary_cash_clamp_does_not_cry_wolf(self, monkeypatch, caplog):
        """The WARNING is for a REFUSAL (size -> 0), not for the everyday cash trim. A
        rule that logs on every routine clamp trains people to ignore it."""
        import logging

        import config

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", True, raising=False)
        with caplog.at_level(logging.WARNING):
            n = _size(_rm(), cash=1_000.0)

        assert n > 0.0, "1000 USD of cash must still buy something"
        assert not [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "ADR-R10" in r.getMessage()
        ], "a normal cash clamp must not warn — only an outright refusal does"


class TestTheEscapeHatchStillWorks:
    """The flag ships ON, so the thing worth pinning is the way OUT. If margin is ever
    wanted deliberately, `RISK_FORBID_LEVERAGE=false` must restore the old path exactly —
    not approximately."""

    def test_setting_the_flag_false_restores_margin(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "RISK_FORBID_LEVERAGE", False, raising=False)
        assert _size(_rm(), cash=-50_000.0) > 0.0, (
            "RISK_FORBID_LEVERAGE=false must reach the pre-ADR-R10 behaviour verbatim; "
            "if it does not, the flag is not a real off-switch and the change is "
            "irreversible in the field"
        )
