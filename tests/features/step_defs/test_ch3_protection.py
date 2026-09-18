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


@when("die Engine nicht laeuft und der Kurs die Stop-Schwelle erreicht")
def price_crosses_while_engine_is_down():
    raise AssertionError("noch nicht erreichbar — es liegt kein Stop beim Broker")


@then("fuehrt der Broker den Stop aus")
def broker_executes_the_stop():
    raise AssertionError("noch nicht erreichbar")


@then("die Position ist geschlossen")
def position_is_closed():
    raise AssertionError("noch nicht erreichbar")


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
    halt, stops = None, None
    for no, line in enumerate(loop_source, 1):
        if halt is None and "trading_halted" in line and line.strip().startswith("if "):
            halt = no
        if stops is None and "_run_position_stop_checks()" in line:
            stops = no
    return {"halt": halt, "stops": stops}


@then("wird der Schutz der Position weiter gepflegt")
def stops_run_before_the_halt_sleeps(order_of_gates):
    halt, stops = order_of_gates["halt"], order_of_gates["stops"]
    assert halt and stops, (
        f"Halt-Pruefung oder Stop-Pflege nicht gefunden (halt={halt}, stops={stops}) — "
        "der Test muss angepasst werden, bevor er etwas beweist."
    )
    assert stops < halt, (
        f"Die Halt-Pruefung steht in trading_loop.py:{halt} VOR der Stop-Pflege in "
        f":{stops}. Solange der Halt zuerst greift und schlaeft, laufen die Stops "
        "offener Positionen nicht. Wird durch #3380 behoben — dort gehoert zusaetzlich "
        "die Freistellung des Schutz-Exits im Kill-Switch-Tor dazu."
    )


@then("es entsteht kein neuer Einstieg")
def no_new_entry():
    raise AssertionError("noch nicht erreichbar")
