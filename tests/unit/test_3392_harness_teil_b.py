"""#3392 Teil B — Geld-Gate: misst die Menge, die den Broker erreicht.

Plan: ``docs/3392-codeowners-ruleset-verhaltens-gate/implementation_plan.md`` §9 und §10.

Der Vorfall, den dieses Gate faengt: Eine Sizing-Aenderung war zwei Tage live und
wirkungslos, weil ein Deckel weiter hinten unveraendert band. Die Eingangsgroesse hatte
sich bewegt, das Geld nicht, und nichts hat das Geld geprueft.

**Diese Datei loest den ersten Stand aus PR #3536 ab** (drei Szenarien, Messpunkt
``_sende_durchs_tor``, Erwartungswerte in der Tabelle). Geaendert hat sich dreierlei:

* **Messpunkt:** ``client.submit_order`` — die Anfrage, die den Broker tatsaechlich
  erreicht. Alles dazwischen (Regime-Drossel, freigegebene Obergrenze, Compliance) wird
  damit mitgemessen statt uebersprungen.
* **Referenz:** eingecheckt unter ``tests/fixtures/geld_gate_referenz_3392.json``. Ihr
  Diff im PR zeigt dem Reviewer das Geld; Erwartungswerte im Testcode taeten das nicht.
* **Abdeckung:** der Abgleich laeuft gegen die VOLLSTAENDIGE Deckel-Liste. Der erste Stand
  pruefte drei Deckel und nannte das "jeder Deckel" — Positionsdeckel, Gesamt-Exposure,
  Order-Deckel, Regime-Drossel und die freigegebene Obergrenze blieben ungeprueft.

Harness und Szenario-Tabelle: ``tests/unit/_geld_gate.py``.
"""

from __future__ import annotations

import dataclasses
import sys
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HIER = Path(__file__).resolve().parent
if str(_HIER) not in sys.path:
    sys.path.insert(0, str(_HIER))

import _geld_gate as gate  # noqa: E402

pytestmark = pytest.mark.vc4

# Plan §9.3: das Gate beginnt als Warnung und wird in einem eigenen PR blockierend.
GATE_MODUS = "warnen"


def _sizer(capital=50_000.0, **kwargs):
    from core.risk_manager import RiskManager

    client = MagicMock()
    client.get_all_positions.return_value = []
    rm = RiskManager(client=client, total_capital=capital, clock=gate._uhr())
    spur: dict = {}
    argumente = dict(
        stop_loss_atr_multiplier=3.0,
        atr=0.5,
        confidence="high",
        size_scaler=1.0,
        market_data={"vix": 20.0},
        num_stocks_in_strategy=10,
        current_price=100.0,
        account_cash=200_000.0,
        allow_fractional=True,
        conviction_score=0.8,
        sizing_trace=spur,
    )
    argumente.update(kwargs)
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        menge = rm.calculate_position_size(**argumente)
    return menge, spur


# ---------------------------------------------------------------------------
# 1. Die Spur nennt den Deckel, der die Menge wirklich gesetzt hat
# ---------------------------------------------------------------------------


def test_max_verlust_je_trade_steht_in_der_spur():
    """Heute rot: risk_manager.py:1463-1472 klemmt ohne ``_note`` — die Spur behaelt den
    vorher bindenden Deckel oder gar keinen. Genau das Vorfall-Muster."""
    menge, spur = _sizer(atr=10.0)
    assert menge == pytest.approx(25.0)  # 750 USD / (10 x 3)
    assert spur.get("binding_limit") == "max_loss_per_trade"


def test_kelly_steht_in_der_spur(monkeypatch):
    import config

    monkeypatch.setattr(config, "KELLY_FRACTION_CAP", 0.5, raising=False)
    menge, spur = _sizer()
    assert menge == pytest.approx(25.0)  # Zielgewicht 5000 USD x 0,5
    assert spur.get("binding_limit") == "kelly"


def test_ein_deckel_der_nicht_klemmt_schreibt_keine_spur(monkeypatch):
    """Nur wer die Menge verkleinert, darf als bindend gelten: Kelly 1,0 und ein
    Max-Verlust oberhalb der Menge lassen die Spur unberuehrt."""
    import config

    monkeypatch.setattr(config, "KELLY_FRACTION_CAP", 1.0, raising=False)
    _, spur = _sizer(atr=0.5)
    assert spur.get("binding_limit") not in ("kelly", "max_loss_per_trade")


# ---------------------------------------------------------------------------
# 2. Der tragende Test: der Vorfall in Testform
# ---------------------------------------------------------------------------


def test_das_gate_meldet_eine_wirkungslose_sizing_aenderung():
    """Das Zielgewicht steigt (5 statt 10 Positionen -> 1/5 statt 1/10), der
    Max-Verlust-Deckel bindet unveraendert. Das Gate muss WIRKUNGSLOS melden und den
    bindenden Deckel nennen. Heute rot, weil es kein Gate gibt und die Spur den Deckel
    nicht kennt."""
    basis = next(s for s in gate.SZENARIEN if s.name == "max_verlust")
    geaendert = dataclasses.replace(
        basis, laufzeit={"MAX_POSITIONS": 5, "FULL_UNIVERSE_MAX_POSITIONS": 5}
    )
    referenz = {basis.name: gate.fahre(basis)}
    ist = {basis.name: gate.fahre(geaendert)}

    assert ist[basis.name]["zielgewicht"] != referenz[basis.name]["zielgewicht"]
    befunde = gate.befunde(referenz, ist)
    assert [b[0] for b in befunde] == [gate.WIRKUNGSLOS], befunde
    assert "max_loss_per_trade" in befunde[0][2]


def test_eine_geaenderte_ordermenge_ist_geldwirkung():
    basis = next(s for s in gate.SZENARIEN if s.name == "zielgewicht_setzt_die_menge")
    geaendert = dataclasses.replace(
        basis, laufzeit={"MAX_POSITIONS": 5, "FULL_UNIVERSE_MAX_POSITIONS": 5}
    )
    befunde = gate.befunde(
        {basis.name: gate.fahre(basis)}, {basis.name: gate.fahre(geaendert)}
    )
    assert [b[0] for b in befunde] == [gate.GELDWIRKUNG], befunde


def test_ein_anderer_bindender_deckel_bei_gleichem_geld_wird_gemeldet():
    referenz = {"x": {"notional": 100.0, "deckel": "cash", "zielgewicht": 0.1}}
    ist = {"x": {"notional": 100.0, "deckel": "kelly", "zielgewicht": 0.1}}
    assert [b[0] for b in gate.befunde(referenz, ist)] == [gate.DECKEL_WECHSEL]


# ---------------------------------------------------------------------------
# 3. Abdeckung und Messort
# ---------------------------------------------------------------------------


def test_jeder_deckel_bindet_in_mindestens_einem_szenario():
    """Ein Deckel ohne Szenario ist ein blinder Fleck des Gates (Plan §9.3)."""
    gemessen = gate.lade_referenz()
    gebunden = {wert["deckel"] for wert in gemessen.values()}
    fehlt = [d for d in gate.DECKEL if d not in gebunden]
    assert not fehlt, f"Deckel ohne bindendes Szenario: {fehlt}"


def test_jedes_szenario_bindet_den_deckel_den_es_vorgibt():
    gemessen = gate.lade_referenz()
    falsch = {
        s.name: gemessen[s.name]["deckel"]
        for s in gate.SZENARIEN
        if gemessen.get(s.name, {}).get("deckel") != s.bindet
    }
    assert not falsch, f"Szenario bindet einen anderen Deckel als vorgegeben: {falsch}"


def test_gemessen_wird_die_abgesendete_menge_nicht_die_sizer_ausgabe():
    """Die freigegebene Obergrenze wirkt NACH dem Sizer — ein Gate auf der Sizer-Ausgabe
    saehe 25 Stueck, beim Broker kommen 3 an."""
    sz = next(s for s in gate.SZENARIEN if s.name == "freigegebene_obergrenze")
    ergebnis = gate.fahre(sz)
    assert ergebnis["menge"] == pytest.approx(3.0)
    assert ergebnis["deckel"] == "freigegebene_obergrenze"


def test_desktop_pfad_ohne_vorgabe_bemisst_ueber_den_sizer():
    sz = next(s for s in gate.SZENARIEN if s.name == "zielgewicht_setzt_die_menge")
    ergebnis = gate.fahre_desktop(sz, vorgabe=0.0)
    assert ergebnis["sizer_aufrufe"] == 1
    assert ergebnis["menge"] == pytest.approx(50.0)


def test_desktop_pfad_klemmt_eine_vorgabe_auf_die_sizer_grenze():
    """#3528 ist behoben (PR #3532, "clamp desktop buy quantities to risk manager limits").

    Vorher belegte dieser Test den Befund: Mit vorgegebener Menge lief im Desktop-Pfad
    kein Sizer, 70 Stueck (7.000 USD) gingen hinaus, wo der Sizer 50 bemisst. Jetzt
    sichert er die Korrektur ab — eine Vorgabe oberhalb der Sizer-Grenze wird geklemmt.
    """
    sz = next(s for s in gate.SZENARIEN if s.name == "zielgewicht_setzt_die_menge")
    ohne = gate.fahre_desktop(sz, vorgabe=0.0)
    mit = gate.fahre_desktop(sz, vorgabe=70.0)
    assert mit["sizer_aufrufe"] >= 1, "die Vorgabe umgeht den Sizer wieder (#3528)"
    assert mit["menge"] <= ohne["menge"] + 1e-6, (
        f"Vorgabe 70 ergab {mit['menge']} Stueck, der Sizer bemisst {ohne['menge']} — "
        "die Klemme aus #3532 greift nicht mehr."
    )


# ---------------------------------------------------------------------------
# 4. Das Gate selbst: der eingecheckte Stand gegen den Code
# ---------------------------------------------------------------------------


def test_das_geld_entspricht_der_referenz():
    befunde = gate.befunde(gate.lade_referenz(), gate.messe_alle())
    if not befunde:
        return
    text = (
        "Geld-Gate (#3392): die abgesendeten Orderwerte weichen von der Referenz ab.\n"
        + "\n".join(f"  {art} {name}: {was}" for art, name, was in befunde)
        + "\nIst die Aenderung gewollt: `python tests/unit/_geld_gate.py --schreibe` "
        "und den Diff der Referenz im PR begruenden."
    )
    if GATE_MODUS == "blockieren":
        pytest.fail(text, pytrace=False)
    warnings.warn(text, UserWarning, stacklevel=1)
