"""#3248 Phase 1 — RR (25Δ risk-reversal) corpus builder pure logic.

The builder script (research/build_rr_history.py) does the Alpaca I/O; ALL the
maths that a test must pin lives in research/rr_corpus.py and is exercised here
WITHOUT network:

  (a) Black-Scholes inversion (Brent) recovers a KNOWN implied vol from a
      synthetic option price, and degenerate / non-converging contracts are
      DROPPED (never enter the corpus) — the Archon sanity-guard requirement.
  (b) the 25Δ wing selection + risk_reversal_from_chain compute
      RR = IV(25Δ call) − IV(25Δ put) on the surviving contracts.
"""

import datetime as dt
import math

import pytest

from research import rr_corpus

# ---------------------------------------------------------------------------
# (a) BS inversion recovers a known IV; degenerate contracts are dropped
# ---------------------------------------------------------------------------


class TestBSInversion:
    def test_recovers_known_iv_call(self):
        S, K, T, sigma = 100.0, 100.0, 30.0 / 365.0, 0.30
        price = rr_corpus.bs_price(S, K, T, rr_corpus.RISK_FREE, sigma, "C")
        iv = rr_corpus.invert_iv(price, S, K, T, "C")
        assert iv == pytest.approx(0.30, abs=1e-4)

    def test_recovers_known_iv_put(self):
        S, K, T, sigma = 100.0, 95.0, 30.0 / 365.0, 0.28
        price = rr_corpus.bs_price(S, K, T, rr_corpus.RISK_FREE, sigma, "P")
        iv = rr_corpus.invert_iv(price, S, K, T, "P")
        assert iv == pytest.approx(0.28, abs=1e-4)

    def test_non_converging_price_is_dropped(self):
        # A price at/below intrinsic (0 for an ATM call) has no positive-vol root.
        assert rr_corpus.invert_iv(0.0, 100.0, 100.0, 30.0 / 365.0, "C") is None

    def test_iv_above_cap_is_dropped(self):
        # A price near the spot implies a vol far past the cap -> no in-band root.
        assert rr_corpus.invert_iv(60.0, 100.0, 100.0, 30.0 / 365.0, "C") is None

    def test_bad_inputs_are_dropped(self):
        assert rr_corpus.invert_iv(5.0, 0.0, 100.0, 30.0 / 365.0, "C") is None
        assert rr_corpus.invert_iv(5.0, 100.0, 100.0, 0.0, "C") is None
        assert rr_corpus.invert_iv(float("nan"), 100.0, 100.0, 0.08, "C") is None


class TestContractFromBar:
    def _expiry(self, tag, days=30):
        return tag + dt.timedelta(days=days)

    def test_in_band_25delta_contract_kept(self):
        tag = dt.date(2024, 6, 3)
        S, K, sigma = 100.0, 105.0, 0.30
        T = 30.0 / 365.0
        price = rr_corpus.bs_price(S, K, T, rr_corpus.RISK_FREE, sigma, "C")
        c = rr_corpus.contract_from_bar("C", K, self._expiry(tag), price, S, tag)
        assert c is not None
        assert c["right"] == "C"
        assert c["iv"] == pytest.approx(0.30, abs=1e-3)
        assert 0.10 <= abs(c["delta"]) <= 0.40  # inside the 25Δ band

    def test_deep_itm_out_of_band_delta_dropped(self):
        # Deep ITM call: delta ~0.99, far outside the 25Δ band -> dropped.
        tag = dt.date(2024, 6, 3)
        S, K, sigma = 100.0, 50.0, 0.30
        T = 30.0 / 365.0
        price = rr_corpus.bs_price(S, K, T, rr_corpus.RISK_FREE, sigma, "C")
        assert (
            rr_corpus.contract_from_bar("C", K, self._expiry(tag), price, S, tag)
            is None
        )

    def test_non_converging_contract_dropped(self):
        tag = dt.date(2024, 6, 3)
        assert (
            rr_corpus.contract_from_bar("C", 100.0, self._expiry(tag), 0.0, 100.0, tag)
            is None
        )


# ---------------------------------------------------------------------------
# (b) 25Δ risk-reversal from a set of contract bars
# ---------------------------------------------------------------------------


class TestRiskReversalFromBars:
    def test_bullish_skew_positive_rr(self):
        tag = dt.date(2024, 6, 3)
        expiry = tag + dt.timedelta(days=30)
        T = 30.0 / 365.0
        call_sigma, put_sigma = 0.34, 0.28  # calls richer -> upside skew

        def bar(right, K, sigma):
            price = rr_corpus.bs_price(100.0, K, T, rr_corpus.RISK_FREE, sigma, right)
            return {"right": right, "strike": K, "expiry": expiry, "price": price}

        rows = [
            bar("C", 105.0, call_sigma),
            bar("C", 110.0, call_sigma),
            bar("P", 95.0, put_sigma),
            bar("P", 90.0, put_sigma),
        ]
        rr, n = rr_corpus.risk_reversal_from_bars(rows, spot=100.0, tag=tag)
        assert rr is not None
        assert n >= 2
        # every surviving call has IV 0.34, every put 0.28 -> RR = 0.06 exactly.
        assert rr == pytest.approx(call_sigma - put_sigma, abs=1e-3)
        assert rr > 0

    def test_missing_side_yields_none(self):
        tag = dt.date(2024, 6, 3)
        expiry = tag + dt.timedelta(days=30)
        T = 30.0 / 365.0
        price = rr_corpus.bs_price(100.0, 105.0, T, rr_corpus.RISK_FREE, 0.30, "C")
        rows = [{"right": "C", "strike": 105.0, "expiry": expiry, "price": price}]
        rr, n = rr_corpus.risk_reversal_from_bars(rows, spot=100.0, tag=tag)
        assert rr is None

    def test_all_degenerate_yields_none(self):
        tag = dt.date(2024, 6, 3)
        expiry = tag + dt.timedelta(days=30)
        rows = [
            {"right": "C", "strike": 100.0, "expiry": expiry, "price": 0.0},
            {"right": "P", "strike": 100.0, "expiry": expiry, "price": 0.0},
        ]
        rr, n = rr_corpus.risk_reversal_from_bars(rows, spot=100.0, tag=tag)
        assert rr is None
        assert n == 0
