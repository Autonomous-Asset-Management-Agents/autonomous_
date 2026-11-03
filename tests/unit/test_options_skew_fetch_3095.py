"""#3095 (b) Step-2 — fetch/state-keys: snap→contract mapping (mock chain, no net)
and the flag-off no-network path. Real fake classes (no SimpleNamespace, CI trap)."""

import datetime as dt

from core import options_skew


class FakeGreeks:
    def __init__(self, delta):
        self.delta = delta


class FakeSnap:
    def __init__(self, iv, delta):
        self.implied_volatility = iv
        self.greeks = FakeGreeks(delta)


class FakeClient:
    def __init__(self, chain):
        self._chain = chain

    def get_option_chain(self, _req):
        return self._chain


class RaisingClient:
    def get_option_chain(self, _req):  # pragma: no cover - must never be called
        raise AssertionError("network must not be touched when flag is OFF")


def _chain():
    # OCC: ROOT + YYMMDD + C|P + strike8 ; right char is at index [-9]
    return {
        "AAPL260320C00150000": FakeSnap(0.30, 0.50),  # ATM call
        "AAPL260320C00160000": FakeSnap(0.34, 0.26),  # ~25Δ call
        "AAPL260320P00150000": FakeSnap(0.31, -0.50),  # ATM put
        "AAPL260320P00140000": FakeSnap(0.28, -0.24),  # ~25Δ put
    }


def test_fetch_risk_reversal_maps_and_computes():
    rr = options_skew.fetch_risk_reversal(
        "AAPL", 150.0, dt.date(2026, 2, 1), client=FakeClient(_chain())
    )
    assert rr is not None
    assert rr == 0.34 - 0.28  # 25Δ call IV minus 25Δ put IV, > 0 (bullish)


def test_fetch_bad_spot_is_none():
    assert options_skew.fetch_risk_reversal("AAPL", 0.0, dt.date(2026, 2, 1)) is None


def test_snap_to_contract_rejects_missing_greeks():
    assert (
        options_skew._snap_to_contract("AAPL260320C00150000", FakeSnap(0.3, None))
        is None
    )


def test_state_keys_flag_off_is_all_none_and_no_network(monkeypatch):
    # default flag is OFF -> all-None, and the (raising) client is never called
    keys = options_skew.options_skew_state_keys(
        "AAPL", 150.0, tag=dt.date(2026, 2, 1), client=RaisingClient()
    )
    assert keys == {
        "risk_reversal": None,
        "risk_reversal_reference": None,
        "risk_reversal_reference_date": None,
    }


def test_state_keys_sim_mode_abstains(monkeypatch):
    monkeypatch.setattr(options_skew, "_enabled", lambda: True)
    monkeypatch.setattr(options_skew, "_sim_mode", lambda: True)
    keys = options_skew.options_skew_state_keys(
        "AAPL", 150.0, tag=dt.date(2026, 2, 1), client=RaisingClient()
    )
    assert all(v is None for v in keys.values())
