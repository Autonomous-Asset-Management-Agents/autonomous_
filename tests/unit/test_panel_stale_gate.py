# tests/unit/test_panel_stale_gate.py
# #1907 (MLR-7) Increment 2/3 — Panel-Stale/Coverage-Gate (plan §5.5, tests R8-R10).
#
# WHY IT EXISTS: the offline full-universe panel is the data foundation for the
# cross-sectional retrains (#2388). A retrain quietly running on a panel that is weeks
# old, or on a window the membership table does not cover, would bake staleness or
# survivorship/look-ahead bias into a trading model — invisibly. The gate makes that
# failure LOUD and BLOCKING, and it is default-ON (conservative): no training/benchmark
# on a panel older than PANEL_MAX_AGE_DAYS, none outside membership coverage.
#
# BORA: PANEL_STALE_GATE_ENABLED / PANEL_MAX_AGE_DAYS exist with identical defaults in
# config.py and config.oss.py (mirroring the LSTM_RANK_PANEL_STRATEGY_INDEPENDENT
# idiom). Gate OFF -> the check answers "allowed" immediately and does nothing else —
# byte-identical posture.

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import logging
import os
import types

import pytest

import config
import core.data_provider as dp
import core.panel_stale_gate as psg


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_panel_gate", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _manifest(tmp_path, coverage_end: str):
    p = tmp_path / "panel_manifest.json"
    p.write_text(
        json.dumps({"artifact": "full_universe_panel", "coverage_end": coverage_end}),
        encoding="utf-8",
    )
    return p


def _stub_config(monkeypatch, enabled=True, max_age_days=5):
    monkeypatch.setattr(
        psg,
        "get_config",
        lambda: types.SimpleNamespace(
            PANEL_STALE_GATE_ENABLED=enabled, PANEL_MAX_AGE_DAYS=max_age_days
        ),
    )


TODAY = dt.date(2026, 7, 22)


# =========================================================================== #
# 1. Config parity — both editions, identical conservative defaults.
# =========================================================================== #
class TestConfigParity:
    def test_enterprise_defaults(self):
        cfg = config.get_config()
        assert cfg.PANEL_STALE_GATE_ENABLED is True  # default-safe: gate ON
        assert cfg.PANEL_MAX_AGE_DAYS == 5
        assert "PANEL_STALE_GATE_ENABLED" in config.RuntimeConfigState.model_fields
        assert "PANEL_MAX_AGE_DAYS" in config.RuntimeConfigState.model_fields

    def test_oss_defaults_mirror_enterprise(self):
        oss = _load_config_oss()
        assert oss.PANEL_STALE_GATE_ENABLED is True
        assert oss.PANEL_MAX_AGE_DAYS == 5
        assert oss.get_config().PANEL_STALE_GATE_ENABLED is True
        assert oss.get_config().PANEL_MAX_AGE_DAYS == 5

    def test_keys_are_env_tunable_not_inherited(self):
        """The gate must be governed by its own env keys only (BORA flag
        minimalism) — never derived from another flag or tier."""
        assert os.getenv("PANEL_STALE_GATE_ENABLED") in (None, "", "True", "true")
        assert config.get_config().PANEL_STALE_GATE_ENABLED is True


# =========================================================================== #
# 2. R8 — a stale panel blocks, at WARNING (never DEBUG, §5.6).
# =========================================================================== #
class TestStalePanelBlocks:
    def test_ten_day_old_panel_is_blocked(self, monkeypatch, tmp_path, caplog):
        _stub_config(monkeypatch, enabled=True, max_age_days=5)
        manifest = _manifest(tmp_path, (TODAY - dt.timedelta(days=10)).isoformat())
        with caplog.at_level(logging.WARNING):
            allowed, reason = psg.panel_gate_check(manifest, today=TODAY)
        assert allowed is False
        assert "10" in reason and "5" in reason  # names the age and the limit
        warnings = [
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        ]
        assert any("10" in m and "5" in m for m in warnings), (
            "the block must be logged at WARNING naming panel age and limit; "
            f"got: {warnings!r}"
        )

    def test_missing_manifest_blocks_fail_closed(self, monkeypatch, tmp_path, caplog):
        _stub_config(monkeypatch, enabled=True)
        with caplog.at_level(logging.WARNING):
            allowed, _reason = psg.panel_gate_check(
                tmp_path / "no_such_manifest.json", today=TODAY
            )
        assert allowed is False
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_unreadable_manifest_blocks_fail_closed(
        self, monkeypatch, tmp_path, caplog
    ):
        _stub_config(monkeypatch, enabled=True)
        bad = tmp_path / "bad.json"
        bad.write_text("not json at all", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            allowed, _reason = psg.panel_gate_check(bad, today=TODAY)
        assert allowed is False


# =========================================================================== #
# 3. R9 — a fresh panel passes.
# =========================================================================== #
class TestFreshPanelPasses:
    def test_fresh_panel_is_allowed(self, monkeypatch, tmp_path):
        _stub_config(monkeypatch, enabled=True, max_age_days=5)
        manifest = _manifest(tmp_path, (TODAY - dt.timedelta(days=2)).isoformat())
        allowed, _reason = psg.panel_gate_check(manifest, today=TODAY)
        assert allowed is True

    def test_boundary_age_equal_to_limit_is_allowed(self, monkeypatch, tmp_path):
        """max age is inclusive: age == PANEL_MAX_AGE_DAYS still passes; one day
        more blocks."""
        _stub_config(monkeypatch, enabled=True, max_age_days=5)
        manifest = _manifest(tmp_path, (TODAY - dt.timedelta(days=5)).isoformat())
        allowed, _ = psg.panel_gate_check(manifest, today=TODAY)
        assert allowed is True


# =========================================================================== #
# 4. Coverage gate — as_of outside the membership table's coverage blocks,
#    via the same claim authority the simulation uses
#    (has_point_in_time_membership, #1907 Inc 1).
# =========================================================================== #
class TestMembershipCoverageGate:
    def test_as_of_on_removals_only_table_blocks(self, monkeypatch, tmp_path, caplog):
        _stub_config(monkeypatch, enabled=True, max_age_days=5)
        removals_only = tmp_path / "membership.csv"
        removals_only.write_text(
            "symbol,start_date,end_date\nSIVB,2018-03-12,2023-03-13\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(removals_only))
        manifest = _manifest(
            tmp_path, TODAY.isoformat()
        )  # fresh — age is NOT the issue
        with caplog.at_level(logging.WARNING):
            allowed, reason = psg.panel_gate_check(
                manifest, as_of=dt.date(2022, 6, 1), today=TODAY
            )
        assert allowed is False
        assert "coverage" in reason.lower() or "membership" in reason.lower()

    def test_as_of_within_capable_coverage_passes(self, monkeypatch, tmp_path):
        _stub_config(monkeypatch, enabled=True, max_age_days=5)
        capable = tmp_path / "membership.csv"
        capable.write_text(
            "symbol,start_date,end_date\n"
            "AAPL,1982-11-30,\n"
            "SIVB,2018-03-12,2023-03-13\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(dp, "SP500_MEMBERSHIP_CSV", str(capable))
        manifest = _manifest(tmp_path, TODAY.isoformat())
        allowed, _ = psg.panel_gate_check(
            manifest, as_of=dt.date(2022, 6, 1), today=TODAY
        )
        assert allowed is True


# =========================================================================== #
# 5. R10 — gate OFF: never blocks, no extra behaviour (BORA byte-identity).
# =========================================================================== #
class TestGateOffIsInert:
    def test_gate_off_allows_even_a_stale_panel(self, monkeypatch, tmp_path, caplog):
        _stub_config(monkeypatch, enabled=False, max_age_days=5)
        manifest = _manifest(tmp_path, (TODAY - dt.timedelta(days=365)).isoformat())
        with caplog.at_level(logging.WARNING):
            allowed, _ = psg.panel_gate_check(manifest, today=TODAY)
        assert allowed is True
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_gate_off_allows_even_a_missing_manifest(
        self, monkeypatch, tmp_path, caplog
    ):
        """OFF must short-circuit BEFORE any filesystem read — the disabled state
        adds zero behaviour."""
        _stub_config(monkeypatch, enabled=False)
        with caplog.at_level(logging.WARNING):
            allowed, _ = psg.panel_gate_check(tmp_path / "absent.json", today=TODAY)
        assert allowed is True
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# =========================================================================== #
# 6. The helper reads config through get_config() — no os.environ in the
#    finance core (CODING_POLICY §2.10).
# =========================================================================== #
def test_no_direct_environ_read_in_gate_module():
    import inspect

    src = inspect.getsource(psg)
    assert "os.environ" not in src and "os.getenv" not in src, (
        "panel_stale_gate must read config via config.get_config() only — env "
        "reads live in the config modules (CODING_POLICY §2.10)"
    )
