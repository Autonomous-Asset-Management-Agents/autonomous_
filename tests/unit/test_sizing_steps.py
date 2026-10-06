"""#3829 (ARC-E6 G-5) — ``calculate_position_size`` als Folge benannter Deckel-Schritte.

Plan: ``docs/3829-*/implementation_plan.md`` §6 Schritte 2 und 3.

* **Größen** werden direkt gegen ``regeln.pruefe_groessen`` geprüft, nicht über
  ``test_groessen_gegen_den_code``: dort steht die Regel auf ``warnen``
  (``vertrag.toml``) und meldet nur eine Warnung (wie #3819).
* **Je Deckel ein Test** auf den neuen Schritt — seine Eingaben, die Stückzahl, die er
  zurückgibt, und was er in die Deckel-Spur schreibt. Dass der Ablauf als Ganzes
  unverändert ist, halten die Golden-Fälle in ``test_sizing_zero_reason.py`` fest.
* **Der Name im Befund ist der Name im Code:** jeder ``binding_limit``-Wert wird genau
  in ``RiskManager._step_<wert>`` geschrieben.
"""

from __future__ import annotations

import ast
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.risk_manager import RiskManager
from tests.architecture import regeln
from tests.unit.test_sizing_zero_reason import DECKEL

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_AI_BOT = Path(__file__).resolve().parents[2]
_RISK = "core/risk_manager.py"


class _Uhr:
    def now(self):
        return datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)

    def time(self):
        return 1735732800.0


def _rm(capital: float = 100_000.0, held=None) -> RiskManager:
    client = MagicMock()
    if held == "raise":
        client.get_all_positions.side_effect = RuntimeError("broker down")
    else:
        client.get_all_positions.return_value = (
            [] if held is None else [{"market_value": held}]
        )
    return RiskManager(client=client, total_capital=capital, clock=_Uhr())


def _spur():
    spur: dict = {}
    return spur, spur.__setitem__


# ── Größen ────────────────────────────────────────────────────────────────────


def test_die_groessen_der_bemessung_stimmen_mit_dem_vertrag():
    """Datei- und Funktionszahl sind gemessen eingetragen — in beide Richtungen.

    Geprüft wird nur, was dieser Umbau bewegt: die Dateizahl von ``risk_manager.py`` und
    die Bemessung samt ihren Schritten. ``evaluate_new_trade`` und
    ``update_account_equity`` sind nicht Gegenstand von G-5.
    """
    meldungen = regeln.pruefe_groessen(_AI_BOT, regeln.lade_vertrag())
    eigene = [
        m
        for m in meldungen
        if m.startswith(f"Groessen: {_RISK} ")
        or "calculate_position_size" in m
        or "RiskManager._step_" in m
        or "RiskManager._sizing_" in m
    ]
    assert not eigene, "\n".join(eigene)


def test_jeder_schritt_liegt_unter_der_funktionsschwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["funktion_schwelle"]
    gemessen = {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(_AI_BOT, "core")
        if b.datei == _RISK
    }
    schritte = {
        name.split(".")[-1]: n
        for name, n in gemessen.items()
        if name.startswith(("RiskManager._step_", "RiskManager._sizing_"))
        or name == "RiskManager.calculate_position_size"
    }
    assert {f"_step_{d}" for d in DECKEL} <= set(schritte), sorted(schritte)
    zu_lang = {name: n for name, n in schritte.items() if n > schwelle}
    assert not zu_lang, f"Schritte über {schwelle} Zeilen: {zu_lang}"


# ── Der Name im Befund ist der Name im Code ──────────────────────────────────


def _binding_limit_schreiber() -> dict[str, set[str]]:
    """Je ``binding_limit``-Wert: die Methoden von ``RiskManager``, die ihn schreiben."""
    baum = ast.parse((_AI_BOT / _RISK).read_text(encoding="utf-8"))
    klasse = next(
        k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == "RiskManager"
    )
    gefunden: dict[str, set[str]] = {}
    for methode in klasse.body:
        if not isinstance(methode, ast.FunctionDef):
            continue
        for knoten in ast.walk(methode):
            if (
                isinstance(knoten, ast.Call)
                and len(knoten.args) == 2
                and all(isinstance(a, ast.Constant) for a in knoten.args)
                and knoten.args[0].value == "binding_limit"
            ):
                gefunden.setdefault(knoten.args[1].value, set()).add(methode.name)
    return gefunden


def test_jeder_deckel_wird_in_seinem_benannten_schritt_gemeldet():
    schreiber = _binding_limit_schreiber()
    assert set(schreiber) == set(DECKEL), schreiber
    for deckel, methoden in schreiber.items():
        assert methoden == {f"_step_{deckel}"}, (deckel, methoden)


# ── Je Deckel ein Schritt ─────────────────────────────────────────────────────


class TestSizingDeckelSchritte:
    def test_position_cap_kappt_auf_den_anteil_am_depot(self):
        spur, note = _spur()
        with patch("config.MAX_POSITION_PERCENT", 0.25):
            menge, gekappt = _rm()._step_position_cap(300.0, 100.0, note)
        assert (menge, gekappt) == (250.0, True)
        assert spur == {
            "binding_limit": "position_cap",
            "binding_cap_value": "25000.00",
        }

    def test_position_cap_laesst_eine_kleine_menge_stehen(self):
        spur, note = _spur()
        with patch("config.MAX_POSITION_PERCENT", 0.25):
            assert _rm()._step_position_cap(100.0, 100.0, note) == (100.0, False)
        assert spur == {}

    def test_cash_kappt_auf_den_freien_slot(self):
        spur, note = _spur()
        with patch("config.RISK_CASH_BUFFER_USD", 1.0), patch(
            "config.RISK_FORBID_LEVERAGE", True
        ):
            menge, gekappt = _rm()._step_cash(
                300.0, 100.0, 10_001.0, 1, "off", None, 0.30, note
            )
        assert (menge, gekappt) == (100.0, True)
        assert spur == {"binding_limit": "cash", "binding_cap_value": "10000.00"}

    def test_cash_ohne_geld_nennt_den_grund(self):
        spur, note = _spur()
        with patch("config.RISK_CASH_BUFFER_USD", 1.0), patch(
            "config.RISK_FORBID_LEVERAGE", True
        ):
            menge, gekappt = _rm()._step_cash(
                300.0, 100.0, 0.0, 1, "off", None, 0.30, note
            )
        assert (menge, gekappt) == (0, True)
        assert spur["zero_reason"] == "insufficient_cash"

    def test_total_exposure_cap_kappt_auf_den_spielraum(self):
        spur, note = _spur()
        with patch("config.MAX_TOTAL_EXPOSURE_PCT", 0.95):
            menge = _rm(held=94_000.0)._step_total_exposure_cap(100.0, 100.0, note)
        assert menge == 10.0
        assert spur == {
            "binding_limit": "total_exposure_cap",
            "binding_cap_value": "1000.00",
        }

    def test_total_exposure_cap_schliesst_bei_brokerfehler(self):
        spur, note = _spur()
        with patch("config.MAX_TOTAL_EXPOSURE_PCT", 0.95):
            menge = _rm(held="raise")._step_total_exposure_cap(100.0, 100.0, note)
        assert menge == 0.0
        assert spur == {"zero_reason": "exposure_check_failed"}

    def test_kelly_skaliert_die_menge(self):
        spur, note = _spur()
        with patch("config.KELLY_FRACTION_CAP", 0.5, create=True):
            assert _rm()._step_kelly(100.0, note) == 50.0
        assert spur == {"binding_limit": "kelly"}

    def test_kelly_ohne_wert_aendert_nichts(self):
        spur, note = _spur()
        with patch("config.KELLY_FRACTION_CAP", None, create=True):
            assert _rm()._step_kelly(100.0, note) == 100.0
        assert spur == {}

    def test_max_loss_per_trade_kappt_auf_den_verlust_am_stop(self):
        spur, note = _spur()
        rm = _rm()
        rm.max_loss_per_trade = 1_500.0
        assert rm._step_max_loss_per_trade(100.0, 10.0, 3.0, 100.0, note) == 50.0
        assert spur == {"binding_limit": "max_loss_per_trade"}

    def test_compliance_order_value_bleibt_unter_dem_orderdeckel(self):
        spur, note = _spur()
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 10_000.0):
            menge = _rm()._step_compliance_order_value(200.0, 100.0, note)
        assert menge == pytest.approx(99.99)
        assert spur == {
            "binding_limit": "compliance_order_value",
            "binding_cap_value": "9999.00",
        }

    def test_compliance_order_value_folgt_der_policy(self):
        spur, note = _spur()
        rm = _rm()
        rm._policy_max_order_value = 500.0
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 10_000.0):
            menge = rm._step_compliance_order_value(200.0, 100.0, note)
        assert menge == pytest.approx(4.9995)
        assert spur["binding_limit"] == "compliance_order_value"
