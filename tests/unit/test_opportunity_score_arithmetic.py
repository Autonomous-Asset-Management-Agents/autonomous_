"""#2947 — zwei Rechenfehler in der Kandidaten-Bewertung.

**Fehler 1: die Sicherheitsmarge hebt sich selbst auf.** Die vier Komponenten von
`score_opportunity` sind mit 0,25 / 0,20 / 0,20 / 0,25 gewichtet — Summe **0,90** — und darauf
kommt eine additive Pauschale von 15 Punkten fuer „RL sagt BUY". Die Verdraengungsschwelle betraegt
ebenfalls 15, also kuerzt sich beides:

    (Basis + 15) − Position > 15   ⟺   Basis > Position

Aus „der Kandidat muss deutlich besser sein" wurde „der Kandidat muss ueberhaupt besser sein".
Verschaerft dadurch, dass `rl_action=1` an beiden Live-Aufrufstellen fest eingetragen ist
(`order_executor.py:1094`, `:2385`) — die Pauschale wird also immer vergeben. Und der Agent, den
sie vertritt, steht im Round Table auf `RL_CONFIDENCE_WEIGHT = 0.0`, ist also bewusst stillgelegt.

**Fehler 2: eine Komponente rechnet mit der falschen Einheit.** `conf_normalized` ist
`50 + model_confidence * 10`, und der Kommentar im Quelltext nennt die Absicht: *„Confidence
typically ranges from -5 to +5"* — gedacht war eine Spreizung von ±50 Punkten. Geliefert wird die
LSTM-Vorhersage, die im Bereich ±1 liegt (Schwellen im Code bei ±0,6). Tatsaechliche Spreizung:
±10. Ueber die gesamte plausible Bandbreite bewegt sich der Kandidatenscore dadurch um 2,5 Punkte,
bei 25 % Gewicht.

Beide Behebungen stellen **beabsichtigtes** Verhalten wieder her. Sie wirken gegeneinander — die
eine senkt alle Kandidatenscores, die andere spreizt sie — und werden deshalb zusammen geschaltet
und zusammen gemessen. Rueckfall ueber `OPPORTUNITY_SCORE_NORMALIZED=false`, dann gilt die alte
Arithmetik byte-identisch.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.portfolio_manager import PortfolioManager

# Der Gesamtscore einer frischen, flachen Position: momentum 50·0,35 + haltedauer 30·0,20
# + risikoadjustiert 50·0,25 + conviction 50·0,20. Gegenstueck jeder Verdraengungsrechnung.
FRESH_POSITION_SCORE = 46.0

# Die Verdraengungsschwelle aus debate_position_swap (Zweig „Strong upgrade").
SWAP_THRESHOLD = 15.0

NEUTRAL = {"rsi_14": 50.0, "adx_14": 20.0, "macd": 0.0}


def _pm() -> PortfolioManager:
    client = MagicMock()
    client.get_account.return_value = MagicMock(equity="100000")
    client.get_all_positions.return_value = []
    return PortfolioManager(
        client, total_capital=100000.0, max_positions=10, user_id="t"
    )


@pytest.fixture
def armed(monkeypatch):
    """Betriebszustand: Behebung aktiv, RL im Round Table stillgelegt (beide Editionen: 0.0)."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(cfg, "OPPORTUNITY_SCORE_NORMALIZED", True, raising=False)
    monkeypatch.setattr(cfg, "RL_CONFIDENCE_WEIGHT", 0.0, raising=False)


@pytest.fixture
def legacy(monkeypatch):
    """Rueckfall: alte Arithmetik."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(cfg, "OPPORTUNITY_SCORE_NORMALIZED", False, raising=False)
    monkeypatch.setattr(cfg, "RL_CONFIDENCE_WEIGHT", 0.0, raising=False)


def _score(pm, *, rl_action=1, confidence=0.3, features=None):
    return pm.score_opportunity(
        "X",
        100.0,
        rl_action=rl_action,
        model_confidence=confidence,
        features=dict(features if features is not None else NEUTRAL),
    ).total_score


class TestFehler1Gewichtssumme:
    def test_weights_sum_to_one(self, armed):
        """value 50 · trend 50 · momentum 70 · confidence 50.

        Gewichtete Summe = 49,0. Bei Gewichtssumme 1,00 sind das 49,0/0,90 = **54,44**;
        die alte Rechnung liefert 49,0 (plus die Pauschale). Der Unterschied ist der ganze Punkt.
        """
        pm = _pm()
        got = pm.score_opportunity(
            "X",
            100.0,
            rl_action=0,
            model_confidence=0.0,
            features={"rsi_14": 50.0, "adx_14": 20.0, "macd": 2.0},
        ).total_score
        assert got == pytest.approx(
            54.44, abs=0.02
        ), "erwartet wurde die auf 1,00 normierte Summe; 49,0 waere die alte 0,90-Rechnung"

    def test_muted_agent_contributes_no_bonus(self, armed):
        """Kernaussage: bei RL_CONFIDENCE_WEIGHT = 0 darf die Aktion den Score nicht bewegen."""
        pm = _pm()
        assert _score(pm, rl_action=1) == pytest.approx(_score(pm, rl_action=0))

    def test_revived_agent_contributes_proportionally(self, monkeypatch):
        """Wird der Agent je reaktiviert, kehrt der Bonus proportional zurueck — ohne
        Code-Aenderung. Bei seinem historischen Gewicht 0,40 ist es der volle Betrag."""
        import config

        cfg = config.get_config()
        monkeypatch.setattr(cfg, "OPPORTUNITY_SCORE_NORMALIZED", True, raising=False)
        pm = _pm()

        monkeypatch.setattr(cfg, "RL_CONFIDENCE_WEIGHT", 0.40, raising=False)
        full = _score(pm, rl_action=1) - _score(pm, rl_action=0)
        monkeypatch.setattr(cfg, "RL_CONFIDENCE_WEIGHT", 0.20, raising=False)
        half = _score(pm, rl_action=1) - _score(pm, rl_action=0)

        assert full == pytest.approx(15.0), "volles Gewicht ⇒ historischer Betrag"
        assert half == pytest.approx(7.5), "halbes Gewicht ⇒ halber Betrag"

    def test_single_weak_indicator_no_longer_clears_the_hurdle(self, armed):
        """Der Live-Befund: heute genuegt ein einzelner gerade ueberschrittener Indikator.
        Nach der Behebung muss jeder davon unter der Schwelle bleiben."""
        pm = _pm()
        for name, feats in {
            "MACD gerade positiv": {"rsi_14": 50.0, "adx_14": 20.0, "macd": 0.5},
            "ADX gerade ueber 25": {"rsi_14": 50.0, "adx_14": 26.0, "macd": 0.0},
            "RSI gerade unter 40": {"rsi_14": 38.0, "adx_14": 20.0, "macd": 0.0},
        }.items():
            diff = _score(pm, features=feats) - FRESH_POSITION_SCORE
            assert (
                diff <= SWAP_THRESHOLD
            ), f"{name}: Differenz {diff:.2f} verdraengt weiterhin"

    def test_a_genuinely_strong_candidate_still_clears_the_hurdle(self, armed):
        """Gegenprobe — die Behebung darf die Verdraengung nicht abschaffen, nur verteuern."""
        pm = _pm()
        strong = {"rsi_14": 25.0, "adx_14": 35.0, "macd": 4.0}
        diff = _score(pm, confidence=0.9, features=strong) - FRESH_POSITION_SCORE
        assert (
            diff > SWAP_THRESHOLD
        ), f"starker Kandidat blieb unter der Schwelle ({diff:.2f})"


class TestFehler2ConfidenceSkala:
    def test_confidence_spans_its_intended_range(self, armed):
        """Die Komponente hat 25 % Gewicht. Ueber den Eingangsbereich ±1 muss der Gesamtscore
        um 27,78 Punkte wandern (±50 Punkte in der Komponente), nicht um 5,0."""
        pm = _pm()
        low = _score(pm, rl_action=0, confidence=-1.0)
        high = _score(pm, rl_action=0, confidence=1.0)
        # Komponente 0…100 (50 ± 50) bei Gewicht 0,25/0,90 = 0,2778 ⇒ 27,78 Punkte Gesamtscore.
        assert (high - low) == pytest.approx(
            27.78, abs=0.05
        ), f"Spreizung {high - low:.2f} — 5,0 waere der alte Faktor 10"

    def test_zero_prediction_is_neutral(self, armed):
        pm = _pm()
        mid = _score(pm, rl_action=0, confidence=0.0)
        assert mid == pytest.approx(
            (
                _score(pm, rl_action=0, confidence=-1.0)
                + _score(pm, rl_action=0, confidence=1.0)
            )
            / 2
        )

    @pytest.mark.parametrize("conf", [-50.0, 50.0])
    def test_extreme_inputs_stay_within_bounds(self, armed, conf):
        """Der Clamp muss halten, auch wenn eine andere Quelle je eine andere Skala liefert."""
        pm = _pm()
        assert 0.0 <= _score(pm, rl_action=0, confidence=conf) <= 100.0


class TestRueckfall:
    def test_flag_off_reproduces_the_old_arithmetic(self, legacy):
        """Byte-identisch: Gewichtssumme 0,90, additive 15, Confidence-Faktor 10.
        Neutraler Fall mit LSTM 0,3 ⇒ 43,75 + 15 = 58,75 (der gemessene Live-Wert)."""
        pm = _pm()
        assert _score(pm, rl_action=1, confidence=0.3) == pytest.approx(58.75, abs=0.01)

    def test_flag_off_keeps_the_unconditional_bonus(self, legacy):
        pm = _pm()
        delta = _score(pm, rl_action=1) - _score(pm, rl_action=0)
        assert delta == pytest.approx(
            15.0
        ), "im Rueckfall bleibt die Pauschale unbedingt"

    def test_flag_off_keeps_the_narrow_confidence_spread(self, legacy):
        pm = _pm()
        spread = _score(pm, rl_action=0, confidence=1.0) - _score(
            pm, rl_action=0, confidence=-1.0
        )
        assert spread == pytest.approx(5.0, abs=0.01)


class TestEditionsParitaet:
    def test_both_editions_default_to_armed(self):
        import importlib.util
        from pathlib import Path

        import config

        ent = bool(config.get_config().OPPORTUNITY_SCORE_NORMALIZED)
        oss_path = Path(config.__file__).parent / "config.oss.py"
        spec = importlib.util.spec_from_file_location("config_oss_osn", oss_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        oss = bool(mod.OPPORTUNITY_SCORE_NORMALIZED)
        assert ent is oss is True, f"enterprise={ent} oss={oss}"
