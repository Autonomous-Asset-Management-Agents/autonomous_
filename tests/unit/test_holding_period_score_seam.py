"""#2940 — Naht für die Haltedauer-Kurve: Default byte-identisch, Sweep-fähig.

Die Kurve 30/50/70/90 an den Grenzen 1/3/7 Tagen stammt aus der Zeit vor der
20-Tage-Mindesthaltedauer. Zwei gemessene Folgen: Der Score erreicht sein Maximum an Tag 7,
während die Strategie 20 Tage committet — und eine frische Position bekommt 30 Punkte, was
sie mit 12 Punkten Abstand zum wahrscheinlichsten Verdrängungskandidaten macht (#2935).

Ob eine andere Kurve besser ist, ist eine Messfrage. Diese Suite pinnt deshalb vor allem
EINES: dass der Default die historischen Werte **exakt** reproduziert. Ein Sweep-Knopf, der
das Verhalten schon beim Einbau ändert, wäre wertlos — dann misst man zwei Änderungen.
"""

from __future__ import annotations

import pytest

from core.portfolio_manager import holding_period_score

# Die historischen Literale, aus dem Code vor #2940 abgeschrieben. GOLDEN — Änderungen hier
# nur mit Owner-Entscheid und Registerzeile.
LEGACY = [
    (0.0, 30.0),
    (0.5, 30.0),
    (0.99, 30.0),  # < 1 Tag
    (1.0, 50.0),
    (2.0, 50.0),
    (2.99, 50.0),  # 1-3
    (3.0, 70.0),
    (5.0, 70.0),
    (6.99, 70.0),  # 3-7
    (7.0, 90.0),
    (20.0, 90.0),
    (365.0, 90.0),  # 7+
]


class TestDefaultIsByteIdentical:
    @pytest.mark.parametrize("days,expected", LEGACY)
    def test_legacy_curve_reproduced(self, days, expected):
        assert holding_period_score(days) == expected

    def test_all_four_bands_are_covered(self):
        """ANTI-VAKUITÄT: die GOLDEN-Liste muss jede der vier Stufen prüfen, sonst könnte die
        Kurve in einer unbeobachteten Band still abweichen."""
        assert {v for _d, v in LEGACY} == {30.0, 50.0, 70.0, 90.0}

    def test_config_default_matches_the_literals(self):
        """Der Config-Leser darf nichts anderes liefern als die historischen Werte."""
        from core.portfolio_manager import _holding_period_cfg

        assert _holding_period_cfg() == (30.0, 0.0)

    def test_score_is_used_by_the_position_scorer(self):
        """Die Naht muss auch WIRKLICH die Aufrufstelle speisen — sonst pinnt die Suite eine
        Funktion, die niemand benutzt."""
        import inspect

        from core.portfolio_manager import PortfolioManager

        src = inspect.getsource(PortfolioManager._calculate_position_scores)
        assert "holding_period_score(" in src
        assert (
            "30.0" not in src
        ), "die Literale muessen aus der Aufrufstelle verschwunden sein"


class TestKandidatA:
    """„Unbekannt ist nicht schlecht": frisch = 50 statt 30, Stufen sonst unverändert."""

    def test_only_the_fresh_band_changes(self):
        assert holding_period_score(0.0, fresh=50.0) == 50.0
        assert holding_period_score(1.0, fresh=50.0) == 50.0
        assert holding_period_score(3.0, fresh=50.0) == 70.0
        assert holding_period_score(7.0, fresh=50.0) == 90.0


class TestKandidatC:
    """Reifung über die konfigurierte Haltefrist statt über 7 Tage."""

    def test_linear_ramp_endpoints(self):
        assert holding_period_score(0.0, fresh=50.0, span_days=20.0) == 50.0
        assert holding_period_score(20.0, fresh=50.0, span_days=20.0) == 90.0

    def test_midpoint(self):
        assert holding_period_score(10.0, fresh=50.0, span_days=20.0) == pytest.approx(
            70.0
        )

    def test_saturates_and_never_exceeds_90(self):
        assert holding_period_score(400.0, fresh=50.0, span_days=20.0) == 90.0

    def test_ramp_works_from_the_legacy_fresh_value_too(self):
        """fresh und span sind unabhängig — 30/20 ist eine gültige vierte Kombination."""
        assert holding_period_score(0.0, fresh=30.0, span_days=20.0) == 30.0
        assert holding_period_score(20.0, fresh=30.0, span_days=20.0) == 90.0
        assert holding_period_score(10.0, fresh=30.0, span_days=20.0) == pytest.approx(
            60.0
        )


class TestRobustness:
    @pytest.mark.parametrize("days", [-1.0, None, 0])
    def test_non_positive_or_missing_age_is_fresh(self, days):
        assert holding_period_score(days) == 30.0

    def test_negative_span_falls_back_to_legacy(self):
        assert holding_period_score(5.0, span_days=-3.0) == 70.0


class TestEditionParity:
    def test_both_editions_carry_the_same_defaults(self):
        import importlib.util
        from pathlib import Path

        import config

        ent = (
            float(config.get_config().HOLDING_PERIOD_SCORE_FRESH),
            float(config.get_config().HOLDING_PERIOD_SCORE_SPAN_DAYS),
        )
        oss_path = Path(config.__file__).parent / "config.oss.py"
        spec = importlib.util.spec_from_file_location("config_oss_hp", oss_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        oss = (
            float(mod.HOLDING_PERIOD_SCORE_FRESH),
            float(mod.HOLDING_PERIOD_SCORE_SPAN_DAYS),
        )
        assert ent == oss == (30.0, 0.0), f"enterprise={ent} oss={oss}"
