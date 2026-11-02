"""#3384 (ARC-E2.1) — die Vorrichtungen pruefen sich selbst. **Diese Tests sind gruen.**

Der Plan stellt die Reihenfolge ausdruecklich auf den Kopf (§7): Erst die Vorrichtungen
und zwar gruen, dann die Abnahmeszenarien und zwar rot. Der Grund ist einfach — solange
diese Tests hier rot sind, misst keiner der Abnahmetests etwas, und ein rotes CH-4 waere
nicht zu unterscheiden von einer kaputten Vorrichtung.

Der wichtigste Fall in dieser Datei ist ``test_toetung_ist_hart_atexit_laeuft_nicht``.
Er macht aus „der Prozess wird hart beendet" eine **Messung** statt einer Behauptung:
Der Unterprozess meldet ueber ``atexit`` ein sauberes Ende an. Laeuft der Hook, war der
Tod nicht hart — und die Vorrichtung wuerde den freundlichen Fall pruefen, vor dem der
Plan in §4 warnt.

Laufzeit: Diese Tests starten echte Unterprozesse (rund 14 s je Start, gemessen). Das
ist der Preis dafuer, dass ein echter Abbruch geprueft wird und nicht eine Exception.
"""

from __future__ import annotations

import pytest

from tests.chain._ein_intent import MARKE_SAUBERES_ENDE, TOETUNGSPUNKTE
from tests.chain.chaos import ChaosVorrichtung
from tests.chain.zwei_instanzen import ZweiInstanzenVorrichtung

pytestmark = pytest.mark.unit

UHR = "2026-09-16 09:35"
SEED = 7


# ---------------------------------------------------------------------------
# Pflichtparameter — ohne sie sind zwei Laeufe nicht vergleichbar (#3376)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vorrichtung", [ChaosVorrichtung, ZweiInstanzenVorrichtung])
def test_uhr_ist_pflicht(vorrichtung, tmp_path) -> None:
    with pytest.raises(ValueError, match="Pflicht"):
        vorrichtung(tmp_path, seed=SEED, uhr="")


def test_unbekannter_toetungspunkt_ist_ein_fehler(tmp_path) -> None:
    """Ein Tippfehler im Toetungspunkt darf nicht als „nicht toeten" durchgehen."""
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    with pytest.raises(ValueError, match="Unbekannter Toetungspunkt"):
        v.lauf(toeten_bei="nach_dem_mittagessen")


# ---------------------------------------------------------------------------
# Der saubere Lauf — der Vergleichsmassstab
# ---------------------------------------------------------------------------


def test_sauberer_lauf_legt_genau_eine_order_und_meldet_sein_ende(tmp_path) -> None:
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    lauf = v.lauf()

    assert lauf.rueckgabecode == 0, lauf.ausgabe[-2000:]
    assert lauf.sauber_beendet, (
        f"{MARKE_SAUBERES_ENDE} fehlt — der Unterprozess ist nicht sauber "
        "durchgelaufen, obwohl kein Toetungspunkt gesetzt war."
    )
    assert len(v.broker_orders()) == 1
    assert len(v.unsere_saetze()) == 1

    satz = v.unsere_saetze()[0]
    assert satz["gefuellt"] is True
    assert satz[
        "alpaca_order_id"
    ], "die Broker-Order-ID hat unsere Seite nicht erreicht"
    # #3387: der Schluessel wird aus der Entscheidung abgeleitet, nicht gewuerfelt.
    assert satz["client_order_id"].endswith(satz["decision_id"])


# ---------------------------------------------------------------------------
# Der harte Tod — gemessen, nicht behauptet
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("punkt", TOETUNGSPUNKTE)
def test_toetung_ist_hart_atexit_laeuft_nicht(punkt, tmp_path) -> None:
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    lauf = v.lauf(toeten_bei=punkt)

    assert lauf.wurde_getoetet, lauf.ausgabe[-2000:]
    assert not lauf.sauber_beendet, (
        f"{MARKE_SAUBERES_ENDE} wurde geschrieben — der atexit-Hook ist gelaufen, also "
        "war der Tod nicht hart. Eine Implementierung koennte an dieser Stelle ihren "
        "Zustand 'noch schnell' wegschreiben, und die Abnahme waere gruen, waehrend der "
        "echte Absturz weiter Daten verliert (Plan §4, Option B)."
    )
    letztes = v.beobachtung()[-1]
    assert letztes == {"ereignis": "getoetet", "punkt": punkt}


def test_jeder_toetungspunkt_liegt_an_der_richtigen_stelle(tmp_path) -> None:
    """Die drei Punkte muessen sich in dem unterscheiden, was der Broker gesehen hat."""
    gesehen = {}
    for punkt in TOETUNGSPUNKTE:
        verzeichnis = tmp_path / punkt
        v = ChaosVorrichtung(verzeichnis, seed=SEED, uhr=UHR)
        v.lauf(toeten_bei=punkt)
        gesehen[punkt] = (len(v.broker_orders()), len(v.unsere_saetze()))

    assert gesehen["vor_absenden"] == (
        0,
        0,
    ), "vor dem Absenden darf der Broker nichts gesehen haben"
    assert gesehen["zwischen_absenden_und_bestaetigung"] == (1, 0), (
        "der Broker hat die Order, unsere Seite weiss nichts davon — genau dieses "
        "Fenster ist order_executor.py:1955-1958"
    )
    assert gesehen["nach_fill_vor_persistenz"] == (
        1,
        0,
    ), "der Fill ist eingetreten, unser Datensatz fehlt"


def test_neustart_arbeitet_im_selben_verzeichnis_weiter(tmp_path) -> None:
    """Wiederhergestellt werden kann nur, was auf der Platte liegt.

    Ohne diese Zusage waere jeder Neustart ein frischer Anfang und die Vorrichtung
    koennte ueber Wiederaufnahme nichts aussagen.
    """
    v = ChaosVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    v.lauf(toeten_bei="zwischen_absenden_und_bestaetigung")
    vorher = len(v.broker_orders())
    assert vorher == 1

    zweiter = v.lauf()
    assert zweiter.rueckgabecode == 0, zweiter.ausgabe[-2000:]
    # #3449: Frueher belegte „es sind MEHR Orders geworden", dass das Auftragsbuch den
    # Prozess ueberlebt — gemessen am Defekt, den CH-4 abnimmt (die zweite Order). Mit der
    # Outbox bleibt es bei einer. Die Aussage dieses Tests ist unveraendert; belegt wird
    # sie jetzt staerker: Der zweite Lauf stellt den Intent des ersten aus DESSEN
    # Verzeichnis wieder her.
    assert (
        len(v.broker_orders()) >= vorher
    ), "das Auftragsbuch des ersten Laufs ist dem zweiten nicht erhalten geblieben"
    ereignisse = [e["ereignis"] for e in v.beobachtung()]
    assert (
        ereignisse.count("intent") == 2
    ), "der zweite Prozess hat nicht im selben Verzeichnis weitergearbeitet"
    assert (
        "intent_wiederhergestellt" in ereignisse
    ), "der zweite Prozess hat nichts aus dem Verzeichnis des ersten wiedergefunden"


# ---------------------------------------------------------------------------
# Zwei Instanzen
# ---------------------------------------------------------------------------


def test_zwei_instanzen_teilen_ein_auftragsbuch(tmp_path, monkeypatch) -> None:
    """Beide Prozesse handeln auf **einem** Konto — sonst waere das Szenario sinnlos.

    Gezeigt mit abgeschalteter Sperre (#3453): Mit ihr schreibt nur eine Instanz, und das
    gemeinsame Buch waere nicht von zwei getrennten zu unterscheiden.
    """
    monkeypatch.setenv("ENGINE_LEASE_ENABLED", "False")
    v = ZweiInstanzenVorrichtung(tmp_path, seed=SEED, uhr=UHR)
    erg = v.beide_starten()

    assert [i.rueckgabecode for i in erg] == [0, 0], [i.ausgabe[-1500:] for i in erg]
    # Nicht „zwei Orders": Beide teilen seit #3453 auch die Outbox. Startet eine Instanz einen
    # Augenblick spaeter, findet sie den offenen Intent der anderen, nimmt deren Entscheidung
    # auf und bekommt ueber das Duplikat (#3473) dieselbe Order zurueck — gemessen in der CI.
    # Geprueft wird darum, was „ein Buch" heisst: Beide haben eine Order bestaetigt bekommen,
    # und jede bestaetigte Order steht im gemeinsamen Buch.
    bestaetigt = [
        e["broker_order_id"]
        for e in v.beobachtung()
        if e.get("ereignis") == "bestaetigt"
    ]
    im_buch = {o["broker_order_id"] for o in v.broker_orders()}
    assert len(bestaetigt) == 2 and set(bestaetigt) <= im_buch, (
        "beide Instanzen muessen dasselbe Auftragsbuch beschreiben; zwei getrennte "
        "Buecher waeren zwei Konten und wuerden die Sperre nicht pruefen. "
        f"bestaetigt={bestaetigt}, im Buch={sorted(im_buch)}"
    )


def test_zwei_instanzen_sind_wiederholbar(tmp_path) -> None:
    """Gleicher Seed, gleiche Uhr, gleiches Ergebnis — sonst ist nichts vergleichbar."""
    ergebnisse = []
    for durchgang in ("erst", "zweit"):
        v = ZweiInstanzenVorrichtung(tmp_path / durchgang, seed=SEED, uhr=UHR)
        v.beide_starten()
        # #3453: Anzahlen, nicht der Name des Siegers — mit der Sperre gewinnt die
        # schnellere von zwei gleichzeitig gestarteten Instanzen (Owner-Entscheid 18.09.).
        ergebnisse.append(
            (len(v.broker_orders()), len(v.instanzen_die_gehandelt_haben()))
        )

    assert (
        ergebnisse[0] == ergebnisse[1]
    ), f"Streuung zwischen zwei Laeufen: {ergebnisse}"
