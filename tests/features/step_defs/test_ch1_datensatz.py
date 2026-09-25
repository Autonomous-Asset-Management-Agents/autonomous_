"""CH-1 — zu jedem Fill ein Datensatz (#3384 fuer Epic #3367).

**Heute ROT, und das ist der Auftrag.** Der Befund ist gemessen: Stirbt der Prozess
zwischen Absenden und Bestaetigung, hat der Broker die Order (und fuellt sie), waehrend
auf unserer Seite kein Datensatz entsteht — die Broker-Order-ID wird erst *nach* der
Rueckmeldung gesetzt (``order_executor.py:1955-1958``). Der Fill ist dann Geld, das
sich bewegt hat, ohne dass unsere Buchhaltung davon weiss.

Das letzte Szenario ist der **Kontrollfall** und heute gruen: ohne Absturz stimmt die
Buchhaltung. Ohne ihn waere nicht zu unterscheiden, ob die Vorrichtung einen echten
Befund zeigt oder schlicht immer scheitert.

Gruen werden die ersten beiden durch die Outbox (#3388) und das Nachtragen im
Reconciler (#3389).
"""

from pytest_bdd import given, parsers, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung

scenarios("../ch1_datensatz_vollstaendigkeit.feature")

UHR = "2026-09-16 09:35"
SEED = 7

_PUNKTE = {
    "zwischen Absenden und Bestaetigung": "zwischen_absenden_und_bestaetigung",
    "nach dem Fill vor der Persistenz": "nach_fill_vor_persistenz",
}


@given("ein Order-Intent, der bis zum Broker gelangt", target_fixture="vorrichtung")
def _vorrichtung(tmp_path):
    return ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)


@when(parsers.parse("der Prozess {punkt} stirbt und neu startet"))
def _sterben_und_neustarten(vorrichtung, punkt):
    erster = vorrichtung.lauf(toeten_bei=_PUNKTE[punkt])
    assert erster.wurde_getoetet, (
        "Die Vorrichtung hat nicht getoetet — dann misst dieses Szenario nichts. "
        f"Ausgabe: {erster.ausgabe[-1500:]}"
    )
    zweiter = vorrichtung.lauf()
    assert zweiter.rueckgabecode == 0, zweiter.ausgabe[-1500:]


@when("der Lauf ohne Absturz durchlaeuft")
def _ohne_absturz(vorrichtung):
    lauf = vorrichtung.lauf()
    assert lauf.rueckgabecode == 0, lauf.ausgabe[-1500:]


@then("gibt es zu jeder Order des Brokers genau einen Datensatz auf unserer Seite")
def _je_order_ein_datensatz(vorrichtung):
    orders = vorrichtung.broker_orders()
    saetze = vorrichtung.unsere_saetze()
    bekannt = {s.get("client_order_id") for s in saetze}
    unbekannt = [
        o["client_order_id"] for o in orders if o["client_order_id"] not in bekannt
    ]

    assert not unbekannt, (
        f"Der Broker haelt {len(orders)} Orders, unsere Seite kennt {len(saetze)} davon.\n"
        f"Ohne Datensatz: {unbekannt}\n"
        "Diese Orders sind beim Broker angekommen und gefuellt worden, waehrend auf "
        "unserer Seite nichts entstanden ist: Die Broker-Order-ID wird erst NACH der "
        "Rueckmeldung gesetzt (order_executor.py:1955-1958), es gibt also keinen "
        "Zwischenstand, den ein Absturz hinterlassen koennte. Behoben wird das durch "
        "die Outbox (#3388) und das Nachtragen im Reconciler (#3389)."
    )


@then("traegt jeder Datensatz auf unserer Seite eine Broker-Order-ID")
def _jeder_satz_mit_broker_id(vorrichtung):
    ohne = [s for s in vorrichtung.unsere_saetze() if not s.get("alpaca_order_id")]
    assert not ohne, (
        f"{len(ohne)} Datensaetze ohne Broker-Order-ID: {ohne}\n"
        "Ein Datensatz ohne Broker-Order-ID ist nicht gegen die Broker-Seite "
        "abgleichbar — der Reconciler (#3389) kann ihn keiner Order zuordnen."
    )
