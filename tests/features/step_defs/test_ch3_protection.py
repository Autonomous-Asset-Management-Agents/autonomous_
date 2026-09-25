"""CH-3 — Schutz bei Stoerung (#3377 fuer Epic #3366).

Auch dieser Test ist heute ROT und soll es sein. Er scheitert an zwei belegbaren
Tatsachen, nicht an fehlender Vorrichtung:

1. Im Kern geht keine Stop-Order an den Broker (Grep StopOrderRequest: 0 Treffer,
   MarketOrderRequest: 16). Der Stop ist die Meinung eines laufenden Prozesses.
2. Ein ausgeloester Halt ueberspringt die Positions-Stops: die Halt-Pruefung steht in
   trading_loop.py vor `_run_position_stop_checks()`, und selbst nach einem Tausch der
   Reihenfolge weist das Kill-Switch-Tor im Order-Pfad den Schutz-Exit ab.

Gruen wird er durch #3380 (Reihenfolge + Freistellung) und #3382 (Broker-Stops).
"""

import re
from pathlib import Path

from pytest_bdd import given, scenarios, then, when

scenarios("../ch3_protection_under_halt.feature")


ROOT = Path(__file__).resolve().parents[3]
CORE = ROOT / "core"
TRADING_LOOP = CORE / "engine" / "trading_loop.py"

_STOP_REQUESTS = re.compile(
    r"\b(StopOrderRequest|StopLossRequest|TrailingStopOrderRequest)\b"
)


def _production_files():
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE).as_posix()
        if rel.startswith(("sim", "research")) or "test" in rel:
            continue
        yield rel, path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Szenario 1 — der Broker haelt den Schutz, nicht der Prozess
# ---------------------------------------------------------------------------


@given("eine offene Position mit hinterlegtem Schutz-Stop", target_fixture="stop_sites")
def protective_stop_is_resting_at_the_broker():
    sites = [
        f"core/{rel}:{no}"
        for rel, text in _production_files()
        for no, line in enumerate(text.splitlines(), 1)
        if _STOP_REQUESTS.search(line) and not line.strip().startswith("#")
    ]
    assert sites, (
        "Der Kern legt keine einzige Stop-Order beim Broker an "
        "(grep StopOrderRequest|StopLossRequest|TrailingStopOrderRequest ueber core/: "
        "kein Treffer). Eine Position ist damit nur geschuetzt, solange der Prozess "
        "laeuft. Wird durch #3382 hergestellt."
    )
    return sites


@when(
    "die Engine nicht laeuft und der Kurs die Stop-Schwelle erreicht",
    target_fixture="broker_nach_kurssturz",
)
def price_crosses_while_engine_is_down():
    """#3382: ausformuliert, seit der Kern Stop-Orders beim Broker anlegt.

    Der Beweis der Aussage haengt daran, dass hier **kein** Zyklus, keine Engine und kein
    Trading-Loop laeuft — nur der Broker und der Kurs. Genau das ist der Unterschied zu
    #3380: dort schuetzt die Engine im Halt weiter, hier schuetzt der Broker ohne sie.
    """
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import StopOrderRequest

    from core.broker_stops import plan_broker_stops

    class _Pos:
        def __init__(self, symbol, qty, avg):
            self.symbol = symbol
            self.qty = qty
            self.avg_entry_price = avg

    class _Bar:
        def __init__(self, close):
            self.close = close

    class _Daten:
        """Liefert genau einen Kurs — den, bei dem der Stop ausloest."""

        def __init__(self, symbol, preis):
            self._symbol, self._preis = symbol, preis

        def get_stock_bars(self, request):
            return type("R", (), {"data": {self._symbol: [_Bar(self._preis)]}})()

    plan = plan_broker_stops(
        [_Pos("AAPL", "3", 100.0)], stop_loss_pct=7.0, session_date=None
    )
    stop = plan.to_place[0]

    from core.sim.broker import VirtualLiveBroker

    broker = VirtualLiveBroker()
    broker.positions["AAPL"] = _Pos("AAPL", "3", "100.0")
    broker.submit_order(
        StopOrderRequest(
            symbol=stop.symbol,
            qty=stop.qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            stop_price=stop.stop_price,
            client_order_id=stop.client_order_id,
        )
    )

    # Kurs UEBER der Schwelle: der Stop bleibt liegen — das ist der halbe Beweis.
    broker.fill_engine.data_client = _Daten("AAPL", stop.stop_price + 5.0)
    broker.fill_engine.process_orders(list(broker.orders))
    ruhend = [o for o in broker.orders if o.status not in ("filled", "canceled")]
    assert (
        ruhend
    ), "Der Stop wurde ausgefuehrt, obwohl der Kurs die Schwelle nicht erreicht hat"

    # Kurs UNTER der Schwelle, weiterhin ohne jede Engine.
    broker.fill_engine.data_client = _Daten("AAPL", stop.stop_price - 1.0)
    broker.fill_engine.process_orders(list(broker.orders))
    return broker


@then("fuehrt der Broker den Stop aus")
def broker_executes_the_stop(broker_nach_kurssturz):
    gefuellt = [
        o for o in broker_nach_kurssturz.orders if getattr(o, "status", "") == "filled"
    ]
    assert gefuellt, (
        "Der Broker hat den hinterlegten Stop nicht ausgefuehrt, obwohl der Kurs die "
        "Schwelle unterschritten hat."
    )


@then("die Position ist geschlossen")
def position_is_closed(broker_nach_kurssturz):
    rest = broker_nach_kurssturz.positions.get("AAPL")
    menge = float(getattr(rest, "qty", 0) or 0) if rest is not None else 0.0
    assert menge == 0.0, f"Die Position haelt noch {menge} Stueck."


# ---------------------------------------------------------------------------
# Szenario 2 — Stop-Pflege im Halt
# ---------------------------------------------------------------------------


@given(
    "eine offene Position und ein ausgeloester Kill-Switch",
    target_fixture="loop_source",
)
def halted_cycle():
    assert TRADING_LOOP.is_file(), f"nicht gefunden: {TRADING_LOOP}"
    return TRADING_LOOP.read_text(encoding="utf-8", errors="replace").splitlines()


@when("der Zyklus laeuft", target_fixture="order_of_gates")
def cycle_runs(loop_source):
    """Sucht die Stop-Pflege und die Stelle, an der ein Halt den Zyklus beendet.

    Gemessen wird nicht die Halt-*Pruefung* — die darf stehen, wo sie will —, sondern
    das `continue`, mit dem ein gehaltener Zyklus abbricht. Es muss NACH der Stop-Pflege
    kommen, sonst laufen die Stops offener Positionen im Halt nicht.
    """
    halt_end, stops = None, None
    for no, line in enumerate(loop_source, 1):
        if stops is None and "_run_position_stop_checks()" in line:
            stops = no
        if halt_end is None and line.strip() == "continue":
            umfeld = " ".join(loop_source[max(0, no - 6) : no]).lower()
            if "halt" in umfeld:
                halt_end = no
    return {"halt": halt_end, "stops": stops}


@then("wird der Schutz der Position weiter gepflegt")
def stops_run_before_the_halt_sleeps(order_of_gates):
    halt, stops = order_of_gates["halt"], order_of_gates["stops"]
    assert halt and stops, (
        f"Halt-Abbruch oder Stop-Pflege nicht gefunden (halt={halt}, stops={stops}) — "
        "der Test muss angepasst werden, bevor er etwas beweist."
    )
    assert stops < halt, (
        f"Der gehaltene Zyklus bricht in trading_loop.py:{halt} ab, VOR der Stop-Pflege "
        f"in :{stops}. Solange der Halt zuerst greift, laufen die Stops offener "
        "Positionen nicht."
    )


@then("es entsteht kein neuer Einstieg")
def no_new_entry(order_of_gates):
    """Der gehaltene Zyklus muss nach der Stop-Pflege abbrechen.

    Sonst liefe der Konsens weiter und koennte einen Einstieg erzeugen — der Halt waere
    dann keiner mehr. Das Verhalten selbst ist in
    ``tests/unit/test_halt_protective_exit.py`` gepinnt; hier wird die Struktur geprueft,
    damit die Reihenfolge nicht unbemerkt wieder kippt.
    """
    assert order_of_gates["halt"], (
        "Kein Abbruch des gehaltenen Zyklus gefunden. Ohne ihn laeuft nach der "
        "Stop-Pflege der ganze Zyklus weiter — inklusive neuer Einstiege."
    )
