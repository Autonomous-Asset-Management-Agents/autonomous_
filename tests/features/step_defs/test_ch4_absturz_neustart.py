"""CH-4 — Absturz und Neustart (#3384 fuer Epic #3367).

**Dieser Test ist heute ROT und soll es sein.** Er scheitert an einer gemessenen
Tatsache, nicht an fehlender Vorrichtung: Die Vorrichtung hat eigene, gruene
Pruefungen in ``tests/unit/test_kette_vorrichtungen.py``.

Der Grund fuer Rot ist ein anderer als im Plan von #3384 angenommen — das ist
nachgeprueft und in #3384 als Belegkorrektur dokumentiert:

* Der Plan nennt ``order_executor.py:1650-1656`` und „ein frisches ``uuid4`` beim
  Wiederholungsversuch". Das ist seit #3387 nicht mehr wahr: ``:56-79`` leitet den
  Schluessel aus der Entscheidung ab, ``:1113-1123`` benutzt ``next_attempt``, und die
  zitierten Zeilen tragen heute die Size-0-Behandlung.
* Rot ist es eine Ebene tiefer: ``cloud_logger.py:82`` gibt **jedem**
  ``DecisionContext`` eine frische ``decision_id``. Der Neustart faellt eine neue
  Entscheidung, leitet daraus einen anderen Schluessel ab, und der Broker nimmt beide
  Orders an.

Gruen wird er durch die **Outbox aus #3388** — sie schreibt den Intent samt seiner
``decision_id`` fest, *bevor* gesendet wird. Ohne Bearbeitung dieses Tests: Die
Vorrichtung versucht bei jedem Start, einen offenen Intent wiederherzustellen
(``tests/chain/_ein_intent.py``), und sobald ``core.outbox`` existiert, greift dieser
Zweig.
"""

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung

scenarios("../ch4_absturz_und_neustart.feature")

UHR = "2026-09-16 09:35"
SEED = 7

_PUNKTE = {
    "vor dem Absenden": "vor_absenden",
    "zwischen Absenden und Bestaetigung": "zwischen_absenden_und_bestaetigung",
    "nach dem Fill vor der Persistenz": "nach_fill_vor_persistenz",
}


@given("ein Order-Intent, der bis zum Broker gelangt", target_fixture="vorrichtung")
def _vorrichtung(tmp_path):
    return ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)


@when(
    parsers.parse("der Prozess {punkt} stirbt und neu startet"),
    target_fixture="laeufe",
)
def _sterben_und_neustarten(vorrichtung, punkt):
    erster = vorrichtung.lauf(toeten_bei=_PUNKTE[punkt])
    assert erster.wurde_getoetet, (
        "Die Vorrichtung hat nicht getoetet — dann misst dieses Szenario nichts. "
        f"Ausgabe: {erster.ausgabe[-1500:]}"
    )
    zweiter = vorrichtung.lauf()
    assert zweiter.rueckgabecode == 0, zweiter.ausgabe[-1500:]
    return erster, zweiter


@then("haelt der Broker genau eine Order")
def _genau_eine_order(vorrichtung):
    orders = vorrichtung.broker_orders()
    schluessel = [o["client_order_id"] for o in orders]
    assert len(orders) == 1, (
        f"Der Broker haelt {len(orders)} Orders fuer EINE Entscheidung.\n"
        f"client_order_ids: {schluessel}\n"
        "Ursache (gemessen, nicht vermutet): cloud_logger.py:82 gibt jedem "
        "DecisionContext eine frische decision_id. Der Neustart faellt eine neue "
        "Entscheidung, der abgeleitete Schluessel (order_executor.py:56-79) ist damit "
        "ein anderer, und der Broker kann die Wiederholung nicht als solche erkennen. "
        "Ein stabiler Schluessel nuetzt nichts, wenn die Entscheidung dahinter den "
        "Neustart nicht ueberlebt — das behebt die Outbox aus #3388."
    )


@then("beide Sendungen tragen denselben Idempotenz-Schluessel")
def _gleicher_schluessel(vorrichtung):
    schluessel = {o["client_order_id"] for o in vorrichtung.broker_orders()}
    assert len(schluessel) == 1, (
        f"Es wurden {len(schluessel)} verschiedene Idempotenz-Schluessel gesendet: "
        f"{sorted(schluessel)}\n"
        "Beide leiten sich korrekt aus ihrer jeweiligen Entscheidung ab (#3387) — es "
        "sind nur zwei verschiedene Entscheidungen fuer denselben Sachverhalt. Die "
        "Wiederaufnahme muss die ERSTE Entscheidung wiederfinden, statt eine neue zu "
        "faellen (#3388, Outbox)."
    )
