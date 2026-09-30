"""#3662 — Panik-Schutz des Intelligent Exit per Schalter ein-/ausschaltbar.

Plan: docs/3662-panic-protection-switch/implementation_plan.md (Option A: Schalter
``PANIC_PROTECTION_ENABLED`` + Regler ``PANIC_PROTECTION_HOURS``, beides zur Aufrufzeit
gelesen wie die Verlust-Stufen ``_loss_tiers``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.vc4]

from core import intelligent_exit as ie  # noqa: E402
from core.intelligent_exit import PositionContext, analyze_exit  # noqa: E402
from core.trading_settings import REGISTRY  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]


def _cfg(**over):
    base = {
        "LOSS_WATCH_PCT": -2.0,
        "LOSS_CUT_PCT": -4.0,
        "LOSS_ESCALATION_PCT": -6.0,
        "HARD_STOP_LOSS_PCT": -8.0,
        "PANIC_PROTECTION_ENABLED": True,
        "PANIC_PROTECTION_HOURS": 2.0,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(pnl_pct: float, hours: float) -> PositionContext:
    entry = 100.0
    return PositionContext(
        symbol="TEST",
        entry_price=entry,
        current_price=entry * (1 + pnl_pct / 100.0),
        high_water_mark=entry,
        hours_held=hours,
        rsi=50.0,
    )


def _registry(key):
    assert key in REGISTRY, f"{key} fehlt in der Registry"
    return REGISTRY[key]


class TestRegistryDefaults:
    def test_enabled_default_an(self):
        s = _registry("PANIC_PROTECTION_ENABLED")
        assert s.kind == "bool" and s.default is True

    def test_hours_default_2_0(self):
        assert _registry("PANIC_PROTECTION_HOURS").default == 2.0

    def test_hours_bounds_0_5_bis_4_0(self):
        s = _registry("PANIC_PROTECTION_HOURS")
        assert (s.lo, s.hi) == (0.5, 4.0)

    def test_hours_step_0_5(self):
        assert _registry("PANIC_PROTECTION_HOURS").decimals == 1


class TestAnalyzeExitCallTime:
    def test_aus_loss_cut_nach_30_min(self):
        with patch.object(
            ie,
            "_runtime_config",
            return_value=_cfg(PANIC_PROTECTION_ENABLED=False, LOSS_ESCALATION_PCT=-1.0),
        ):
            a = analyze_exit(_ctx(-1.2, hours=0.5))
        assert a.should_sell is True
        assert "LOSS CUT" in a.reason

    def test_an_enthaltung_mit_fenster_im_grund(self):
        with patch.object(ie, "_runtime_config", return_value=_cfg()):
            a = analyze_exit(_ctx(-5.0, hours=0.5))
        assert a.should_sell is False
        assert "Panic protection" in a.reason and "2.0h" in a.reason

    def test_hard_stop_im_fenster_aktiv(self):
        with patch.object(ie, "_runtime_config", return_value=_cfg()):
            a = analyze_exit(_ctx(-8.5, hours=0.5))
        assert a.should_sell is True and a.reason.startswith("HARD STOP")

    def test_fenster_0_5h_greift_nach_45_min(self):
        with patch.object(
            ie,
            "_runtime_config",
            return_value=_cfg(PANIC_PROTECTION_HOURS=0.5, LOSS_ESCALATION_PCT=-1.0),
        ):
            a = analyze_exit(_ctx(-1.2, hours=0.75))
        assert a.should_sell is True

    def test_werte_je_aufruf_gelesen_ohne_reload(self):
        with patch.object(ie, "_runtime_config", return_value=_cfg()):
            first = analyze_exit(_ctx(-5.0, hours=0.5))
        with patch.object(
            ie, "_runtime_config", return_value=_cfg(PANIC_PROTECTION_ENABLED=False)
        ):
            second = analyze_exit(_ctx(-6.5, hours=0.5))
        assert first.should_sell is False and second.should_sell is True

    def test_unlesbare_config_faellt_auf_auslieferung_zurueck(self, caplog):
        with patch.object(ie, "_runtime_config", side_effect=RuntimeError("boom")):
            a = analyze_exit(_ctx(-5.0, hours=0.5))
        assert a.should_sell is False and "Panic protection" in a.reason
        assert any(r.levelname == "WARNING" for r in caplog.records)


class TestNoImportTimeConstant:
    def test_analyze_exit_liest_keine_modulkonstante(self):
        src = (_ROOT / "core" / "intelligent_exit.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "analyze_exit"
        )
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "PANIC_PROTECTION_HOURS" not in names


class TestSettingsChain:
    def test_settings_env_naht_enabled(self):
        src = (_ROOT / "settings.py").read_text(encoding="utf-8")
        assert re.search(r'os\.getenv\("PANIC_PROTECTION_ENABLED"', src)

    def test_settings_env_naht_hours(self):
        src = (_ROOT / "settings.py").read_text(encoding="utf-8")
        assert re.search(r'os\.getenv\("PANIC_PROTECTION_HOURS"', src)
