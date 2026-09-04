# tests/unit/test_coverage_sizing_3210.py
"""#3210 — Coverage prudence sizing: the pure ``coverage_size_discount`` helper.

Mirrors the #1953 vol / #3199 skew contract (config-gated, dark by default,
fail-open, PURE so the sizer and the MiFID II audit mirror never drift). A
directional-vote coverage fraction ``c`` ∈ (0, 1] becomes a bounded, concave,
WEAKENED discount::

    discount = 1 - lambda * (1 - sqrt(c))

  c=1 (full coverage)      -> 1.0
  thinner coverage         -> < 1.0 (concave, attenuated by lambda)
  lambda 0 / bad c         -> 1.0 exactly (byte-identical, fail-open)

Prudence, NOT alpha (#3210 / ADR-R17 / STRATEGY_VALIDATION §7).
"""

import math

import pytest

pytestmark = pytest.mark.iron_dome


from core.risk_manager import apply_vol_targeting_audit, coverage_size_discount
from core.round_table.consensus import vote_coverage


class _V:
    """Minimal VoteResult stub (agent_name, weight, vetoed)."""

    def __init__(self, agent_name, weight, vetoed=False):
        self.agent_name = agent_name
        self.weight = weight
        self.vetoed = vetoed


class TestVoteCoverageHelper:
    ARMED = {"MomentumAgent": 0.45, "LSTMSignalAgent": 0.40, "FundamentalsAgent": 0.35}

    def test_full_coverage_all_armed_voted(self):
        votes = [_V(n, w) for n, w in self.ARMED.items()]
        assert vote_coverage(votes, self.ARMED) == pytest.approx(1.0)

    def test_abstention_lowers_coverage_regardless_of_cause(self):
        # Fundamentals abstains this round (weight 0) -> in denom, not in num.
        votes = [
            _V("MomentumAgent", 0.45),
            _V("LSTMSignalAgent", 0.40),
            _V("FundamentalsAgent", 0.0),  # abstain / "report not ready"
        ]
        expected = (0.45 + 0.40) / (0.45 + 0.40 + 0.35)
        assert vote_coverage(votes, self.ARMED) == pytest.approx(expected)

    def test_muted_or_unarmed_agent_ignored(self):
        # RL is not in ARMED (structurally muted) -> ignored in numerator.
        votes = [_V("MomentumAgent", 0.45), _V("RLConfidenceAgent", 0.0)]
        assert vote_coverage(votes, self.ARMED) == pytest.approx(0.45 / 1.20)

    def test_vetoed_vote_excluded_from_numerator(self):
        votes = [_V("MomentumAgent", 0.45, vetoed=True), _V("LSTMSignalAgent", 0.40)]
        assert vote_coverage(votes, self.ARMED) == pytest.approx(0.40 / 1.20)

    def test_empty_armed_set_is_none_fail_open(self):
        assert vote_coverage([_V("MomentumAgent", 0.45)], {}) is None


def _covflag(monkeypatch, lam: float) -> None:
    import config

    monkeypatch.setattr(config, "COVERAGE_SIZING_STRENGTH", lam, raising=False)


class TestDarkShipByteIdentity:
    def test_strength_zero_is_one_regardless_of_coverage(self, monkeypatch):
        _covflag(monkeypatch, 0.0)
        for c in (0.1, 0.37, 0.7, 1.0, None, 1.5, 0.0):
            assert coverage_size_discount(c) == 1.0

    def test_full_coverage_is_one_even_when_armed(self, monkeypatch):
        _covflag(monkeypatch, 0.25)
        assert coverage_size_discount(1.0) == 1.0


class TestWeakenedMath:
    def test_lambda_025_min_coverage_is_gentle(self, monkeypatch):
        _covflag(monkeypatch, 0.25)
        expected = 1.0 - 0.25 * (1.0 - math.sqrt(0.37))
        assert coverage_size_discount(0.37) == pytest.approx(expected)
        assert 0.89 < coverage_size_discount(0.37) < 0.91  # ~0.902, gentle trim

    def test_lambda_one_is_raw_sqrt(self, monkeypatch):
        _covflag(monkeypatch, 1.0)
        assert coverage_size_discount(0.49) == pytest.approx(0.7)

    def test_monotone_and_concave(self, monkeypatch):
        _covflag(monkeypatch, 0.5)
        assert (
            coverage_size_discount(0.4)
            < coverage_size_discount(0.7)
            < coverage_size_discount(1.0)
            == 1.0
        )
        # concave: a step near full trims less than a step near empty
        near_full = 1.0 - coverage_size_discount(0.9)
        near_empty = coverage_size_discount(0.5) - coverage_size_discount(0.4)
        assert near_full < near_empty


class TestFailOpen:
    @pytest.mark.parametrize("bad", [None, 0.0, -0.1, 1.5, float("nan"), True, "x"])
    def test_invalid_coverage_is_one(self, monkeypatch, bad):
        _covflag(monkeypatch, 0.25)
        assert coverage_size_discount(bad) == 1.0

    def test_never_exceeds_one(self, monkeypatch):
        _covflag(monkeypatch, 0.5)
        for c in (0.01, 0.5, 0.999, 1.0):
            assert coverage_size_discount(c) <= 1.0


class TestAuditFold:
    """apply_vol_targeting_audit folds coverage: applied == logged (MiFID II Art. 17)."""

    def _all_off_but_coverage(self, monkeypatch, lam):
        import config

        monkeypatch.setattr(
            config, "VOL_TARGETING_SIZING_ENABLED", False, raising=False
        )
        monkeypatch.setattr(config, "SKEW_SIZE_TILT_ENABLED", False, raising=False)
        _covflag(monkeypatch, lam)

    def test_audit_mirror_folds_coverage(self, monkeypatch):
        self._all_off_but_coverage(monkeypatch, 0.25)

        class Ctx:
            risk_size_scaler = 1.0

        ctx = Ctx()
        scaler = apply_vol_targeting_audit(
            ctx, forecast_vol=None, rr_percentile=None, coverage=0.49
        )
        expected = 1.0 - 0.25 * (1.0 - math.sqrt(0.49))  # 0.925
        assert scaler == pytest.approx(expected)
        assert ctx.risk_size_scaler == pytest.approx(expected)  # applied == logged

    def test_audit_no_op_when_all_off(self, monkeypatch):
        self._all_off_but_coverage(monkeypatch, 0.0)

        class Ctx:
            risk_size_scaler = 1.0

        ctx = Ctx()
        scaler = apply_vol_targeting_audit(ctx, None, None, coverage=0.4)
        assert scaler == 1.0
        assert ctx.risk_size_scaler == 1.0  # untouched, byte-identical
