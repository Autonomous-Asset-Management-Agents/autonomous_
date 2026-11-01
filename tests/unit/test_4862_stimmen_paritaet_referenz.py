"""#4862 Paritaet: Strategie -> --set-Argumente der vollen Sim (Plan TDD-Schritt 2)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.vc1]


def _setzungen(**felder):
    from core.sim.stimmen_paritaet import referenz_setzungen
    from core.sim.stimmen_sim import StimmenSimStrategie

    return referenz_setzungen(StimmenSimStrategie(name="a", **felder))


def test_auslieferung_ergibt_config_setzungen():
    erwartet = (
        "FULL_UNIVERSE_MAX_POSITIONS=20 GATEKEEPER_STRICT_ML_REQUIRES_RL=False "
        "GLOBAL_BUY_COOLDOWN_MINUTES=30 INTRADAY_TIMING_ENABLED=True "
        "MATERIAL_ENTRY_MIN_PCT_OF_TARGET=0.0 MAX_BUYS_PER_HOUR=2"
    ).split()
    assert _setzungen() == erwartet


def test_setzungen_werden_von_settings_gelesen(monkeypatch):
    """Jede Setzung hat die Form, die ``settings.py`` wieder einliest.

    ``RuntimeConfigState`` ist ein pydantic-``BaseSettings``: eine frische Instanz liest
    die Umgebung, der globale Zustand bleibt unberuehrt.
    """
    from settings import RuntimeConfigState

    for kv in _setzungen(max_positions=3, pacing_aktiv=False, material_min_pct=0.5):
        k, _, v = kv.partition("=")
        monkeypatch.setenv(k, v)
    cfg = RuntimeConfigState()

    assert cfg.FULL_UNIVERSE_MAX_POSITIONS == 3
    assert cfg.INTRADAY_TIMING_ENABLED is False
    assert cfg.GLOBAL_BUY_COOLDOWN_MINUTES == 30
    assert cfg.MAX_BUYS_PER_HOUR == 2
    assert cfg.GATEKEEPER_STRICT_ML_REQUIRES_RL is False
    assert cfg.MATERIAL_ENTRY_MIN_PCT_OF_TARGET == 0.5


def test_slots_als_full_universe_max_positions():
    assert "FULL_UNIVERSE_MAX_POSITIONS=3" in _setzungen(max_positions=3)


def test_gewichte_nicht_abbildbar():
    with pytest.raises(ValueError, match="nicht abbildbar.*gewichte"):
        _setzungen(gewichte={"MomentumAgent": 2.0})


@pytest.mark.parametrize("kw", [{"buy_threshold": 0.7}, {"sell_threshold": 0.2}])
def test_schwellen_nicht_abbildbar(kw):
    with pytest.raises(ValueError, match="nicht abbildbar"):
        _setzungen(**kw)


def test_kosten_nicht_abbildbar():
    with pytest.raises(ValueError, match="nicht abbildbar.*kosten_bp"):
        _setzungen(kosten_bp=5.0)


@pytest.mark.parametrize(
    "feld,wert",
    [
        ("startkapital", 50_000.0),  # VirtualLiveBroker: fest 100 000 (broker.py)
        ("max_drawdown_limit", 0.10),  # risk_manager.py: Portfolio-Stop fest 7 %
        ("buy_cooldown_minuten", 12.5),  # settings.py liest int()
    ],
)
def test_weitere_felder_nicht_abbildbar(feld, wert):
    with pytest.raises(ValueError, match=f"nicht abbildbar.*{feld}"):
        _setzungen(**{feld: wert})
