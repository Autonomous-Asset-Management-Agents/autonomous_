"""Haltefrist und Churn-Sperren muessen im Sim die VIRTUELLE Zeit messen.

Defekt (gemessen 30.08.2026): ``record_trade()`` und die ``days_held``-Berechnung
lasen beide ``datetime.now(timezone.utc)`` — die Wanduhr. Ein Sim-Lauf dauert
Minuten, also blieb ``days_held`` dauerhaft 0 und die 20-Tage-Mindesthaltedauer
lief NIE ab. Jede im Lauf gekaufte Position wurde zum permanenten
Verdraengungs-Blocker; das Buch fror nach den ersten Kaeufen ein.

Belegt im 33-Tage-Replay: 10 Fills statt real 188, davon 0 SELL, und 22.095 von
23.769 Blockierungen mit "minimum holding period not met (0/20 days)".
"""

import datetime as dt
from types import SimpleNamespace

import pytest

import config
import core.portfolio_manager as pm


class _Clock:
    def __init__(self, when: dt.datetime):
        self.current_time = when


@pytest.fixture
def sim_on(monkeypatch):
    """SIM_MODE an, mit steuerbarer virtueller Uhr."""
    clock = _Clock(dt.datetime(2026, 7, 13, 13, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(SIM_MODE=True))
    import sys

    monkeypatch.setitem(
        sys.modules, "core.sim.clock", SimpleNamespace(get_sim_clock=lambda: clock)
    )
    return clock


class TestProduktionsGarantien:
    """Was in Produktion gelten MUSS — als Test erzwungen, nicht als Zusicherung.

    Der Fix taucht in Produktionscode auf (record_trade, can_trade, Cooldown,
    days_held laufen live in jedem Zyklus). Verhaltensgleichheit allein reicht
    als Begruendung nicht; sie muss nachpruefbar sein.
    """

    def test_prod_importiert_die_sim_uhr_NIE(self, monkeypatch):
        """Haerteste Garantie: ohne SIM_MODE wird Sim-Code nicht einmal geladen.

        Damit kann kein Defekt in core.sim.clock den Live-Pfad erreichen —
        unabhaengig davon, was dort steht.
        """
        import sys

        monkeypatch.setattr(
            config, "get_config", lambda: SimpleNamespace(SIM_MODE=False)
        )
        monkeypatch.delitem(sys.modules, "core.sim.clock", raising=False)
        pm._now_utc()
        assert (
            "core.sim.clock" not in sys.modules
        ), "der Live-Pfad darf die Sim-Uhr nicht einmal importieren"

    @pytest.mark.parametrize(
        "broken",
        [
            pytest.param(
                lambda: (_ for _ in ()).throw(RuntimeError("kaputt")), id="config-wirft"
            ),
            pytest.param(lambda: SimpleNamespace(), id="SIM_MODE-fehlt"),
            pytest.param(lambda: None, id="config-ist-None"),
            pytest.param(lambda: SimpleNamespace(SIM_MODE=None), id="SIM_MODE-None"),
            pytest.param(
                lambda: SimpleNamespace(SIM_MODE="vielleicht"), id="SIM_MODE-Unsinn"
            ),
            pytest.param(lambda: SimpleNamespace(SIM_MODE=0), id="SIM_MODE-null"),
        ],
    )
    def test_wirft_niemals_und_liefert_die_wanduhr(self, monkeypatch, broken):
        """Kein kaputter Config-Zustand darf eine Ausnahme in den Live-Pfad lassen.

        Genau diese Klasse Fehler hat sich beim Bauen materialisiert: ein
        NameError im Fehlerzweig waere in Produktion geknallt.
        """
        monkeypatch.setattr(config, "get_config", broken)
        got = pm._now_utc()  # darf unter keinen Umstaenden werfen
        assert abs((got - dt.datetime.now(dt.timezone.utc)).total_seconds()) < 5

    def test_ergebnis_ist_zeitzonen_bewusst_wie_vorher(self, monkeypatch):
        """Die Aufrufstellen rechnen mit aware-UTC — ein naives datetime braeche sie."""
        monkeypatch.setattr(
            config, "get_config", lambda: SimpleNamespace(SIM_MODE=False)
        )
        got = pm._now_utc()
        assert got.tzinfo is not None
        assert got.utcoffset() == dt.timedelta(0)

    def test_sim_uhr_die_None_liefert_faellt_auf_die_wanduhr(self, monkeypatch):
        """Auch IM Sim: eine Uhr ohne Zeit darf nicht None durchreichen."""
        import sys

        monkeypatch.setattr(
            config, "get_config", lambda: SimpleNamespace(SIM_MODE=True)
        )
        monkeypatch.setitem(
            sys.modules,
            "core.sim.clock",
            SimpleNamespace(get_sim_clock=lambda: SimpleNamespace(current_time=None)),
        )
        got = pm._now_utc()
        assert got is not None
        assert abs((got - dt.datetime.now(dt.timezone.utc)).total_seconds()) < 5


def test_wall_clock_when_sim_mode_is_off(monkeypatch):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(SIM_MODE=False))
    delta = abs((pm._now_utc() - dt.datetime.now(dt.timezone.utc)).total_seconds())
    assert delta < 5, "ohne SIM_MODE muss die echte Uhr gelten — Live-Pfad unveraendert"


def test_virtual_clock_when_sim_mode_is_on(sim_on):
    assert pm._now_utc() == sim_on.current_time


def test_fail_safe_to_wall_clock_when_sim_clock_unreadable(monkeypatch):
    """Fail-safe: eine unlesbare Sim-Uhr darf den Lauf nie brechen."""
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(SIM_MODE=True))
    import sys

    def _boom():
        raise RuntimeError("keine Sim-Uhr")

    monkeypatch.setitem(
        sys.modules, "core.sim.clock", SimpleNamespace(get_sim_clock=_boom)
    )
    delta = abs((pm._now_utc() - dt.datetime.now(dt.timezone.utc)).total_seconds())
    assert delta < 5


def test_record_trade_stores_the_virtual_timestamp(sim_on):
    m = pm.PortfolioManager.__new__(pm.PortfolioManager)
    m._trade_history = {}
    m._last_rebalance = {}
    m._position_scores = {}
    import threading

    m._history_lock = threading.Lock()
    m.record_trade("AAPL", "BUY")
    assert m._trade_history["AAPL"] == [sim_on.current_time]


def test_holding_period_elapses_over_virtual_days(sim_on):
    """DER REGRESSIONSTEST: nach 25 virtuellen Tagen sind es 25, nicht 0."""
    m = pm.PortfolioManager.__new__(pm.PortfolioManager)
    m._trade_history = {}
    m._last_rebalance = {}
    m._position_scores = {}
    import threading

    m._history_lock = threading.Lock()
    m.record_trade("AAPL", "BUY")

    sim_on.current_time += dt.timedelta(days=25)  # 25 virtuelle Handelstage weiter
    first_buy = min(m._trade_history["AAPL"])
    days_held = (pm._now_utc() - first_buy).days
    assert days_held == 25, (
        "die Haltefrist muss mit der virtuellen Zeit altern — bei der Wanduhr "
        "bliebe sie 0 und die 20-Tage-Sperre liefe nie ab"
    )
