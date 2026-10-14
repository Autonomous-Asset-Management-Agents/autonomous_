"""CH-5 Editions-Paritaet (#3745 fuer #3397, Epic #3375).

**Was diese Datei vorher tat.** Sie schrieb zwei gleiche Woerterbuecher und prueifte,
dass sie gleich sind::

    run_context["results"]["run1"] = {"action": "BUY", "intent": "INTENT_1"}
    run_context["results"]["run2"] = {"action": "BUY", "intent": "INTENT_1"}
    assert run_context["results"]["run1"] == run_context["results"]["run2"]

Diese Zusicherung konnte nicht fehlschlagen; zwei der vier ``then``-Schritte waren leer.
Der Auftrag von #3397 lautete „rot aufsetzen" — der Test war nie rot.

**Was sie jetzt tut.** Zwei **echte Prozesslaeufe** ueber dieselbe Vorrichtung, die auch
CH-1 und CH-4 fahren (``tests/chain/chaos.py``): eigener Prozess, echter Absendepunkt
``_submit_with_market_failsafe``, echtes Auftragsbuch. Verglichen wird Feld fuer Feld
mit Einordnung (``tests/chain/vergleich.py``).

**Was sie ausdruecklich NICHT beweist.** Dass die *Enterprise*-Zusammenstellung dieselben
Entscheidungen trifft. Das ist offline nicht fahrbar, dreifach gemessen am 28.09.2026:

* Unterprozess mit ``K_SERVICE=1``: ``rc=1`` — die Datenbankschicht verweigert den
  fluechtigen SQLite-Rueckfall auf Cloud Run, ``secrets_loader`` bricht ohne Projekt-ID
  hart ab (``secrets_loader.py:76``).
* Mit gesetzter Projekt-ID: Zeitueberschreitung — der Secret-Manager-Client wartet aufs Netz.
* In-Prozess-Import des Entscheidungskerns mit ``K_SERVICE=1``: ``SystemExit(1)`` beim Import.

Das sind Schutzregeln, die arbeiten, keine Defekte. Das betreffende Szenario ist deshalb
**uebersprungen mit Begruendung** statt gruen gemeldet. Was die Paritaet heute tatsaechlich
erzwingt, ist die Vertragsregel ``editionsweiche = "blockieren"``
(``tests/architecture/vertrag.toml``): jede Editionsweiche ausserhalb der Composition Root
ist ein Befund, auf jedem Pull Request.
"""

import pytest
from pytest_bdd import given, scenario, scenarios, then, when

from tests.chain.chaos import ChaosVorrichtung
from tests.chain.vergleich import bericht, unzulaessige, vergleiche

UHR = "2026-09-16 09:35"
SEED = 7

_OFFLINE_GRUND = (
    "Die Enterprise-Zusammenstellung ist offline nicht fahrbar: ohne Projekt-ID bricht "
    "secrets_loader.py:76 hart ab, mit Projekt-ID wartet der Secret-Manager-Client aufs "
    "Netz, und die Datenbankschicht verweigert auf Cloud Run den fluechtigen "
    "SQLite-Rueckfall. Gemessen am 2026-09-28 (#3745). Die Paritaet erzwingt heute die "
    "Vertragsregel editionsweiche='blockieren': keine Editionsweiche ausserhalb der "
    "Composition Root. Dieses Szenario wird erst gruen, wenn die Abnahme eine "
    "Enterprise-Umgebung bekommt (Cloud SQL und Secret-Stub) — Owner-Entscheidung."
)


@pytest.mark.skip(reason=_OFFLINE_GRUND)
@scenario(
    "../ch5_editions_paritaet.feature", "Gleiche Eingabe, gleiches Modell, feste Uhr"
)
def test_editionen_feld_fuer_feld():
    """Uebersprungen mit Begruendung — siehe Modulkopf."""


scenarios("../ch5_editions_paritaet.feature")


# ---------------------------------------------------------------------------
# Szenario 1 — zwei echte Laeufe derselben Zusammenstellung
# ---------------------------------------------------------------------------


@given(
    "zweimal derselbe Order-Intent mit fester Uhr und festem Seed",
    target_fixture="laeufe",
)
def _zwei_laeufe(tmp_path):
    return [
        ChaosVorrichtung(tmp_path / "lauf1", seed=SEED, uhr=UHR),
        ChaosVorrichtung(tmp_path / "lauf2", seed=SEED, uhr=UHR),
    ]


@when("beide Prozesslaeufe enden", target_fixture="saetze")
def _beide_laufen(laeufe):
    saetze = []
    for nummer, vorrichtung in enumerate(laeufe, start=1):
        lauf = vorrichtung.lauf()
        assert lauf.rueckgabecode == 0, (
            f"Lauf {nummer} ist nicht sauber beendet (rc={lauf.rueckgabecode}). "
            f"Ausgabe:\n{lauf.ausgabe[-1500:]}"
        )
        eigene = vorrichtung.unsere_saetze()
        orders = vorrichtung.broker_orders()
        assert len(eigene) == 1 and len(orders) == 1, (
            f"Lauf {nummer} hat {len(eigene)} eigene Saetze und {len(orders)} Orders "
            "erzeugt — erwartet ist genau je einer. Ohne das vergleicht dieses "
            "Szenario nichts."
        )
        saetze.append({"eigen": eigene[0], "order": orders[0]})
    return saetze


@then("stimmen alle Kern-Felder ueberein")
def _kern_gleich(saetze):
    a, b = saetze
    befunde = vergleiche(a["eigen"], b["eigen"], "unsere_saetze") + vergleiche(
        a["order"], b["order"], "auftragsbuch"
    )
    schlimm = unzulaessige(befunde)
    assert not schlimm, (
        "Zwei Laeufe derselben Zusammenstellung weichen im Kern voneinander ab:\n"
        + bericht(schlimm)
        + "\n(Zulaessig sind nur Identitaets- und Adapter-Felder — "
        "Einordnung in tests/chain/vergleich.py.)"
    )


@then("unterscheiden sich die Identitaets-Felder")
def _identitaet_verschieden(saetze):
    a, b = saetze
    for feld in ("decision_id", "client_order_id"):
        assert a["eigen"][feld] != b["eigen"][feld], (
            f"Beide Laeufe tragen dieselbe {feld} ({a['eigen'][feld]!r}). Eine "
            "wiederverwendete Identitaet waere der Fehler: zwei Entscheidungen mit "
            "derselben Kennung sind im Audit nicht zu trennen."
        )


@then("bleibt die client_order_id aus der decision_id abgeleitet")
def _coid_abgeleitet(saetze):
    for satz in saetze:
        eigen = satz["eigen"]
        assert eigen["client_order_id"].endswith(eigen["decision_id"]), (
            f"client_order_id {eigen['client_order_id']!r} traegt nicht die "
            f"decision_id {eigen['decision_id']!r} — die Ableitung aus #3387 ist "
            "gebrochen, und der Reconciler kann die Order keiner Entscheidung zuordnen."
        )


# ---------------------------------------------------------------------------
# Szenarien 3 und 4 — der Vergleich selbst muss Feld und Einordnung nennen
# ---------------------------------------------------------------------------


@given("zwei Saetze, die sich im Symbol unterscheiden", target_fixture="paar")
def _paar_kern():
    return (
        {"symbol": "AAPL", "side": "buy", "alpaca_order_id": "A"},
        {"symbol": "MSFT", "side": "buy", "alpaca_order_id": "A"},
    )


@given(
    "zwei Saetze, die sich nur in der Broker-Order-ID unterscheiden",
    target_fixture="paar",
)
def _paar_adapter():
    return (
        {"symbol": "AAPL", "side": "buy", "alpaca_order_id": "A"},
        {"symbol": "AAPL", "side": "buy", "alpaca_order_id": "B"},
    )


@when("der Vergleich laeuft", target_fixture="befunde")
def _vergleich(paar):
    links, rechts = paar
    return vergleiche(links, rechts, "auftragsbuch")


@then("nennt er Modul und Feld der Abweichung")
def _nennt_modul_und_feld(befunde):
    assert befunde, "Der Vergleich hat die Abweichung gar nicht gefunden."
    text = bericht(befunde)
    assert "auftragsbuch" in text, f"Das Modul fehlt in der Meldung:\n{text}"
    assert "symbol" in text, f"Das Feld fehlt in der Meldung:\n{text}"


@then("weist sie als unzulaessig aus")
def _weist_unzulaessig_aus(befunde):
    schlimm = unzulaessige(befunde)
    assert [a.feld for a in schlimm] == ["symbol"], bericht(befunde)
    assert schlimm[0].einordnung == "KERN", bericht(befunde)


@then("meldet er keine unzulaessige Abweichung")
def _keine_unzulaessige(befunde):
    assert befunde, "Der Vergleich hat die Adapter-Abweichung gar nicht gesehen."
    assert not unzulaessige(befunde), bericht(befunde)
    assert befunde[0].einordnung == "ADAPTER", bericht(befunde)
