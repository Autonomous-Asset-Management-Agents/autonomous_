"""#3698 — Auslieferungs-Defaults an Erkenntnisse und Literatur angepasst (Plan #3699 + Nachtrag #3798).

Pinnt die neuen Defaults an zwei Orten (Registry und ``settings.py``-Quelle), prüft, dass kein Agent sein
neues Default-Gewicht still auf ``min_weight`` hochklemmt, und dass die Verlust-Stufen geordnet bleiben.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.vc0]

from core.trading_settings import REGISTRY  # noqa: E402

_ROOT = Path(__file__).resolve().parents[2]
_SETTINGS = (_ROOT / "settings.py").read_text(encoding="utf-8")

NEUE_DEFAULTS = {
    "LSTM_SIGNAL_WEIGHT": 0.15,
    "SPECIALIST_ALPHA_WEIGHT": 0.15,
    "NEWS_SENTIMENT_WEIGHT": 0.20,
    "FUNDAMENTALS_AGENT_WEIGHT": 0.25,
    "VALUATION_AGENT_WEIGHT": 0.0,
    "SIGNAL_BUY_THRESHOLD": 0.73,
    "ROUND_TABLE_TOP_K_EVAL": 60,
    "REGIME_THROTTLE_ENABLED": True,
    "LOSS_WATCH_PCT": -3.0,
    "LOSS_CUT_PCT": -6.0,
    "LOSS_ESCALATION_PCT": -7.9,
}


def _env_default(key: str) -> str:
    m = re.search(rf'os\.getenv\(\s*"{key}"\s*,\s*"([^"]*)"', _SETTINGS)
    assert m, f"{key}: keine os.getenv-Naht in settings.py"
    return m.group(1)


def _gleich(default, quelle: str) -> bool:
    if isinstance(default, bool):
        return quelle.strip().lower() == ("true" if default else "false")
    return float(quelle) == pytest.approx(float(default))


class TestNeueDefaults:
    @pytest.mark.parametrize("key,wert", sorted(NEUE_DEFAULTS.items()))
    def test_registry_default(self, key, wert):
        assert key in REGISTRY, f"{key} fehlt in der Registry"
        if isinstance(wert, bool):
            assert REGISTRY[key].default is wert
        else:
            assert float(REGISTRY[key].default) == pytest.approx(float(wert))

    @pytest.mark.parametrize("key,wert", sorted(NEUE_DEFAULTS.items()))
    def test_settings_quelle_default(self, key, wert):
        assert _gleich(wert, _env_default(key)), f"{key}: settings.py-Default weicht ab"

    def test_full_universe_max_positions_20(self):
        assert _env_default("FULL_UNIVERSE_MAX_POSITIONS") == "20"

    def test_vix_risk_weight_bleibt_045(self):
        """#3798: VIX_RISK_WEIGHT ist aus dem Umfang genommen (Klassen-Untergrenze 0.10)."""
        assert float(REGISTRY["VIX_RISK_WEIGHT"].default) == pytest.approx(0.45)
        assert float(REGISTRY["VIX_RISK_WEIGHT"].lo) == pytest.approx(0.10)

    def test_loss_stufen_geordnet(self):
        w = REGISTRY["LOSS_WATCH_PCT"].default
        c = REGISTRY["LOSS_CUT_PCT"].default
        e = REGISTRY["LOSS_ESCALATION_PCT"].default
        assert w > c > e > -8.0


# Klassen der Gewichts-Regler: neuer Default darf nicht unter der Klassen-Untergrenze liegen,
# sonst klemmt base_agent.weight still hoch (Audit 30.09.2026, #3798).
_GEWICHT_KLASSEN = {
    "LSTM_SIGNAL_WEIGHT": "LSTMSignalAgent",
    "SPECIALIST_ALPHA_WEIGHT": "SpecialistAlphaAgent",
    "NEWS_SENTIMENT_WEIGHT": "NewsSentimentAgent",
    "FUNDAMENTALS_AGENT_WEIGHT": "FundamentalsAgent",
    "VALUATION_AGENT_WEIGHT": "ValuationAgent",
}


@pytest.mark.parametrize("key,klasse", sorted(_GEWICHT_KLASSEN.items()))
def test_instanziiertes_gewicht_gleich_default(key, klasse):
    from core.round_table import agents

    cls = getattr(agents, klasse)
    default = float(NEUE_DEFAULTS[key])
    assert (
        cls.min_weight <= default
    ), f"{klasse}.min_weight={cls.min_weight} > neuer Default {default} — stille Klemme"
    lo = max(
        cls.min_weight, min(cls.max_weight if cls.max_weight > 0 else default, default)
    )
    assert lo == pytest.approx(default)
