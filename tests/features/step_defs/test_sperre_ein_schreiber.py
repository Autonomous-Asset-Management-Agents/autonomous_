"""Sperr-Szenario — genau ein aktiver Schreiber je Konto (#3384 fuer Epic #3367).

**Heute ROT.** Zwei Prozesse auf demselben Konto setzen beide eine Order ab, weil es
keine Schreibberechtigung je Konto gibt. Der einzige Lock im Order-Pfad greift je
Nutzer **und Symbol** fuer zwoelf Sekunden (``order_executor.py:997``, ``:1002``);
auf dem Desktop ist er ein No-Op (``local_state_client.py:94-95`` gibt bedingungslos
``True`` zurueck).

Zwei **Prozesse**, nicht zwei Threads — der Plan verwirft die Thread-Variante
ausdruecklich (§4, Option C): Zwei Threads teilen sich einen Interpreter und wuerden
den No-Op-Lock gar nicht auffliegen lassen.

Das zweite Szenario ist der **Kontrollfall** und heute gruen: Die Vorrichtung liefert
bei gleicher Uhr und gleichem Seed dasselbe Ergebnis. Ohne ihn waere ein rotes erstes
Szenario nicht von Streuung zu unterscheiden (#3376: der Sim-Harness streut 6,6 pp).

Gruen wird das erste Szenario mit der Engine-Sperre aus #3390. Solange es rot ist, muss
``max-instances=1`` aus #3385 stehen bleiben — die beiden gehoeren zusammen betrachtet.
"""

from pytest_bdd import given, scenarios, then, when

from tests.chain.zwei_instanzen import ZweiInstanzenVorrichtung

scenarios("../sperre_ein_schreiber.feature")

UHR = "2026-09-16 09:35"
SEED = 7


@given("zwei Engine-Instanzen auf demselben Konto", target_fixture="konto")
def _konto(tmp_path):
    return {"verzeichnis": tmp_path, "vorrichtung": None, "wiederholung": None}


@when("beide einen Zyklus starten")
def _beide_starten(konto):
    v = ZweiInstanzenVorrichtung(konto["verzeichnis"] / "lauf", seed=SEED, uhr=UHR)
    ergebnisse = v.beide_starten()
    assert [i.rueckgabecode for i in ergebnisse] == [0, 0], [
        i.ausgabe[-1200:] for i in ergebnisse
    ]
    konto["vorrichtung"] = v


@when("beide einen Zyklus starten und der Lauf wiederholt wird")
def _beide_starten_zweimal(konto):
    messungen = []
    for durchgang in ("erst", "zweit"):
        v = ZweiInstanzenVorrichtung(
            konto["verzeichnis"] / durchgang, seed=SEED, uhr=UHR
        )
        v.beide_starten()
        # #3453, Owner-Entscheid 18.09.2026: verglichen wird die ANZAHL, nicht der Name.
        # Mit der Sperre gewinnt die schnellere von zwei gleichzeitig gestarteten Instanzen —
        # gemessen wechselnd a/b. Das ist ein Wettlauf der Prozesse, keine Streuung der
        # Engine; die Kontrolle soll Streuung erkennen, nicht den Sieger vorhersagen.
        messungen.append(
            (len(v.broker_orders()), len(v.instanzen_die_gehandelt_haben()))
        )
        konto["vorrichtung"] = v
    konto["wiederholung"] = messungen


@then("setzt genau eine Instanz eine Order ab")
def _genau_eine_instanz(konto):
    handelnde = konto["vorrichtung"].instanzen_die_gehandelt_haben()
    assert len(handelnde) == 1, (
        f"{len(handelnde)} Instanzen haben gehandelt: {handelnde}\n"
        "Es gibt keine Schreibberechtigung je Konto. Der einzige vorhandene Lock "
        "greift je Nutzer UND Symbol fuer zwoelf Sekunden (order_executor.py:997, "
        ":1002), und auf dem Desktop gibt local_state_client.py:94-95 bedingungslos "
        "True zurueck. Beide Instanzen halten sich fuer den einzigen Schreiber. "
        "Behoben wird das durch die Engine-Sperre aus #3390."
    )


@then("der Broker haelt genau eine Order")
def _broker_eine_order(konto):
    orders = konto["vorrichtung"].broker_orders()
    assert len(orders) == 1, (
        f"Der Broker haelt {len(orders)} Orders fuer einen Zyklus auf einem Konto: "
        f"{[o['client_order_id'] for o in orders]}\n"
        "Zwei Instanzen, zwei Entscheidungen, zwei Orders — doppelte Position und "
        "doppeltes Risiko auf demselben Konto (#3390)."
    )


@then("ist das Ergebnis bei Wiederholung identisch")
def _wiederholbar(konto):
    erst, zweit = konto["wiederholung"]
    assert erst == zweit, (
        f"Streuung zwischen zwei Laeufen mit gleichem Seed und gleicher Uhr: "
        f"{erst} vs. {zweit}. Ohne Determinismus ist ein rotes Sperr-Szenario nicht "
        "von Zufall zu unterscheiden (#3376)."
    )
