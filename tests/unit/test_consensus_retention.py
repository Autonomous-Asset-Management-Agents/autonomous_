"""#3180 Consensus Retention Gate — pure veto helper + PM live-consensus store (plan #3189)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import core.consensus_retention as cr


class _FakePM:
    def __init__(self, consensus=None):
        self._c = consensus

    def get_live_consensus(self, symbol):  # noqa: ARG002
        return self._c


class TestConsensusRetentionVeto:
    def test_gate_off_default_is_byte_identical_no_veto(self, monkeypatch):
        monkeypatch.setattr(cr, "_threshold", lambda: 0.0)
        assert cr.consensus_retention_veto("X", "rotation", _FakePM(0.9)) is False

    def test_gate_on_vetoes_when_consensus_above_threshold(self, monkeypatch):
        monkeypatch.setattr(cr, "_threshold", lambda: 0.5)
        assert cr.consensus_retention_veto("X", "rotation", _FakePM(0.6)) is True

    def test_gate_on_no_veto_at_or_below_threshold(self, monkeypatch):
        monkeypatch.setattr(cr, "_threshold", lambda: 0.5)
        assert cr.consensus_retention_veto("X", "model", _FakePM(0.5)) is False
        assert cr.consensus_retention_veto("X", "model", _FakePM(0.4)) is False

    def test_risk_exit_is_never_vetoed(self, monkeypatch):
        # Belt-and-suspenders: even at max consensus, a risk exit must fire.
        monkeypatch.setattr(cr, "_threshold", lambda: 0.5)
        assert cr.consensus_retention_veto("X", "risk", _FakePM(0.99)) is False

    def test_missing_consensus_fails_open(self, monkeypatch):
        monkeypatch.setattr(cr, "_threshold", lambda: 0.5)
        assert cr.consensus_retention_veto("X", "rotation", _FakePM(None)) is False

    def test_pm_without_accessor_fails_open(self, monkeypatch):
        monkeypatch.setattr(cr, "_threshold", lambda: 0.5)
        assert cr.consensus_retention_veto("X", "rotation", object()) is False

    def test_threshold_reads_config_default_zero(self):
        # No monkeypatch: the shipped default (CONSENSUS_RETENTION_THRESHOLD=0.0) ⇒ OFF.
        assert cr._threshold() == 0.0
        assert cr.consensus_retention_veto("X", "rotation", _FakePM(0.9)) is False


class TestRiskByteIdentical:
    """Property: a RISK exit is NEVER vetoed — for ANY (consensus, threshold) pair the
    output is False, so hard stops (hard/trailing/loss) are bit-for-bit unaffected by the
    gate. This is the core safety invariant (output ⊆ baseline for risk exits)."""

    @pytest.mark.parametrize("threshold", [0.0, 0.1, 0.35, 0.5, 0.65, 0.9, 1.0])
    @pytest.mark.parametrize(
        "consensus", [None, 0.0, 0.2, 0.35, 0.5, 0.65, 0.8, 0.99, 1.0]
    )
    def test_risk_exit_never_vetoed_across_grid(
        self, monkeypatch, threshold, consensus
    ):
        monkeypatch.setattr(cr, "_threshold", lambda: threshold)
        assert cr.consensus_retention_veto("X", "risk", _FakePM(consensus)) is False


class TestLiveConsensusStore:
    def _pm(self):
        from core.portfolio_manager import PortfolioManager

        return PortfolioManager(client=MagicMock(), total_capital=100000.0)

    def test_set_get_roundtrip(self):
        pm = self._pm()
        pm.set_live_consensus("AAPL", 0.62)
        assert pm.get_live_consensus("AAPL") == 0.62
        assert pm.get_live_consensus("MSFT") is None

    def test_nan_and_inf_ignored(self):
        pm = self._pm()
        pm.set_live_consensus("AAPL", float("nan"))
        pm.set_live_consensus("MSFT", float("inf"))
        assert pm.get_live_consensus("AAPL") is None
        assert pm.get_live_consensus("MSFT") is None

    def test_cleared_on_full_exit(self):
        pm = self._pm()
        pm.set_live_consensus("AAPL", 0.7)
        pm.clear_conviction("AAPL")
        assert pm.get_live_consensus("AAPL") is None


class TestConfigParity:
    """BORA: the flag must exist in BOTH editions and default to 0.0 (gate OFF)."""

    def _load_oss_config(self):
        import importlib.util
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "config_oss_consensus_retention", str(root / "config.oss.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_flag_present_and_defaults_off_both_editions(self):
        import os

        import config as ent

        oss = self._load_oss_config()
        # The attribute must exist in both editions (BORA parity).
        assert hasattr(ent.get_config(), "CONSENSUS_RETENTION_THRESHOLD")
        assert hasattr(oss, "CONSENSUS_RETENTION_THRESHOLD")
        # Default OFF (0.0) when the env override is not set.
        if os.getenv("CONSENSUS_RETENTION_THRESHOLD") is None:
            assert ent.get_config().CONSENSUS_RETENTION_THRESHOLD == 0.0
            assert oss.CONSENSUS_RETENTION_THRESHOLD == 0.0
