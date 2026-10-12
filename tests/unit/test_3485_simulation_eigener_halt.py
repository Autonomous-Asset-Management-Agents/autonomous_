"""#3485 (ARC-E2) — Die In-App-Simulation bekommt einen eigenen Halt.

Die Simulation laeuft im Prozess der Engine und baut einen eigenen ``RiskManager``. Bisher
sprach der an fuenf Stellen den **globalen** Kill-Switch an: Ein Verlust in der Simulation hielt
die echte Engine an (und setzte ueber ``_apply_fail_closed`` das echte Konto auf Paper), eine
Erholung in der Simulation **hob einen echten Halt auf**, und ein echter Halt stoppte die
Simulation.

Plan: ``docs/3485-simulation-eigener-halt/implementation_plan.md`` (PR #3496, Option A).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import core.kill_switch as ks_modul
from core.kill_switch import KillSwitch, LokalerHalt
from core.risk_manager import RiskManager

pytestmark = [pytest.mark.iron_dome, pytest.mark.vc4]

START = 100_000.0


class _GlobalerSpion:
    """Steht fuer den globalen Kill-Switch und merkt sich jede Beruehrung."""

    def __init__(self, gehalten=False):
        self.gehalten = gehalten
        self.trips = []
        self.resets = []

    def trip(self, reason, user_id=None, access_token=None, fail_closed=True):
        self.trips.append(reason)
        self.gehalten = True

    def reset(self, user_id=None):
        self.resets.append(user_id)
        self.gehalten = False

    def is_halted(self, user_id=None):
        return self.gehalten


@pytest.fixture
def globaler(monkeypatch):
    spion = _GlobalerSpion()
    monkeypatch.setattr(ks_modul, "kill_switch", spion)
    return spion


def _sim_rm(halt=None):
    clock_mock = MagicMock()
    import datetime

    clock_mock.now.return_value = datetime.datetime(
        2026, 1, 1, tzinfo=datetime.timezone.utc
    )
    rm = RiskManager(
        MagicMock(), START, kill_switch=halt or LokalerHalt(), clock=clock_mock
    )
    rm.session_start_equity = START
    return rm


# ── Szenario 1: Eine Simulation haelt die echte Engine nicht an ──────────────


def test_portfolio_stop_der_simulation_haelt_nur_die_simulation(globaler):
    halt = LokalerHalt()
    rm = _sim_rm(halt)
    rm.update_account_equity(START * 0.90)  # 10 % unter Start > 7 %-Stop

    assert rm.trading_halted is True
    assert halt.is_halted() is True, "Die Simulation haelt sich selbst an."
    assert (
        globaler.trips == [] and globaler.gehalten is False
    ), "Der Portfolio-Stop der Simulation hat die echte Engine angehalten."


def test_tagesverlust_der_simulation_haelt_nur_die_simulation(globaler):
    halt = LokalerHalt()
    rm = _sim_rm(halt)
    rm.portfolio_stop_loss_pct = 0  # nur der Tages-Breaker
    rm.peak_daily_equity = START
    rm.update_account_equity(START - rm.daily_drawdown_limit - 1_000)  # Limit in Dollar

    assert rm.trading_halted is True and halt.is_halted() is True
    assert globaler.trips == []


# ── Szenario 2: Eine Simulation hebt keinen Halt der Engine auf ──────────────


def test_erholung_der_simulation_hebt_den_halt_der_engine_nicht_auf(globaler):
    globaler.gehalten = True  # echter Sicherheits-Trip
    halt = LokalerHalt()
    rm = _sim_rm(halt)
    rm.portfolio_stop_loss_pct = 0
    rm.peak_daily_equity = START
    rm.update_account_equity(START - rm.daily_drawdown_limit - 1_000)
    assert rm.trading_halted is True

    rm.update_account_equity(START)  # volle Erholung, Entsperr-Pfad aktiv

    assert rm.trading_halted is False, "Die Simulation entsperrt sich selbst."
    assert halt.is_halted() is False
    assert (
        globaler.resets == [] and globaler.gehalten is True
    ), "Die Erholung der Simulation hat den Halt der echten Engine aufgehoben."


# ── Szenario 3: Ein Halt der Engine stoppt die Simulation nicht ──────────────


def test_ein_halt_der_engine_stoppt_die_bemessung_der_simulation_nicht(globaler):
    globaler.gehalten = True
    rm = _sim_rm()
    notiz = {}
    rm.calculate_position_size(2.0, 2.0, current_price=100.0, sizing_trace=notiz)
    assert (
        notiz.get("zero_reason") != "trading_halted"
    ), "Der Halt der echten Engine hat die Simulation angehalten."


def test_der_eigene_halt_stoppt_die_bemessung_der_simulation(globaler):
    halt = LokalerHalt()
    halt.trip("Probe")
    rm = _sim_rm(halt)
    notiz = {}
    menge = rm.calculate_position_size(
        2.0, 2.0, current_price=100.0, sizing_trace=notiz
    )
    assert menge == 0.0 and notiz.get("zero_reason") == "trading_halted"


# ── Ohne Parameter: unveraendert der globale Kill-Switch ─────────────────────


def test_ohne_parameter_spricht_der_risk_manager_den_globalen_kill_switch(globaler):
    clock_mock = MagicMock()
    import datetime

    clock_mock.now.return_value = datetime.datetime(
        2026, 1, 1, tzinfo=datetime.timezone.utc
    )
    rm = RiskManager(MagicMock(), START, clock=clock_mock)
    rm.session_start_equity = START
    rm.update_account_equity(START * 0.90)
    assert globaler.trips, "Die Engine muss weiter den globalen Kill-Switch ausloesen."


# ── Vertrag: der lokale Halt hat die Form des echten ─────────────────────────


@pytest.mark.parametrize("methode", ["trip", "reset", "is_halted"])
def test_lokaler_halt_hat_dieselbe_form_wie_der_kill_switch(methode):
    echt = inspect.signature(getattr(KillSwitch, methode)).parameters
    lokal = inspect.signature(getattr(LokalerHalt, methode)).parameters
    assert list(lokal) == list(echt)[: len(lokal)] and len(lokal) == len(echt), (
        f"LokalerHalt.{methode} weicht von KillSwitch.{methode} ab: "
        f"{list(lokal)} gegen {list(echt)}"
    )


def test_lokaler_halt_je_nutzer_und_global():
    halt = LokalerHalt()
    halt.trip("a", user_id="u1")
    assert halt.is_halted("u1") and not halt.is_halted("u2") and not halt.is_halted()
    halt.trip("b")
    assert halt.is_halted("u2")
    halt.reset()
    assert not halt.is_halted() and not halt.is_halted("u1")
    halt.trip("c", user_id="u1")
    halt.reset(user_id="u1")
    assert not halt.is_halted("u1")


# ── Die Simulation reicht ihren eigenen Halt herein ──────────────────────────


def test_der_simulationslauf_baut_den_risk_manager_mit_eigenem_halt():
    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "engine" / "simulation_runner.py"
    ).read_text(encoding="utf-8")
    aufrufe = [
        k
        for k in ast.walk(ast.parse(quelle))
        if isinstance(k, ast.Call) and getattr(k.func, "id", None) == "RiskManager"
    ]
    assert aufrufe, "simulation_runner baut keinen RiskManager mehr?"
    for aufruf in aufrufe:
        halt = {kw.arg: kw.value for kw in aufruf.keywords}.get("kill_switch")
        assert (
            isinstance(halt, ast.Call)
            and getattr(halt.func, "id", None) == "LokalerHalt"
        ), f"simulation_runner.py:{aufruf.lineno} baut den RiskManager ohne eigenen Halt."
