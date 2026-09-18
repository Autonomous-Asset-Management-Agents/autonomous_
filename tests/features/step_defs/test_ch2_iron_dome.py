"""CH-2 — Vollstaendigkeit des Iron Dome (#3377 fuer Epic #3366).

Dieser Test ist heute ROT und soll es sein. Der Plan (§7) verlangt ausdruecklich, dass
er *inhaltlich* scheitert: ein ``ModuleNotFoundError`` gilt als NICHT erfuellt, weil er
nichts ueber das System aussagt. Deshalb pruefen die Schritte die Abwesenheit des
Vertrags als Tatsache und melden sie als Zusicherungsfehler mit Beleg.

Gruen wird er durch #3378 (Vertraege) und #3379 (Gateway).
"""

import re
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../ch2_iron_dome_completeness.feature")


CORE = Path(__file__).resolve().parents[3] / "core"

# Beide Aufrufformen. Der einfache Grep auf ".submit_order(" findet nur 16 der 21
# Stellen — `await asyncio.to_thread(client.submit_order, req)` faellt durch das Muster
# (Korrektur zu #3366, siehe Issue-Kommentar vom 16.09.2026).
_DIRECT = re.compile(
    r"\.(submit_order|_submit_order_safe|close_all_positions|close_position)\s*\("
)
_TO_THREAD = re.compile(r"to_thread\(\s*[\w.]*\.?(submit_order|close_position)\b")
_SKIP_DIRS = ("sim", "research")


def _broker_call_sites():
    """Alle Stellen im Kern, die eine Order am Broker mutieren."""
    sites = []
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE).as_posix()
        if rel.startswith(_SKIP_DIRS) or "test" in rel:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            stripped = line.strip()
            if stripped.startswith(("def ", "async def ", "#")):
                continue
            if _DIRECT.search(line) or _TO_THREAD.search(line):
                sites.append(f"core/{rel}:{lineno}")
    return sites


# ---------------------------------------------------------------------------
# Szenario 1 — jede Order traegt eine ComplianceDecision
# ---------------------------------------------------------------------------


@given("der Vertrag ComplianceDecision ist im Kern verfuegbar")
def contract_available():
    # Bewusst eine Dateisystem-Tatsache statt eines Imports: `import core.…` zieht die
    # Konfiguration hoch und koennte aus einem ganz anderen Grund scheitern. Der Test
    # soll ueber den Vertrag sprechen, nicht ueber die Importkette.
    assert (CORE / "contracts").is_dir(), (
        "core/contracts/ existiert nicht. core/compliance.py:244 liefert heute nur "
        "`-> bool`; es gibt keinen ComplianceDecision-Datensatz, den eine Order tragen "
        "koennte. Wird durch #3378 hergestellt."
    )


@when(
    "ein Zyklus Einstieg, Stop, Verdraengung, Notverkauf, Breaker und Strategiewechsel ausloest"
)
def cycle_runs():
    pytest.fail(
        "Kein Zyklus-Harness ohne den Vertrag aus #3378 — dieser Schritt wird mit dem "
        "Gateway aus #3379 ausformuliert."
    )


@then(
    "traegt jede Broker-Order eine ComplianceDecision mit Grund-Code und Halt-Zustand"
)
def every_order_has_a_decision():
    raise AssertionError("noch nicht erreichbar")


@then("es existiert keine Broker-Order ohne zugehoerigen OrderIntent")
def no_order_without_intent():
    raise AssertionError("noch nicht erreichbar")


# ---------------------------------------------------------------------------
# Szenario 2 — genau ein Broker-Aufrufer
# ---------------------------------------------------------------------------


@given("der Kern ist unveraendert")
def core_unchanged():
    assert CORE.is_dir(), f"Kernverzeichnis nicht gefunden: {CORE}"


@when(
    "die Broker-mutierenden Aufrufstellen gezaehlt werden", target_fixture="call_sites"
)
def count_call_sites():
    return _broker_call_sites()


@then("fuehrt genau eine Stelle Orders an den Broker aus")
def exactly_one_broker_caller(call_sites):
    sim = [s for s in call_sites if "simulation_runner" in s]
    prod = [s for s in call_sites if s not in sim]
    assert len(call_sites) == 1, (
        f"{len(call_sites)} broker-mutierende Aufrufstellen im Kern statt einer "
        f"({len(prod)} produktiv, {len(sim)} im Sim-Laeufer). Solange es mehr als eine "
        "gibt, kann keine Pruefung vollstaendig sein — genau das ist der Befund von "
        "#3366. Wird durch #3379 auf eine reduziert; der Sim-Laeufer geht mit #3398 "
        "(BrokerPort) ueber denselben Weg.\n  " + "\n  ".join(call_sites)
    )
