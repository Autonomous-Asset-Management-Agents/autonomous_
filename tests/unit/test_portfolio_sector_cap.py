# tests/unit/test_portfolio_sector_cap.py
"""#2654 (Epic #2655, Weg 1) — approved sector constraints drive native REDUCE recommendations.

The PortfolioManager's rebalance pass gains an ADDITIVE sector-cap stage: HITL-approved relative
sector caps (loaded from the local constraint store, written only via the four-eyes POST of #2653)
are enforced by market-value-proportional REDUCE recommendations across the sector's tradable
names. Honesty rules (Archon audit 2026-08-04): a cooldown-blocked residual is a WARNING
(``SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED``), never a silent breach; constraints without a
symbol->sector map this cycle warn ``SECTOR_CAP_NO_SECTOR_DATA`` (the map source is a tracked
follow-up brick in portfolio_context). Without constraints the output is byte-identical to today.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from core.governance.portfolio_constraints import load_approved_constraints
from core.portfolio_manager import PortfolioManager

SECTORS = {"NVDA": "Technology", "MSFT": "Technology", "XOM": "Energy"}


def _pm(weights_pct, capital=100_000.0):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm._trim_respects_conviction = True  # per-name trim only past the 25% cap
    pm._max_position_pct = 0.25
    pm._drift_threshold_pct = 3.0
    pm.max_positions = 10
    pm._position_scores = {
        sym: MagicMock(market_value=capital * (pct / 100.0), total_score=70.0)
        for sym, pct in weights_pct.items()
    }
    pm.refresh_positions = lambda: None
    pm._can_trade_symbol = lambda s: True
    return pm


def _capped(constraints):
    return patch(
        "core.portfolio_manager.load_approved_constraints", return_value=constraints
    )


class TestSectorCapRecommendations:
    def test_no_constraints_is_byte_identical(self):
        pm = _pm({"NVDA": 20.0, "MSFT": 8.0})
        with _capped({}):
            assert pm.get_rebalance_recommendations(symbol_sector_map=SECTORS) == []

    def test_sector_over_cap_emits_proportional_reduces(self):
        # Technology = 28% vs approved cap 20% -> excess $8,000 split by market value.
        pm = _pm({"NVDA": 20.0, "MSFT": 8.0, "XOM": 5.0})
        with _capped({"Technology": 0.20}):
            recs = pm.get_rebalance_recommendations(symbol_sector_map=SECTORS)
        by_sym = {r["symbol"]: r for r in recs}
        assert set(by_sym) == {"NVDA", "MSFT"}
        assert all(r["action"] == "REDUCE" for r in recs)
        assert by_sym["NVDA"]["adjustment_value"] == pytest.approx(-8000 * 20 / 28)
        assert by_sym["MSFT"]["adjustment_value"] == pytest.approx(-8000 * 8 / 28)
        assert sum(r["adjustment_value"] for r in recs) == pytest.approx(-8000.0)
        # the sell hook needs market_value; the operator needs an explainable reason
        assert by_sym["NVDA"]["market_value"] == pytest.approx(20_000.0)
        assert "SECTOR_CAP" in by_sym["NVDA"]["reasoning"]
        assert "Technology" in by_sym["NVDA"]["reasoning"]

    def test_sector_under_cap_is_untouched(self):
        pm = _pm({"NVDA": 10.0, "MSFT": 5.0})
        with _capped({"Technology": 0.20}):
            assert pm.get_rebalance_recommendations(symbol_sector_map=SECTORS) == []

    def test_cooldown_blocked_residual_warns_not_silent(self, caplog):
        # Tech 28% vs cap 15% -> excess $13k, but NVDA ($20k) is cooldown-locked: only MSFT's
        # $8k is reducible -> partial trim + explicit WARNING (no silent breach).
        pm = _pm({"NVDA": 20.0, "MSFT": 8.0})
        pm._can_trade_symbol = lambda s: s != "NVDA"
        with _capped({"Technology": 0.15}), caplog.at_level("WARNING"):
            recs = pm.get_rebalance_recommendations(symbol_sector_map=SECTORS)
        assert [r["symbol"] for r in recs] == ["MSFT"]
        assert recs[0]["adjustment_value"] == pytest.approx(-8000.0)
        assert any(
            "SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED" in m for m in caplog.messages
        )

    def test_constraints_without_sector_map_warn_not_silent(self, caplog):
        # portfolio_context ships empty maps until the sector-source brick lands — approved
        # caps must surface that gap loudly instead of silently not enforcing.
        pm = _pm({"NVDA": 20.0, "MSFT": 8.0})
        with _capped({"Technology": 0.20}), caplog.at_level("WARNING"):
            recs = pm.get_rebalance_recommendations(symbol_sector_map=None)
        assert recs == []
        assert any("SECTOR_CAP_NO_SECTOR_DATA" in m for m in caplog.messages)

    def test_existing_drift_reduce_counts_toward_the_excess(self):
        # NVDA 30% already gets the conviction-cap drift REDUCE (-$5k to 25%); with a 25% tech
        # cap the sector excess is the SAME $5k -> no double-dip second recommendation.
        pm = _pm({"NVDA": 30.0})
        with _capped({"Technology": 0.25}):
            recs = pm.get_rebalance_recommendations(symbol_sector_map=SECTORS)
        assert len(recs) == 1
        assert recs[0]["adjustment_value"] == pytest.approx(-5000.0)


class TestConstraintStore:
    def test_missing_file_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        assert load_approved_constraints() == {}

    def test_corrupt_file_fails_safe_to_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        (tmp_path / "portfolio_constraints.json").write_text(
            "{not json", encoding="utf-8"
        )
        assert load_approved_constraints() == {}

    def test_loads_approved_relative_caps_and_drops_invalid(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
        (tmp_path / "portfolio_constraints.json").write_text(
            json.dumps(
                {
                    "approved": {
                        "Technology": 0.20,
                        "Energy": 0.35,
                        "Absolute": 5000,  # not a relative cap -> dropped
                        "Zero": 0,  # dropped
                        "Junk": "x",  # dropped
                    }
                }
            ),
            encoding="utf-8",
        )
        assert load_approved_constraints() == {"Technology": 0.20, "Energy": 0.35}
