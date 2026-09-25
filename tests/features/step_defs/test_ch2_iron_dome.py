"""CH-2 — Vollstaendigkeit des Iron Dome (#3377 fuer Epic #3366).

Dieser Test ist heute ROT und soll es sein. Der Plan (§7) verlangt ausdruecklich, dass
er *inhaltlich* scheitert: ein ``ModuleNotFoundError`` gilt als NICHT erfuellt, weil er
nichts ueber das System aussagt. Deshalb pruefen die Schritte die Abwesenheit des
Vertrags als Tatsache und melden sie als Zusicherungsfehler mit Beleg.

Gruen wird er durch #3378 (Vertraege) und #3379 (Gateway).
"""

import ast
from pathlib import Path

import pytest
from pytest_bdd import given, scenarios, then, when

scenarios("../ch2_iron_dome_completeness.feature")


CORE = Path(__file__).resolve().parents[3] / "core"

#: Methoden, deren Aufruf eine Order am Broker mutiert.
#:
#: ``_submit_order_safe`` stand hier, solange er den Broker selbst rief. Seit #3447 Schritt 3
#: fuehrt er durchs Tor — er ist ein Weg ZUM Tor, keiner daran vorbei. Seine vier Aufrufer
#: wurden bis dahin mitgezaehlt, obwohl sie nur diese eine Funktion rufen.
_METHODEN = frozenset({"submit_order", "close_all_positions", "close_position"})

#: Dieselben Methoden, wenn sie als *Argument* an ``asyncio.to_thread`` gereicht werden —
#: ``await asyncio.to_thread(client.submit_order, req)``. Diese Form fiel durch das
#: urspruengliche Textmuster; sieben echte Aufrufe waren dadurch unsichtbar, bis jemand
#: das Muster von Hand erweiterte (#3366, Korrektur vom 16.09.2026).
_THREAD_METHODEN = frozenset({"submit_order", "close_position"})

_SKIP_DIRS = ("sim", "research")


def _aufrufstellen_in_quelle(quelltext: str, rel: str):
    """Die broker-mutierenden Aufrufe einer Datei — ueber den Syntaxbaum, nicht ueber Text.

    **Warum AST und nicht Regex** (#3447, Schritt 1): Eine Textsuche kann einen Aufruf
    nicht von seiner Erwaehnung unterscheiden. Sie zaehlte darum ``lstm_strategy.py:76``
    mit — eine Docstring-Zeile („Nutzt BaseStrategy._submit_order_safe (…)"), in der
    nichts aufgerufen wird. Der alte Filter uebersprang nur Zeilen, die mit ``def``,
    ``async def`` oder ``#`` beginnen; Docstring-Ruempfe fielen durch.

    Der Syntaxbaum kennt den Unterschied ohne Sonderregel: Eine Zeichenkette ist ein
    ``ast.Constant``, ein Aufruf ein ``ast.Call``. Dieselbe Umstellung hat in #3418
    bereits einen gleichartigen Fehlalarm beseitigt.

    Eine Datei, die sich nicht parsen laesst, liefert **keine** Treffer — und das ist
    hier die richtige Ausfallrichtung nicht: Sie wuerde eine Umgehung verstecken. Darum
    wird der Syntaxfehler weitergereicht, nicht geschluckt.
    """
    baum = ast.parse(quelltext, filename=rel)

    zeilen = set()
    for knoten in ast.walk(baum):
        if not isinstance(knoten, ast.Call):
            continue

        # 1) Unmittelbarer Aufruf: ``irgendwas.submit_order(...)``
        if isinstance(knoten.func, ast.Attribute) and knoten.func.attr in _METHODEN:
            zeilen.add(knoten.lineno)
            continue

        # 2) Ueber einen Thread gereicht: ``to_thread(irgendwas.submit_order, ...)``.
        #    Hier wird die Methode nicht aufgerufen, sondern uebergeben — der Broker
        #    sieht die Order trotzdem.
        aufgerufen = knoten.func
        name = (
            aufgerufen.attr
            if isinstance(aufgerufen, ast.Attribute)
            else getattr(aufgerufen, "id", "")
        )
        if name == "to_thread" and knoten.args:
            erstes = knoten.args[0]
            if isinstance(erstes, ast.Attribute) and erstes.attr in _THREAD_METHODEN:
                zeilen.add(knoten.lineno)

    return [f"core/{rel}:{z}" for z in sorted(zeilen)]


def _ist_sim_zweig(knoten) -> bool:
    """``if is_simulation:`` — der Zweig, den nur ein Simulations-Client nimmt."""
    test = knoten.test
    name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", "")
    return name == "is_simulation"


def _sim_zeilen_in_quelle(quelltext: str, rel: str):
    """Aufrufstellen in einem ``if is_simulation:``-Zweig — getrennt ausgewiesen, nicht verschwiegen.

    Sie bewegen kein Kapital: ``is_simulation`` ist wahr nur fuer Clients mit
    ``simulation_data`` oder „Simulation" im Typnamen (``core/strategies/base.py``). Durchs
    Tor geleitet, schrieben Backtests tausende Pseudo-Entscheidungen in dieselbe Senke wie
    echte Orders. Sie ziehen mit dem ``BrokerPort`` nach (#3398) — Owner-Entscheid vom
    18.09.2026 zum Schliesskriterium von #3366.
    """
    baum = ast.parse(quelltext, filename=rel)
    zeilen = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.If) and _ist_sim_zweig(knoten):
            for kind in knoten.body:
                for unter in ast.walk(kind):
                    if isinstance(unter, ast.Call):
                        zeilen.add(unter.lineno)
    alle = set(_aufrufstellen_in_quelle(quelltext, rel))
    return [s for s in alle if int(s.rsplit(":", 1)[1]) in zeilen]


def _sim_call_sites():
    """Sim-Stellen: der Sim-Laeufer und jeder ``if is_simulation:``-Zweig im Kern."""
    sites = []
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE).as_posix()
        if rel.startswith(_SKIP_DIRS) or "test" in rel:
            continue
        quelle = path.read_text(encoding="utf-8-sig", errors="replace")
        if "simulation_runner" in rel:
            sites.extend(_aufrufstellen_in_quelle(quelle, rel))
        else:
            sites.extend(_sim_zeilen_in_quelle(quelle, rel))
    return sorted(sites)


def _broker_call_sites():
    """Alle Stellen im Kern, die eine Order am Broker mutieren."""
    sites = []
    for path in sorted(CORE.rglob("*.py")):
        rel = path.relative_to(CORE).as_posix()
        if rel.startswith(_SKIP_DIRS) or "test" in rel:
            continue
        # `utf-8-sig` statt `utf-8`: Mindestens eine Datei im Kern traegt eine
        # Byte-Order-Mark (`core/keychain.py`). Der Textleser gibt sie als Zeichen
        # U+FEFF durch, und `ast.parse` bricht daran ab. Der Textfilter von frueher
        # bemerkte das nie, weil er nie geparst hat.
        sites.extend(
            _aufrufstellen_in_quelle(
                path.read_text(encoding="utf-8-sig", errors="replace"), rel
            )
        )
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


#: Die sechs Bewegungsarten, die der Epic nennt. `strategy_switch` steht fuer den
#: Strategiewechsel, `panic` fuer den Notverkauf.
_KINDS = ("entry", "stop", "displacement", "panic", "breaker", "strategy_switch")


@when(
    "ein Zyklus Einstieg, Stop, Verdraengung, Notverkauf, Breaker und Strategiewechsel ausloest",
    target_fixture="zyklus",
)
def cycle_runs():
    """#3383: ausformuliert, seit das Tor aus #3379 steht und die drei Ausnahmepfade
    (#3383) hindurchgehen. Es laeuft ein echtes Tor mit einem Attrappen-Broker — kein
    Netz, keine Konfiguration.
    """
    from core.contracts import OrderIntent
    from core.gateway import OrderGateway

    gesendet, entscheidungen = [], []
    tor = OrderGateway(
        broker=type("B", (), {"submit_order": lambda self, r: gesendet.append(r)})(),
        is_halted=lambda user_id=None: False,
        record=entscheidungen.append,
    )

    for i, kind in enumerate(_KINDS):
        tor.submit(
            OrderIntent(
                decision_id=f"d-{i}",
                symbol="AAPL",
                side="sell" if kind != "entry" else "buy",
                qty=1.0,
                intent_kind=kind,
                is_protective_exit=kind == "stop",
                halted=False,
            ),
            request={"symbol": "AAPL"},
        )

    return {"gesendet": gesendet, "entscheidungen": entscheidungen}


@then(
    "traegt jede Broker-Order eine ComplianceDecision mit Grund-Code und Halt-Zustand"
)
def every_order_has_a_decision(zyklus):
    assert len(zyklus["entscheidungen"]) == len(
        _KINDS
    ), f"{len(zyklus['entscheidungen'])} Entscheidungen fuer {len(_KINDS)} Bewegungen"
    for e in zyklus["entscheidungen"]:
        assert e.reason_code is not None
        assert isinstance(e.halted, bool)

    # Die Freistellungen tragen ihren EIGENEN Grund-Code (#3383) — sonst waere ein
    # Notverkauf im Audit nicht von einem Schutz-Stop zu unterscheiden.
    codes = {e.reason_code.value for e in zyklus["entscheidungen"]}
    for erwartet in ("emergency", "breaker", "strategy_switch"):
        assert erwartet in codes, f"Grund-Code {erwartet} fehlt: {sorted(codes)}"


@then("es existiert keine Broker-Order ohne zugehoerigen OrderIntent")
def no_order_without_intent():
    """Die Umkehrprobe zum Schritt darueber: solange Aufrufstellen am Tor vorbeigehen,
    kann es Broker-Orders ohne Intent geben — unabhaengig davon, wie sauber die Orders
    aussehen, die durch das Tor laufen."""
    stellen = _broker_call_sites()
    sim = set(_sim_call_sites())
    vorbei = [s for s in stellen if not s.startswith("core/gateway/") and s not in sim]
    assert not vorbei, (
        f"{len(vorbei)} Aufrufstellen koennen eine Broker-Order ohne OrderIntent "
        "erzeugen:\n  " + "\n  ".join(vorbei)
    )


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
    """Gezaehlt wird, was AM TOR VORBEI geht.

    Das Gateway selbst ruft den Broker — es ist die eine erlaubte Stelle (#3379). Wuerde
    man es mitzaehlen, bliebe die Zahl beim Umhaengen konstant und der Test waere kein
    Fortschrittsmass mehr. Die Ausnahmeliste ist namentlich gefuehrt und waechst nur mit
    Begruendung im PR (Plan §4).
    """
    tor = [s for s in call_sites if s.startswith("core/gateway/")]
    sim = [s for s in call_sites if s in set(_sim_call_sites())]
    vorbei = [s for s in call_sites if s not in tor and s not in sim]

    assert len(tor) == 1, (
        f"Das Gateway muss genau einen Broker-Aufruf enthalten, gefunden: {len(tor)} "
        f"({tor}). Mehr als einer heisst: es gibt auch im Tor mehrere Wege hinaus."
    )
    assert not vorbei, (
        f"{len(vorbei)} broker-mutierende Aufrufstellen gehen am Tor vorbei "
        f"(dazu {len(sim)} Sim-Stellen, die mit #3398 ueber den BrokerPort nachziehen: "
        f"{sim}). "
        "Solange es mehr als null sind, kann keine Pruefung vollstaendig sein — genau "
        "das ist der Befund von #3366. Jede umgehaengte Stelle senkt diese Zahl.\n  "
        + "\n  ".join(vorbei)
    )
