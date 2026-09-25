"""#3384 — ein Entscheidungsdurchlauf in einem eigenen Prozess, an drei Punkten toetbar.

Dieses Modul ist der **Unterprozess** der Chaos-Vorrichtung. Es faehrt genau einen
Order-Intent bis zum Broker und legt dabei an den drei Punkten des Plans einen harten
Tod ein:

* ``vor_absenden``
* ``zwischen_absenden_und_bestaetigung``
* ``nach_fill_vor_persistenz``

**Was hier echt ist und was nicht — damit im Review niemand mehr annimmt, als belegt ist.**

*Echt* (Produktionscode, unveraendert benutzt):

* ``core.cloud_logger.DecisionContext`` — die Entscheidung samt ihrer ``decision_id``.
* ``core.engine.order_executor._derived_coid`` — die Ableitung des ``client_order_id``
  aus der Entscheidung (#3387).
* ``core.engine.order_executor.OrderExecutorMixin._submit_with_market_failsafe`` — der
  tatsaechliche Absendepunkt (``order_executor.py:1102``). Er wird ungebunden
  aufgerufen, weil er ``self`` nachweislich nicht benutzt (Kontrollgrep ueber
  ``:1082-1128``: kein ``self.``).
* ``core.sim.broker.VirtualLiveBroker`` samt echter ``SimFillEngine``.

*Nicht* gefahren wird ``_execute_tenant_order`` (1200 Zeilen mit Compliance, Risiko,
PortfolioManager, HITL). Das ist Absicht: gemessen werden soll das Verhalten bei
Absturz und Neustart, nicht die Entscheidungslogik. Was diese Vorrichtung darum
**nicht** beweist, steht im Walkthrough.

*Nachgebildet* ist der Broker-seitige Teil: dauerhaftes Auftragsbuch und Abweisung
doppelter ``client_order_id`` (siehe ``ledger_broker.py``).
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

TOETUNGSPUNKTE = (
    "vor_absenden",
    "zwischen_absenden_und_bestaetigung",
    "nach_fill_vor_persistenz",
)

_DATEN = "KETTE_DATENVERZEICHNIS"
_TOETEN = "KETTE_TOETEN_BEI"

#: Beweist, dass der Tod hart war. ``os._exit`` laesst ``atexit`` NICHT laufen; ein
#: sauberer Lauf schreibt die Marke, ein getoeteter nicht. Die Vorrichtung prueft das
#: in ihrem eigenen Test — sonst waere „hart" eine Behauptung.
MARKE_SAUBERES_ENDE = "sauberes_ende.marke"


def _verzeichnis() -> Path:
    pfad = os.environ.get(_DATEN)
    if not pfad:
        raise SystemExit(
            f"{_DATEN} ist nicht gesetzt — die Vorrichtung ruft falsch auf."
        )
    p = Path(pfad)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _protokoll(verzeichnis: Path, ereignis: str, **felder) -> None:
    """Beobachtungsspur der Vorrichtung — nicht der Zustand des Systems.

    Wichtig fuer die Auswertung: Hier steht, was der *Testbeobachter* gesehen hat.
    Was das System selbst dauerhaft weiss, steht ausschliesslich in
    ``unsere_saetze.jsonl`` (unsere Seite) und im Auftragsbuch (Broker-Seite).
    """
    with (verzeichnis / "beobachtung.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ereignis": ereignis, **felder}, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _toete(verzeichnis: Path, punkt: str) -> None:
    """Harter Tod: kein ``finally``, kein ``atexit``, kein Puffer-Abgleich.

    ``os._exit`` statt eines Signals von aussen. Der Plan verlangt in §4 einen echten
    Abbruch und verwirft ausdruecklich die Exception-Variante, weil die den
    ``finally``-Pfad durchlaeuft — dort wuerde eine Implementierung den Zustand „noch
    schnell" wegschreiben und der Test waere gruen, waehrend der echte Absturz weiter
    Daten verliert. ``os._exit`` hat genau die geforderte Haerte und verhaelt sich auf
    Windows und Linux gleich, was ``SIGKILL`` nicht tut (§8 laesst den Signalweg
    ausdruecklich offen). Die Haerte ist gemessen, nicht behauptet: siehe
    ``MARKE_SAUBERES_ENDE``.
    """
    _protokoll(verzeichnis, "getoetet", punkt=punkt)
    os._exit(137)


def _preis_datenclient(symbol: str, preis: float):
    """Ein Balken je Symbol — dasselbe Muster wie ``test_sim_broker_order_types.py:35-44``."""

    class _FesterPreis:
        def get_stock_bars(self, request):
            return SimpleNamespace(data={symbol: [SimpleNamespace(close=preis)]})

    return _FesterPreis()


def _broker(verzeichnis: Path, symbol: str, preis: float):
    """Sim-Broker ohne Korpus, mit dauerhaftem Auftragsbuch.

    Der Datenclient wird **vor** der Konstruktion getauscht, damit ``SimDataClient``
    den 504-Symbol-Korpus gar nicht erst laedt (zweimal rund zwei Sekunden je Prozess).
    Die echte ``SimFillEngine`` bleibt in Betrieb.
    """
    from core.sim import broker as broker_modul
    from core.sim import fill_engine as fill_modul

    fest = _preis_datenclient(symbol, preis)
    broker_modul.SimDataClient = lambda *a, **k: fest  # type: ignore[assignment]
    fill_modul.SimDataClient = lambda *a, **k: fest  # type: ignore[assignment]

    inner = broker_modul.VirtualLiveBroker()
    inner.fill_engine.data_client = fest
    inner.data_client = fest

    from tests.chain.ledger_broker import LedgerBroker

    return LedgerBroker(verzeichnis / "auftragsbuch.jsonl", inner)


def _entscheidung(verzeichnis: Path, symbol: str):
    """Die Entscheidung dieses Durchlaufs — wiederhergestellt, wenn das moeglich ist.

    Heute ist es nicht moeglich: eine Outbox gibt es nicht (Kontrollgrep ``outbox``
    ueber den Python-Bestand: kein Treffer). Jeder Durchlauf erzeugt darum eine neue
    Entscheidung mit neuer ``decision_id`` — und genau das ist das Verhalten, das CH-4
    heute rot macht.

    Der Wiederherstellungsversuch steht trotzdem hier und nicht als Kommentar: Sobald
    #3388 ``core.outbox`` liefert, greift dieser Zweig, und CH-4 wird gruen **ohne dass
    dieser Test angefasst werden muss**. Ein Abnahmetest, den man zum Gruenwerden
    editieren muss, nimmt nichts ab.
    """
    try:
        from core.outbox import offener_intent  # type: ignore[attr-defined]
    except Exception:
        offener_intent = None

    if offener_intent is not None:
        wieder = offener_intent(symbol=symbol, datenverzeichnis=str(verzeichnis))
        if wieder is not None:
            _protokoll(
                verzeichnis, "intent_wiederhergestellt", decision_id=wieder.decision_id
            )
            return wieder

    from core.cloud_logger import DecisionContext

    kontext = DecisionContext(symbol=symbol)
    _protokoll(verzeichnis, "entscheidung_neu", decision_id=kontext.decision_id)
    return kontext


def _berechtigung(verzeichnis: Path, instanz: str):
    """Darf **diese** Instanz schreiben?

    Heute darf jede: eine Schreibberechtigung je Konto gibt es nicht. Der einzige
    vorhandene Lock im Order-Pfad greift je Nutzer **und Symbol** fuer zwoelf Sekunden
    (``order_executor.py:997``, ``:1002``), und auf dem Desktop ist er ein No-Op
    (``local_state_client.py:94-95`` gibt bedingungslos ``True`` zurueck).

    Wie beim Outbox-Haken steht der Versuch hier und nicht als Kommentar: Sobald #3390
    eine Berechtigung liefert, greift dieser Zweig und das Sperr-Szenario wird gruen,
    **ohne dass dieser Test angefasst werden muss**.
    """
    try:
        from core.engine.lease import erwerbe_schreibberechtigung  # type: ignore
    except Exception:
        _protokoll(verzeichnis, "berechtigung_nicht_vorhanden", instanz=instanz)
        return True

    hat = bool(erwerbe_schreibberechtigung(konto="kette", instanz=instanz))
    _protokoll(verzeichnis, "berechtigung", instanz=instanz, erhalten=hat)
    _gleichzeitig(verzeichnis, instanz)
    return hat


def _gleichzeitig(verzeichnis: Path, instanz: str) -> None:
    """Zwei-Instanzen-Modus: warten, bis alle Instanzen ihre Berechtigung erfragt haben.

    Das Sperr-Szenario prueft zwei **gleichzeitig laufende** Instanzen. Ohne diesen Treffpunkt
    konnte die erste schon fertig und beendet sein, bevor die zweite fragte — dann ist sie
    nachweislich tot, die zweite uebernimmt zu Recht (#3453) und nimmt ueber die Outbox deren
    Entscheidung auf. Das ist korrekt, aber nicht das Szenario: gemessen 2 von 8 Laeufen.
    Begrenzt auf 60 s; wer den Treffpunkt nicht erreicht, faellt in der Abnahme auf.
    """
    import time

    anzahl = int(os.environ.get("KETTE_GLEICHZEITIG", "0") or 0)
    if anzahl < 2:
        return
    (verzeichnis / f"treffpunkt_{instanz}.marke").write_text("ok", encoding="utf-8")
    frist = time.monotonic() + 60.0
    while time.monotonic() < frist:
        if len(list(verzeichnis.glob("treffpunkt_*.marke"))) >= anzahl:
            return
        time.sleep(0.05)
    _protokoll(verzeichnis, "treffpunkt_verpasst", instanz=instanz)


def main() -> int:
    verzeichnis = _verzeichnis()
    atexit.register(
        lambda: (verzeichnis / MARKE_SAUBERES_ENDE).write_text("ok", encoding="utf-8")
    )

    toeten_bei = os.environ.get(_TOETEN, "").strip()
    if toeten_bei and toeten_bei not in TOETUNGSPUNKTE:
        raise SystemExit(f"Unbekannter Toetungspunkt {toeten_bei!r}")

    symbol = os.environ.get("KETTE_SYMBOL", "AAPL")
    preis = float(os.environ.get("KETTE_PREIS", "100.0"))
    menge = float(os.environ.get("KETTE_MENGE", "1"))

    instanz = os.environ.get("KETTE_INSTANZ", "einzeln")
    if not _berechtigung(verzeichnis, instanz):
        _protokoll(
            verzeichnis, "nicht_gehandelt", instanz=instanz, grund="keine Berechtigung"
        )
        return 0

    broker = _broker(verzeichnis, symbol, preis)
    kontext = _entscheidung(verzeichnis, symbol)

    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.engine.order_executor import OrderExecutorMixin, _derived_coid

    client_order_id = _derived_coid(kontext, "entry", 0)
    anfrage = MarketOrderRequest(
        symbol=symbol,
        qty=menge,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )
    _protokoll(verzeichnis, "intent", client_order_id=client_order_id)

    if toeten_bei == "vor_absenden":
        _toete(verzeichnis, toeten_bei)

    import asyncio

    async def _absenden():
        return await OrderExecutorMixin._submit_with_market_failsafe(
            None,
            client=broker,
            primary_req=anfrage,
            symbol=symbol,
            qty=menge,
            side_enum=OrderSide.BUY,
            user_id="kette",
            is_exit=False,
        )

    try:
        order = asyncio.run(_absenden())
    except Exception as exc:
        # Ein abgewiesenes Duplikat ist kein Fehler der Vorrichtung, sondern das
        # erwuenschte Ergebnis, sobald die Wiederaufnahme denselben Schluessel bildet.
        _protokoll(
            verzeichnis, "absenden_abgewiesen", grund=type(exc).__name__, text=str(exc)
        )
        return 0

    if toeten_bei == "zwischen_absenden_und_bestaetigung":
        # Der Broker hat die Order (das Auftragsbuch ist bereits mit fsync geschrieben);
        # wir erfahren die Broker-Order-ID nie. Das ist das Fenster aus
        # order_executor.py:1955-1958.
        _toete(verzeichnis, toeten_bei)

    kontext.alpaca_order_id = str(order.id)
    _protokoll(verzeichnis, "bestaetigt", broker_order_id=str(order.id))

    gefuellt = str(getattr(order, "status", "")).lower().endswith("filled")
    if gefuellt and toeten_bei == "nach_fill_vor_persistenz":
        _toete(verzeichnis, toeten_bei)

    with (verzeichnis / "unsere_saetze.jsonl").open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "decision_id": kontext.decision_id,
                    "client_order_id": client_order_id,
                    "alpaca_order_id": getattr(kontext, "alpaca_order_id", None),
                    "gefuellt": gefuellt,
                },
                sort_keys=True,
            )
            + "\n"
        )
        f.flush()
        os.fsync(f.fileno())
    _protokoll(verzeichnis, "persistiert", gefuellt=gefuellt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
