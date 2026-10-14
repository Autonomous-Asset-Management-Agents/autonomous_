"""#3833 (G-7a) — Geld-Gate fuer den Strategie-Pfad.

Plan: ``docs/3833-geld-gate-szenario-fuer-den-strategie-pfad-dann/implementation_plan.md``.

Das Geld-Gate aus #3392 (``test_3392_harness_teil_b.py``) faehrt den Tenant-Pfad des
Executors. Der Strategie-Pfad — ``RLExecutionMixin._run_for_symbol_impl`` und
``BaseStrategy._submit_order_safe`` — lief bisher an ihm vorbei. Dieses Gate misst dort
dasselbe: was den Broker erreicht, dazu Datensatz des Tors, Halt und Tagesbudget, gegen
eine eingecheckte Referenz. Es ist das Netz fuer die Zerlegung beider Funktionen
(#4088, #4089) und aendert keinen Produktivcode.

Harness und Szenario-Tabelle: ``tests/unit/_geld_gate_strategie.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import _geld_gate_strategie as gate  # noqa: E402

pytestmark = pytest.mark.vc4


def _szenario(name: str):
    return next(s for s in gate.SZENARIEN if s.name == name)


# ---------------------------------------------------------------------------
# 1. Das Gate sieht den Strategie-Pfad: Datensatz, Halt, Budget
# ---------------------------------------------------------------------------


def test_der_kauf_der_strategie_traegt_datensatz_und_bucht_budget():
    """Ein BUY aus ``_run_for_symbol_impl``: der echte Sizer bemisst, die Order geht
    durchs Tor (ein Datensatz) und verbraucht genau einen Platz im Tagesbudget."""
    ergebnis = gate.fahre(_szenario("lauf_kauf_markt"))

    assert [o["weg"] for o in ergebnis["gesendet"]] == ["MarketOrderRequest"]
    assert ergebnis["gesendet"][0]["seite"] == "buy"
    assert ergebnis["gesendet"][0]["menge"] > 0
    assert ergebnis["datensatz"] == ["approved"]
    assert ergebnis["budget"] == 1


def test_ein_halt_blockt_den_kauf_auf_dem_absendeweg():
    """Direkt an ``_submit_order_safe``: keine Risikopruefung davor, die den Halt schon
    faengt."""
    ergebnis = gate.fahre(_szenario("absendung_kauf_bei_halt"))

    assert ergebnis["gesendet"] == []
    assert ergebnis["budget"] == 0
    assert ergebnis["ergebnis"] is False


def test_ein_halt_laesst_den_stop_loss_auf_dem_absendeweg_durch():
    ergebnis = gate.fahre(_szenario("absendung_stop_loss_bei_halt"))

    assert [o["seite"] for o in ergebnis["gesendet"]] == ["sell"]
    assert ergebnis["datensatz"] and ergebnis["datensatz"][0].endswith(":halt")


def test_ein_erschoepftes_tagesbudget_blockt_den_kauf():
    ergebnis = gate.fahre(_szenario("absendung_kauf_budget_erschoepft"))

    assert ergebnis["gesendet"] == []
    assert ergebnis["datensatz"] == []
    assert ergebnis["budget"] == 0


# ---------------------------------------------------------------------------
# 2. Abdeckung
# ---------------------------------------------------------------------------


def test_jeder_aspekt_wird_von_einem_szenario_geprueft():
    """Datensatz, Halt und Budget — fehlt einer, ist er ein blinder Fleck des Gates."""
    geprueft = {a for s in gate.SZENARIEN for a in s.prueft}
    fehlt = [a for a in gate.ASPEKTE if a not in geprueft]
    assert not fehlt, f"Aspekt ohne Szenario: {fehlt}"


def test_jeder_absendezweig_wird_in_der_referenz_befahren():
    """``_submit_order_safe`` hat fuenf Wege zum Broker. Drei davon (Alpaca-Form Markt
    und Limit, Kwargs-Form) laufen durchs Tor; Simulation und async-Client nicht — genau
    diese beiden sind die eingefrorenen Ausnahmen in ``vertrag.toml``."""
    befahren = {
        o["weg"] for wert in gate.lade_referenz().values() for o in wert["gesendet"]
    }
    fehlt = [w for w in gate.ZWEIGE if w not in befahren]
    assert not fehlt, f"Absendezweig ohne Szenario: {fehlt}"


def test_referenz_und_szenario_tabelle_decken_sich():
    namen = {s.name for s in gate.SZENARIEN}
    referenz = set(gate.lade_referenz())
    assert namen == referenz, (
        f"ohne Referenz: {sorted(namen - referenz)}, "
        f"verwaiste Referenz: {sorted(referenz - namen)}"
    )


# ---------------------------------------------------------------------------
# 3. Das Gate scheitert, wenn der Pfad sich veraendert (Negativprobe)
# ---------------------------------------------------------------------------


def test_eine_veraenderte_menge_auf_dem_pfad_ist_ein_befund(monkeypatch):
    """Verdoppelt ``_submit_order_safe`` die Menge, meldet das Gate jedes Szenario, das
    etwas absendet, als GELDWIRKUNG."""
    from core.strategies.base import BaseStrategy

    echt = BaseStrategy._submit_order_safe

    async def _doppelt(self, symbol, qty, side, *args, **kwargs):
        return await echt(self, symbol, qty * 2, side, *args, **kwargs)

    monkeypatch.setattr(BaseStrategy, "_submit_order_safe", _doppelt)
    referenz = gate.lade_referenz()
    befunde = gate.befunde(referenz, gate.messe_alle())

    sendend = {name for name, wert in referenz.items() if wert["gesendet"]}
    assert sendend, "Die Referenz enthaelt kein absendendes Szenario."
    assert {name for art, name, _ in befunde if art == gate.GELDWIRKUNG} == sendend


def test_ein_uebergangener_halt_auf_dem_pfad_ist_ein_befund(monkeypatch):
    """Faellt die Halt-Pruefung vor dem Tor weg, schickt der Kauf bei Halt wieder ab —
    das Tor allein blockt ihn zwar, schreibt aber einen Datensatz: das Gate meldet es.
    """
    from core import kill_switch as ks_mod

    monkeypatch.setattr(
        type(ks_mod.kill_switch), "check_halt", lambda self, user_id=None: None
    )
    sz = _szenario("absendung_kauf_bei_halt")
    befunde = gate.befunde(gate.lade_referenz(), {sz.name: gate.fahre(sz)})
    assert [b[1] for b in befunde] == [sz.name], befunde


# ---------------------------------------------------------------------------
# 4. Das Gate selbst: der eingecheckte Stand gegen den Code
# ---------------------------------------------------------------------------


def test_der_strategiepfad_entspricht_der_referenz():
    befunde = gate.befunde(gate.lade_referenz(), gate.messe_alle())
    if not befunde:
        return
    pytest.fail(
        "Geld-Gate Strategie-Pfad (#3833): was den Broker erreicht, weicht von der "
        "Referenz ab.\n"
        + "\n".join(f"  {art} {name}: {was}" for art, name, was in befunde)
        + "\nIst die Aenderung gewollt: `python tests/unit/_geld_gate_strategie.py "
        "--schreibe` und den Diff der Referenz im PR begruenden.",
        pytrace=False,
    )
