"""#3382 (Epic #3366) — der Schutz liegt beim Broker, nicht im Prozess.

Heute liegt im Kern **keine einzige** Stop-Order beim Broker: der Kontrollgrep auf
``StopOrderRequest`` liefert keinen Treffer. Stops werden ausschliesslich von der
laufenden Engine ausgewertet (``core/position_stop.py``, ``core/intelligent_exit.py``) —
stirbt sie, ist die Position ungeschuetzt. Das ist die eine Halbe des CH-3-Szenarios, die
auch nach #3380 (Halt-Reihenfolge) rot bleibt: dort ging es darum, dass die Engine im
Halt weiter schuetzt; hier darum, dass der Schutz **ohne** Engine haelt.

**Bruchstuecke bleiben** (Owner-Entscheid: grosser Vorteil bei kleinen Konten). Alpaca
nimmt fraktionale Orders aber nur als Tages-Order an — eine GTC-Stop-Order ueber einen
Bruchteil ist nicht moeglich. Daraus folgt die Aufteilung: GTC fuer die ganzen Stuecke,
Tages-Stop fuer den Rest, und der Tages-Stop muss erneuert werden. Das getragene
Restrisiko ist dadurch auf weniger als ein Stueck je Position begrenzt — und es wird
ausgewiesen, nicht versteckt.
"""

from datetime import date

import pytest


class _Pos:
    def __init__(self, symbol, qty, avg_entry_price=100.0, qty_available=None):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        if qty_available is not None:
            self.qty_available = qty_available


class _Stop:
    """Eine beim Broker liegende Stop-Order."""

    def __init__(self, oid, symbol, qty, stop_price, tif="gtc"):
        self.id = oid
        self.symbol = symbol
        self.qty = qty
        self.stop_price = stop_price
        self.time_in_force = tif


HEUTE = date(2026, 9, 16)
GESTERN = date(2026, 9, 15)


def _plan(positions, **kw):
    from core.broker_stops import plan_broker_stops

    kw.setdefault("stop_loss_pct", 7.0)
    kw.setdefault("session_date", HEUTE)
    return plan_broker_stops(positions, **kw)


# ---------------------------------------------------------------------------
# 1 — Die Aufteilung
# ---------------------------------------------------------------------------


def test_ganze_stuecke_bekommen_einen_gtc_stop():
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=100.0)])

    assert len(plan.to_place) == 1
    stop = plan.to_place[0]
    assert stop.symbol == "AAPL"
    assert stop.qty == 3
    assert stop.time_in_force == "gtc"
    assert stop.leg == "whole"


def test_der_bruchstueck_rest_bekommt_einen_tages_stop():
    """Alpaca nimmt fraktionale Orders nur als Tages-Order an."""
    plan = _plan([_Pos("AAPL", "3.4", avg_entry_price=100.0)])

    nach_leg = {s.leg: s for s in plan.to_place}
    assert nach_leg["whole"].qty == 3
    assert nach_leg["whole"].time_in_force == "gtc"
    assert nach_leg["fraction"].qty == pytest.approx(0.4)
    assert nach_leg["fraction"].time_in_force == "day"


def test_eine_reine_bruchstueck_position_bekommt_nur_einen_tages_stop():
    plan = _plan([_Pos("AAPL", "0.4", avg_entry_price=100.0)])

    assert len(plan.to_place) == 1
    assert plan.to_place[0].leg == "fraction"
    assert plan.to_place[0].time_in_force == "day"


def test_der_stop_preis_folgt_der_eingestellten_schwelle():
    plan = _plan([_Pos("AAPL", "1", avg_entry_price=200.0)], stop_loss_pct=7.0)

    assert plan.to_place[0].stop_price == pytest.approx(186.00)


def test_der_stop_preis_ist_auf_cent_gerundet():
    """Ein Broker nimmt keinen Preis mit acht Nachkommastellen an."""
    plan = _plan([_Pos("AAPL", "1", avg_entry_price=33.33)], stop_loss_pct=7.0)

    preis = plan.to_place[0].stop_price
    assert preis == round(preis, 2)


def test_die_verfuegbare_menge_hat_vorrang():
    """Gebundene Stuecke duerfen nicht ein zweites Mal verkauft werden."""
    plan = _plan([_Pos("AAPL", "5", qty_available="3")])

    assert sum(s.qty for s in plan.to_place) == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# 2 — Was schon liegt, bleibt liegen
# ---------------------------------------------------------------------------


def test_ein_passender_gtc_stop_wird_nicht_ersetzt():
    """Sonst wuerde jeder Zyklus den Schutz kurz abraeumen und neu legen — und genau in
    dieser Luecke ist die Position ungeschuetzt."""
    vorhanden = [_Stop("s-1", "AAPL", 3, 93.00, "gtc")]
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=100.0)], existing_stops=vorhanden)

    assert plan.to_place == ()
    assert plan.to_cancel == ()


def test_ein_stop_auf_falscher_menge_wird_ersetzt():
    vorhanden = [_Stop("s-1", "AAPL", 2, 93.00, "gtc")]
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=100.0)], existing_stops=vorhanden)

    assert "s-1" in plan.to_cancel
    assert any(s.qty == 3 for s in plan.to_place)


def test_ein_stop_auf_falschem_preis_wird_ersetzt():
    vorhanden = [_Stop("s-1", "AAPL", 3, 80.00, "gtc")]
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=100.0)], existing_stops=vorhanden)

    assert "s-1" in plan.to_cancel


def test_ein_stop_ohne_position_wird_abgeraeumt():
    """Die Position ist weg, der Stop liegt noch — ein Leerverkauf in Wartestellung."""
    vorhanden = [_Stop("s-alt", "NVDA", 2, 90.00, "gtc")]
    plan = _plan([_Pos("AAPL", "1")], existing_stops=vorhanden)

    assert "s-alt" in plan.to_cancel


# ---------------------------------------------------------------------------
# 3 — Erneuerung des Tages-Stops
# ---------------------------------------------------------------------------


def test_ein_tages_stop_von_gestern_wird_erneuert():
    """Sitzungsbeginn: ein Tages-Stop ist abgelaufen, die Position waere ungeschuetzt."""
    vorhanden = [_Stop("s-gestern", "AAPL", 0.4, 93.00, "day")]
    plan = _plan(
        [_Pos("AAPL", "0.4", avg_entry_price=100.0)],
        existing_stops=vorhanden,
        existing_session_date=GESTERN,
    )

    assert any(s.leg == "fraction" for s in plan.to_place), (
        "Der abgelaufene Tages-Stop wurde nicht erneuert — genau dann ist der "
        "Bruchstueck-Rest ungeschuetzt."
    )


def test_ein_tages_stop_von_heute_bleibt():
    vorhanden = [_Stop("s-heute", "AAPL", 0.4, 93.00, "day")]
    plan = _plan(
        [_Pos("AAPL", "0.4", avg_entry_price=100.0)],
        existing_stops=vorhanden,
        existing_session_date=HEUTE,
    )

    assert plan.to_place == ()


def test_ein_gtc_stop_ueberlebt_den_sitzungswechsel():
    vorhanden = [_Stop("s-1", "AAPL", 3, 93.00, "gtc")]
    plan = _plan(
        [_Pos("AAPL", "3", avg_entry_price=100.0)],
        existing_stops=vorhanden,
        existing_session_date=GESTERN,
    )

    assert plan.to_place == ()
    assert plan.to_cancel == ()


# ---------------------------------------------------------------------------
# 4 — Ungeschuetzte Positionen werden sichtbar
# ---------------------------------------------------------------------------


def test_eine_position_ohne_einstandspreis_gilt_als_ungeschuetzt():
    """Ohne Einstand gibt es keine Schwelle — und einen Stop zu erfinden waere schlimmer
    als keinen zu haben."""
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=0.0)])

    assert plan.to_place == ()
    assert [s for s, _ in plan.unprotected] == ["AAPL"]


def test_eine_zu_kleine_position_wird_als_ungeschuetzt_ausgewiesen():
    """Unter der Broker-Mindestmenge laesst sich kein Stop legen. Das Restrisiko ist
    winzig, aber es wird ausgewiesen statt verschwiegen."""
    plan = _plan([_Pos("AAPL", "0.0001", avg_entry_price=100.0)])

    assert [s for s, _ in plan.unprotected] == ["AAPL"]


def test_ein_vollstaendiger_plan_meldet_sich_als_vollstaendig():
    plan = _plan([_Pos("AAPL", "3"), _Pos("MSFT", "1.5")])

    assert plan.complete is True
    assert plan.unprotected == ()


def test_der_grund_steht_am_befund():
    plan = _plan([_Pos("AAPL", "3", avg_entry_price=0.0)])

    _symbol, grund = plan.unprotected[0]
    assert grund, "Ein Alarm ohne Grund ist keiner"


# ---------------------------------------------------------------------------
# 5 — Kein Doppelverkauf
# ---------------------------------------------------------------------------


def test_jeder_stop_traegt_einen_abgeleiteten_schluessel():
    """#3387: Broker-Stop und Engine-Exit koennen zusammentreffen. Der Schluessel ist
    das, was die zweite Order verhindert."""
    from core.idempotency import derive_client_order_id

    plan = _plan([_Pos("AAPL", "3", avg_entry_price=100.0)])
    stop = plan.to_place[0]

    assert stop.client_order_id
    assert stop.client_order_id == derive_client_order_id(stop.decision_id, "stop", 0)


def test_der_gtc_schluessel_ist_ueber_neustarts_stabil():
    """Nach einem Neustart wird derselbe Stop neu geplant — er darf kein Duplikat sein."""
    erst = _plan([_Pos("AAPL", "3")]).to_place[0]
    zweit = _plan([_Pos("AAPL", "3")]).to_place[0]

    assert erst.client_order_id == zweit.client_order_id


def test_der_tages_schluessel_unterscheidet_die_sitzungen():
    """Die Erneuerung am naechsten Tag ist eine ABSICHTLICH neue Order."""
    heute = _plan([_Pos("AAPL", "0.4")], session_date=HEUTE).to_place[0]
    morgen = _plan([_Pos("AAPL", "0.4")], session_date=date(2026, 9, 17)).to_place[0]

    assert heute.client_order_id != morgen.client_order_id


def test_die_beiden_legs_derselben_position_kollidieren_nicht():
    plan = _plan([_Pos("AAPL", "3.4")])

    schluessel = {s.client_order_id for s in plan.to_place}
    assert len(schluessel) == 2


# ---------------------------------------------------------------------------
# 6 — Scharfschalten erst nach dem Abgleich
# ---------------------------------------------------------------------------


def test_der_schalter_ist_standardmaessig_an():
    """Plan #3382 §TDD-3: Ausfuehrung erst NACH dem Reconciler (#3389). Mit #3632 standardmaessig aktiv."""
    from config import RuntimeConfigState

    assert RuntimeConfigState().BROKER_STOPS_ENABLED is True


def test_beide_editionen_tragen_denselben_schalter():
    import importlib.util
    import os
    from pathlib import Path
    from unittest.mock import patch

    from config import RuntimeConfigState

    spec = importlib.util.spec_from_file_location(
        "config_oss_3382",
        str(Path(__file__).resolve().parents[2] / "config.oss.py"),
    )
    oss = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, clear=False):
        spec.loader.exec_module(oss)

    assert oss.BROKER_STOPS_ENABLED == RuntimeConfigState().BROKER_STOPS_ENABLED


def test_der_planer_liest_keine_konfiguration():
    """Rein und ohne I/O — damit er ohne Engine, ohne Netz und ohne Broker pruefbar ist."""
    import ast
    from pathlib import Path

    baum = ast.parse(
        (Path(__file__).resolve().parents[2] / "core" / "broker_stops.py").read_text(
            encoding="utf-8"
        )
    )
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Call):
            name = getattr(knoten.func, "id", None) or getattr(knoten.func, "attr", "")
            assert name not in ("get_config", "getenv"), f"{name}() im Planer"


# ---------------------------------------------------------------------------
# 7 — Ein unlesbarer Broker-Stand fuehrt zu keiner Entscheidung
#     (Review #3432, P1)
# ---------------------------------------------------------------------------


def test_ein_nicht_lesbarer_orderstand_darf_nicht_als_leer_gelten():
    """Der Quellbeleg zur Korrektur aus der Review.

    Schlaegt ``api.get_orders`` fehl, hiesse eine leere Liste fuer den Planer „es liegt
    kein Stop" — er legte dann Stops doppelt an und raeumte bestehende ab, beides auf
    Basis einer Annahme statt einer Beobachtung. Derselbe Fehler steckte bis #3389 im
    Abgleich, wo ein Broker-Ausfall als „nichts beim Broker" durchging.

    Die konservative Richtung ist, den Durchgang auszulassen: was liegt, bleibt liegen.
    """
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "engine" / "trading_loop.py"
    ).read_text(encoding="utf-8")

    beginn = quelle.index("async def _maintain_broker_stops")
    koerper = quelle[beginn : beginn + 4000]
    abruf = koerper.index("api.get_orders")
    danach = koerper[abruf : abruf + 700]

    assert "offene = []" not in danach, (
        "Ein fehlgeschlagener Orderabruf wird als leere Liste weitergereicht — der "
        "Planer haelt das fuer 'es liegt nichts'."
    )
    assert "return" in danach, (
        "Der Durchgang wird nicht ausgelassen; es wird auf einem ungelesenen Stand "
        "gelegt oder storniert."
    )
    assert "exc_info=True" in danach, "Der Fehler wird ohne Stapelspur gemeldet."


def test_alle_catch_bloecke_der_stop_pflege_melden_mit_stapelspur():
    """Review #3432 (P2): ein stiller Fehlschlag im Schutzpfad ist keiner, den man
    hinterher noch untersuchen kann."""
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "engine" / "trading_loop.py"
    ).read_text(encoding="utf-8")

    beginn = quelle.index("async def _maintain_broker_stops")
    ende = quelle.index("async def _run_position_stop_checks")
    koerper = quelle[beginn:ende]

    catches = koerper.count("except Exception")
    mit_spur = koerper.count("exc_info=True")
    assert (
        mit_spur >= catches
    ), f"{catches} Catch-Bloecke, aber nur {mit_spur} mit exc_info=True."
