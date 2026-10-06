# tests/unit/test_gatekeeper_pdt_finra_accurate.py
# B1 — the PDT gate blocks every order, and the rule it enforces is not the rule.
#
# WHAT HAPPENS TODAY: gatekeeper.py `if day_trades >= self.PDT_MAX_DAY_TRADES` (=3)
# rejects EVERY symbol -> signal=None -> zero orders for the session, including
# risk-reducing SELLs. The SELL carve-out is inert by the code's own admission
# ("provably inert"): `traded_today` is only populated when GATEKEEPER_PDT_CROSSDAY_EXEMPTION
# is on, and it defaults False.
#
# ⚠ THIS IS NOT A CARELESS BUG — IT IS A DELIBERATE CONSERVATIVE CHOICE. The code says so:
# "Fail-safe: unbekannt/absent -> True -> Block (nie ein FINRA-Under-Block)". The author
# chose to over-block rather than risk a violation. That intent is CORRECT and these tests
# preserve it. What is wrong is narrower:
#
#   The actual FINRA rule (Reg T / FINRA Rule 4210): the pattern-day-trader designation
#   applies at MORE THAN 3 day trades in 5 business days (i.e. the 4th trips it) AND ONLY
#   for a margin account whose equity is BELOW $25,000. At or above $25k the rule does not
#   bind at all.
#
# The code uses 3 (one too strict) and never looks at equity — even though
# portfolio_context.py ALREADY computes it (`equity = _coerce_float(...)`) and simply omits
# it from the returned dict. So a $100k paper account is blocked by a rule that, applied
# correctly, would not apply to it.
#
# The fail-safe stays: unknown equity -> block. We only stop blocking when we can PROVE the
# rule does not apply. Flag-gated, default OFF -> today's behaviour is byte-identical.

from __future__ import annotations

import pytest

from core.round_table.gatekeeper import ComplianceGatekeeper

pytestmark = pytest.mark.asyncio

_PDT_TRIPPED = 4  # more than 3 day trades in 5 business days
_RICH = 100_000.0  # a funded paper account — the rule cannot bind
_POOR = 24_000.0  # below the $25k floor — the rule genuinely binds

_BUY_SCORE = 0.9


def _ctx(**over):
    base = {
        "day_trades_last_5d": _PDT_TRIPPED,
        "max_daily_trades": 10,
        "current_daily_trades": 0,
        "symbol_weights": {},
        "sector_weights": {},
        "symbol_sector_map": {},
        "position_locked": False,
        "traded_today": {},
    }
    base.update(over)
    return base


@pytest.fixture
def gk():
    return ComplianceGatekeeper()


class TestTodaysBehaviourIsUnchangedWhenTheFlagIsOff:
    """The default path must stay byte-identical. This is a compliance gate; loosening it
    by accident is worse than the over-blocking it currently does."""

    async def test_flag_off_still_blocks_at_three_regardless_of_equity(
        self, gk, monkeypatch
    ):
        import config

        monkeypatch.setattr(
            config.get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", False, raising=False
        )
        d = await gk.check("NVDA", _BUY_SCORE, _ctx(day_trades_last_5d=3, equity=_RICH))
        assert (
            d.approved is False
        ), "the default path must keep today's conservative block"


class TestTheRuleIsAppliedAsWritten:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", True, raising=False
        )

    async def test_a_funded_account_is_not_a_pattern_day_trader(self, gk):
        """$100k paper account: FINRA's PDT rule does not apply at all. Blocking it is
        enforcing a rule that does not exist — the reason the bot places zero orders."""
        d = await gk.check(
            "NVDA", _BUY_SCORE, _ctx(day_trades_last_5d=99, equity=_RICH)
        )
        assert d.approved is True, (
            "a $100k account was blocked as a pattern day trader — the rule only binds "
            "below $25k equity"
        )

    async def test_below_the_floor_the_rule_binds_on_the_fourth_day_trade(self, gk):
        d = await gk.check("NVDA", _BUY_SCORE, _ctx(day_trades_last_5d=4, equity=_POOR))
        assert d.approved is False

    async def test_below_the_floor_three_day_trades_is_still_allowed(self, gk):
        """The rule trips at MORE THAN 3 in 5 days. Blocking at 3 is one too strict — it
        forfeits a day trade the account is entitled to."""
        d = await gk.check("NVDA", _BUY_SCORE, _ctx(day_trades_last_5d=3, equity=_POOR))
        assert d.approved is True

    async def test_exactly_at_the_floor_the_rule_does_not_bind(self, gk):
        """FINRA says BELOW $25,000. Exactly $25,000 is not below it."""
        d = await gk.check(
            "NVDA", _BUY_SCORE, _ctx(day_trades_last_5d=99, equity=25_000.0)
        )
        assert d.approved is True


class TestTheFailSafeSurvives:
    """The original intent — never permit a FINRA under-block — must not be traded away
    for convenience. We stop blocking only when we can PROVE the rule does not apply."""

    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        import config

        monkeypatch.setattr(
            config.get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", True, raising=False
        )

    async def test_absent_equity_blocks(self, gk):
        ctx = _ctx(day_trades_last_5d=99)
        ctx.pop("equity", None)
        assert (await gk.check("NVDA", _BUY_SCORE, ctx)).approved is False

    async def test_none_equity_blocks(self, gk):
        assert (await gk.check("NVDA", _BUY_SCORE, _ctx(equity=None))).approved is False

    async def test_unparseable_equity_blocks(self, gk):
        assert (
            await gk.check("NVDA", _BUY_SCORE, _ctx(equity="n/a"))
        ).approved is False

    async def test_zero_equity_blocks(self, gk):
        """0.0 is the coercion default for a missing field — it must never read as
        'below the floor, therefore fine'. It is below the floor AND unknown."""
        assert (await gk.check("NVDA", _BUY_SCORE, _ctx(equity=0.0))).approved is False


class TestEquityActuallyReachesTheGate:
    """⚠ The test that makes the rest matter. portfolio_context ALREADY computed equity
    and simply did not put it in the dict it returns — so the gate could not see it no
    matter how correct its logic is. Correct logic plus a caller that never passes the
    field is still a dead feature; that exact shape shipped twice today already.

    ⚠⚠ AND THE FIRST VERSION OF THIS TEST WAS ITSELF FAKE. It grepped
    `inspect.getsource()` for '"equity"' — which matches the pre-existing
    `getattr(account, "equity", 0.0)` line. It passed with the key removed. A test that
    reads the source instead of the VALUE proves nothing. Assert on what the function
    RETURNS.
    """

    async def test_the_returned_context_carries_equity(self):
        from unittest.mock import MagicMock

        from core.engine.portfolio_context import build_portfolio_context

        account = MagicMock(equity="100000.0", daytrade_count=4, cash="50000.0")
        api = MagicMock()
        api.get_account.return_value = account
        api.get_all_positions.return_value = []
        guardian = MagicMock(daily_trades=0, max_daily_trades=10)

        ctx = await build_portfolio_context(api, guardian)

        assert ctx is not None
        assert "equity" in ctx, (
            "build_portfolio_context computes equity but never returns it — the "
            "gatekeeper cannot apply the $25k floor on data it does not receive"
        )
        assert ctx["equity"] == pytest.approx(100_000.0)

    async def test_the_gate_actually_unblocks_on_that_real_context(self, monkeypatch):
        """End-to-end: the real context object, fed to the real gate, must approve."""
        from unittest.mock import MagicMock

        import config
        from core.engine.portfolio_context import build_portfolio_context

        monkeypatch.setattr(
            config.get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", True, raising=False
        )
        account = MagicMock(equity="100000.0", daytrade_count=99, cash="50000.0")
        api = MagicMock()
        api.get_account.return_value = account
        api.get_all_positions.return_value = []
        guardian = MagicMock(daily_trades=0, max_daily_trades=10)
        guardian.buys_last_hour.return_value = 0  # no recent buys ⇒ pacing inert
        guardian.minutes_since_last_buy.return_value = None
        ctx = await build_portfolio_context(api, guardian)

        decision = await ComplianceGatekeeper().check("NVDA", _BUY_SCORE, ctx)

        assert decision.approved is True, (
            "99 day trades on a $100k account still blocked — the rule does not apply "
            "above the $25k floor, and this is the path the engine actually runs"
        )
