# tests/unit/test_momentum_score_smoothing.py
# TRD-8 T6 (#3004) — MomentumAgent: Abbildung ohne harte Kappung (TDD Red→Green).
#
# `score = clamp(0.5 + momentum_12_1 / 0.60)` erreicht den oberen Anschlag, sobald das
# 12-1-Monats-Momentum +30 % übersteigt. Gemessen über 26.744 geloggte Stimmen tragen
# 13.033 (48,7 %) exakt den Wert 1,00 — rekonstruierte Rohwerte dieser Fälle: Median
# +51,8 %, Maximum +2.879 %. In einem gewichteten Mittelwert (consensus.py:295-306,
# Vergleich gegen die feste Schwelle 0,65 in runner.py:903-915) wirkt ein für viele
# Titel identischer Wert nicht als Votum, sondern als konstanter Aufschlag von +0,1285.
#
# Variante A (implementation_plan.md §4): monotone Stauchung statt harter Kappung,
# hinter MOMENTUM_SCORE_SMOOTH_ENABLED, Default AUS ⇒ byte-identisch.
#
# Akzeptanzkriterien AC-1..AC-7 aus docs/3004-momentum-normierung/implementation_plan.md §5.

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------


def _state(symbol: str = "AAPL") -> dict:
    """Minimaler SymbolEvalState. Die OHLC-Werte sind bewusst flach — die Tests hier
    treffen alle den 12-1M-Primärpfad, der OHLC nicht liest."""
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1_000_000.0,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _arm(monkeypatch, smooth: bool) -> None:
    """Flag über die einzige Config-Naht des Finance-Core setzen (CODING_POLICY §2.10).

    MOMENTUM_FALLBACK_ABSTAIN_ENABLED wird mitgesetzt, weil der Abstain-Pfad denselben
    Namespace liest — ein SimpleNamespace ohne das Feld würde ihn stillschweigend auf
    den Default zurückfallen lassen und den Test vom eigentlichen Prüfgegenstand
    ablenken."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            MOMENTUM_SCORE_SMOOTH_ENABLED=smooth,
            MOMENTUM_FALLBACK_ABSTAIN_ENABLED=True,
        ),
    )


def _history(monkeypatch, momentum: float, n: int = 260):
    """Kursreihe, die genau ``momentum`` als 12-1M-Wert erzeugt.

    Der Agent liest ausschließlich ``closes[-20]`` und ``closes[-252]``
    (agents.py:697-699). Nur diese beiden Stützstellen werden gesetzt; der Rest der
    Reihe ist für das Ergebnis ohne Belang und dient nur der Längenbedingung
    ``len(closes) >= 252``.
    """
    pd = pytest.importorskip("pandas")
    import core.agent_registry as agent_registry

    closes = [100.0] * n
    if n >= 252:
        # Nur bei voller Historie sind beide Stützstellen definiert. Bei kürzeren
        # Reihen (AC-5) greift ohnehin der Enthaltungspfad, bevor gerechnet wird.
        closes[n - 252] = 100.0
        closes[n - 20] = 100.0 * (1.0 + momentum)

    provider = MagicMock()
    provider.get_data.return_value = pd.DataFrame({"Close": closes})
    registry = SimpleNamespace(
        get_active=lambda: SimpleNamespace(data_provider=provider)
    )
    monkeypatch.setattr(agent_registry, "get_global_registry", lambda: registry)
    return provider


async def _score(monkeypatch, momentum: float, smooth: bool) -> float:
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, smooth)
    _history(monkeypatch, momentum)
    return (await MomentumAgent().vote(_state())).score


# ---------------------------------------------------------------------------
# AC-1 — Flag AUS ist byte-identisch (BORA / Rollback-Zusage der CIA)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "momentum, erwartet",
    [
        (0.45, 1.0),  # gekappt: 0.5 + 0.75 = 1.25 → 1.0
        (0.30, 1.0),  # exakt am Anschlag
        (0.15, 0.75),
        (-0.10, 0.3333333333333333),
        (0.0, 0.5),
    ],
)
async def test_ac1_flag_off_is_byte_identical(monkeypatch, momentum, erwartet):
    assert await _score(monkeypatch, momentum, smooth=False) == pytest.approx(
        erwartet, abs=1e-12
    )


# ---------------------------------------------------------------------------
# AC-2 — Flag AN beseitigt die Punktgleichheiten
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac2_flag_on_removes_ties_above_the_old_ceiling(monkeypatch):
    werte = [0.30, 0.60, 1.00, 2.00]
    scores = [await _score(monkeypatch, m, smooth=True) for m in werte]

    assert len(set(scores)) == len(scores), f"Punktgleichheit geblieben: {scores}"
    for s in scores:
        assert 0.0 < s < 1.0, f"Score {s} liegt auf einem Anschlag"
    assert scores == sorted(scores), "Reihenfolge nicht erhalten"


@pytest.mark.anyio
async def test_ac2_flag_off_still_ties_them_all(monkeypatch):
    """Gegenprobe — ohne diesen Test wäre AC-2 nicht falsifizierbar."""
    scores = [
        await _score(monkeypatch, m, smooth=False) for m in (0.30, 0.60, 1.00, 2.00)
    ]
    assert set(scores) == {1.0}


# ---------------------------------------------------------------------------
# AC-3 — Ordnungserhalt über eine Cross-Section
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac3_order_is_preserved_across_a_cross_section(monkeypatch):
    """500 paarweise verschiedene Rohwerte von −50 % bis +300 %.

    Obergrenze bewusst unter dem Punkt, an dem ``tanh`` in float64 auf exakt 1.0
    läuft (m ≈ +1144 %) — siehe AC-7 und die erklärte Grenze im Plan §9.
    """
    roh = [-0.50 + i * (3.50 / 499) for i in range(500)]
    scores = [await _score(monkeypatch, m, smooth=True) for m in roh]

    assert (
        len(set(scores)) == 500
    ), f"{500 - len(set(scores))} Punktgleichheiten in der Cross-Section"
    # Rohwerte sind aufsteigend ⇒ Ordnungserhalt heißt: Scores ebenfalls aufsteigend.
    # Das ist die Rangkorrelation 1.0 ohne scipy-Abhängigkeit.
    assert all(a < b for a, b in zip(scores, scores[1:])), "Reihenfolge verletzt"


# ---------------------------------------------------------------------------
# AC-4 — Neutralpunkt und Wertebereich bleiben
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac4_neutral_point_unchanged(monkeypatch):
    assert await _score(monkeypatch, 0.0, smooth=True) == pytest.approx(0.5, abs=1e-12)


@pytest.mark.anyio
@pytest.mark.parametrize("momentum", [-0.99, -0.50, -0.05, 0.05, 0.50, 5.00, 28.79])
async def test_ac4_range_is_respected(monkeypatch, momentum):
    s = await _score(monkeypatch, momentum, smooth=True)
    assert math.isfinite(s)
    assert 0.0 <= s <= 1.0


# ---------------------------------------------------------------------------
# AC-5 — der Enthaltungspfad bleibt unberührt (#1968)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac5_abstain_path_untouched_with_flag_on(monkeypatch):
    from core.round_table import agents
    from core.round_table.agents import MomentumAgent

    agents._MOMENTUM_ABSTAIN_WARNED.clear()
    _arm(monkeypatch, smooth=True)
    _history(monkeypatch, 0.45, n=30)  # <40 Closes ⇒ Primärpfad nicht verfügbar

    r = await MomentumAgent().vote(_state())
    assert r.weight == 0.0
    assert r.score == 0.5
    assert "EXCLUDED" in r.reasoning
    agents._MOMENTUM_ABSTAIN_WARNED.clear()


# ---------------------------------------------------------------------------
# AC-6 — das Flag ist sweep-fähig, und beide Editionen stimmen überein
# ---------------------------------------------------------------------------
#
# research/sweep.py:70-80 validiert Spec-Werte als ZAHLEN — der Sweep setzt Flags als
# "1"/"0". Das in config.py 102-mal verwendete Muster `.lower() == "true"` läse "1" als
# False; Stufe 2 des Plans würde dann zweimal denselben Zustand messen und ein sauber
# aussehendes Nullergebnis liefern. Daher zwingend das tolerante Muster aus
# config.py:198-200.


def _load_config_fresh(name: str, datei: str):
    spec = importlib.util.spec_from_file_location(name, _AI_BOT / datei)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "roh, erwartet",
    [
        ("1", True),
        ("0", False),
        ("true", True),
        ("True", True),
        ("false", False),
        ("yes", True),
        (" 1 ", True),
    ],
)
@pytest.mark.parametrize("datei", ["config.py", "config.oss.py"])
def test_ac6_flag_parses_numeric_and_textual(monkeypatch, datei, roh, erwartet):
    monkeypatch.setenv("MOMENTUM_SCORE_SMOOTH_ENABLED", roh)
    cfg = _load_config_fresh(
        f"cfg_smooth_{datei.replace('.', '_')}_{roh.strip()}", datei
    )
    assert cfg.get_config().MOMENTUM_SCORE_SMOOTH_ENABLED is erwartet


@pytest.mark.parametrize("datei", ["config.py", "config.oss.py"])
def test_ac6_default_is_on_in_both_editions(monkeypatch, datei):
    """Seit der Aktivierung (Owner-Entscheid 25.08.) ist der Default AN — in BEIDEN
    Editionen identisch (BORA).

    Der Test hat vorher auf ``is False`` bestanden; die Umkehr ist der eigentliche
    Inhalt dieses PRs und steht deshalb hier, nicht im Kommentar. Wer den Default
    zurücknimmt, bricht diesen Test — gewollt.
    """
    monkeypatch.delenv("MOMENTUM_SCORE_SMOOTH_ENABLED", raising=False)
    cfg = _load_config_fresh(f"cfg_smooth_default_{datei.replace('.', '_')}", datei)
    assert cfg.get_config().MOMENTUM_SCORE_SMOOTH_ENABLED is True


@pytest.mark.parametrize("datei", ["config.py", "config.oss.py"])
def test_ac1_old_behaviour_still_reachable(monkeypatch, datei):
    """Der Rückweg bleibt offen: ``=0`` stellt die harte Kappung wieder her.

    Rollback-Zusage der CIA — ohne diesen Test wäre sie unbelegt."""
    monkeypatch.setenv("MOMENTUM_SCORE_SMOOTH_ENABLED", "0")
    cfg = _load_config_fresh(f"cfg_smooth_off_{datei.replace('.', '_')}", datei)
    assert cfg.get_config().MOMENTUM_SCORE_SMOOTH_ENABLED is False


# ---------------------------------------------------------------------------
# AC-7 — Extremwerte kippen nicht
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ac7_extreme_value_stays_finite_and_ordered(monkeypatch):
    """+2.879 % ist der größte rekonstruierte Rohwert aus dem Live-Log (24.08.).

    Er deutet auf eine nicht split-bereinigte Kursreihe (Befund I5, eigenes Thema) —
    der Agent muss ihn trotzdem verarbeiten, ohne zu kippen.
    """
    extrem = await _score(monkeypatch, 28.79, smooth=True)
    normal = await _score(monkeypatch, 2.00, smooth=True)

    assert math.isfinite(extrem)
    assert extrem <= 1.0
    assert extrem > normal
