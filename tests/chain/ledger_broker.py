"""#3384 — ein Broker mit Gedaechtnis, das den Tod unseres Prozesses ueberlebt.

Ohne dieses Gedaechtnis kann die Chaos-Vorrichtung nichts messen: ``VirtualLiveBroker``
haelt seine Orders in einer Liste im Arbeitsspeicher (``core/sim/broker.py:263``), und
die stirbt mit dem Prozess, den wir absichtlich toeten. Nach dem Neustart waere der
Broker leer — und die Frage „sind fuer **eine** Entscheidung **zwei** Broker-Orders
entstanden?" nicht mehr zu stellen.

Ein echter Broker vergisst nicht, wenn unser Prozess stirbt. Diese Klasse bildet genau
diese Eigenschaft ab, und nur sie:

1. **Dauerhaftigkeit.** Jede angenommene Order wird angehaengt und mit ``fsync``
   auf die Platte gezwungen, *bevor* ``submit_order`` zurueckkehrt. Ein Tod zwischen
   Annahme und Rueckkehr laesst die Order beim Broker und uns ohne Wissen davon — das
   ist der Toetungspunkt „zwischen Absenden und Bestaetigung".
2. **Abweisung von Duplikaten.** Ein zweites Absenden mit demselben
   ``client_order_id`` wird abgelehnt.

Zu Punkt 2, damit im Review niemand raten muss: Diese Eigenschaft ist die
**ausdrueckliche Annahme des Bestands**, nicht meine Erfindung.
``core/idempotency.py`` begruendet den ganzen abgeleiteten Schluessel damit, dass „der
Broker das Duplikat ablehnt, statt eine zweite Order anzulegen", und
``core/engine/order_executor.py:2448`` nennt denselben Mechanismus. Sie ist in dieser
Sitzung **nicht** gegen die echte Alpaca-API nachgeprueft; was hier geprueft wird, ist
das Verhalten unserer Seite unter einem Broker, der sich so verhaelt, wie der Bestand
es annimmt. Verhielte er sich anders, waere die Annahme des Bestands falsch — und das
waere ein eigener Befund, kein Fehler dieser Vorrichtung.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from alpaca.common.exceptions import APIError


class DoppelterClientOrderId(APIError):
    """Der Broker kennt diesen ``client_order_id`` bereits — in Alpacas Form (#3473).

    HTTP 422, Code ``40010001``, Text ``client_order_id must be unique`` (Beleg im Kommentar
    an #3473). Die Vorrichtung ist damit broker-treu, nicht nachsichtig: Die Engine muss ein
    Duplikat an genau der Form erkennen, in der Alpaca es meldet.
    """

    def __init__(self, client_order_id: str):
        antwort = SimpleNamespace(status_code=422)
        super().__init__(
            json.dumps({"code": 40010001, "message": "client_order_id must be unique"}),
            SimpleNamespace(response=antwort, request=None),
        )
        self.client_order_id = client_order_id


class LedgerBroker:
    """Legt sich ueber einen Sim-Broker und fuehrt ein dauerhaftes Auftragsbuch."""

    def __init__(self, ledger_pfad: str | os.PathLike[str], inner: Any):
        self._pfad = Path(ledger_pfad)
        self._pfad.parent.mkdir(parents=True, exist_ok=True)
        self._inner = inner
        self._lock = threading.Lock()

    # -- Auftragsbuch -------------------------------------------------------

    def eintraege(self) -> List[Dict[str, Any]]:
        """Alles, was der Broker je angenommen hat — auch aus frueheren Prozessen."""
        if not self._pfad.exists():
            return []
        zeilen = self._pfad.read_text(encoding="utf-8").splitlines()
        return [json.loads(z) for z in zeilen if z.strip()]

    def _kennt(self, client_order_id: str) -> bool:
        return any(
            e.get("client_order_id") == client_order_id for e in self.eintraege()
        )

    def _anhaengen(self, satz: Dict[str, Any]) -> None:
        """Anhaengen und erzwingen, dass es die Platte erreicht hat.

        Ohne ``flush`` + ``fsync`` laege der Satz im Puffer des Betriebssystems, und
        ein harter Tod verloere ihn — die Vorrichtung wuerde dann die Dauerhaftigkeit
        des Brokers messen statt die unseres Codes.
        """
        with self._pfad.open("a", encoding="utf-8") as f:
            f.write(json.dumps(satz, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    # -- Broker-Schnittstelle ----------------------------------------------

    def submit_order(self, order_data: Any) -> Any:
        client_order_id: Optional[str] = getattr(order_data, "client_order_id", None)
        with self._lock:
            if client_order_id and self._kennt(client_order_id):
                raise DoppelterClientOrderId(client_order_id)
            order = self._inner.submit_order(order_data)
            self._anhaengen(
                {
                    "client_order_id": client_order_id,
                    "broker_order_id": str(getattr(order, "id", "")),
                    "symbol": str(getattr(order, "symbol", "")),
                    "side": str(getattr(order, "side", "")),
                    "qty": str(getattr(order, "qty", "")),
                    "status": str(getattr(order, "status", "")),
                }
            )
            return order

    def get_order_by_client_id(self, client_order_id: str) -> Any:
        """Die Order zu einem Schluessel — aus dem Auftragsbuch, also auch nach unserem Tod.

        Der Sim-Broker darunter lebt im Arbeitsspeicher und kennt nach einem Neustart nichts
        mehr; ein echter Broker schon. Darum antwortet das Buch (#3473).
        """
        for satz in reversed(self.eintraege()):
            if satz.get("client_order_id") == client_order_id:
                return SimpleNamespace(
                    id=satz.get("broker_order_id"),
                    client_order_id=client_order_id,
                    symbol=satz.get("symbol"),
                    side=satz.get("side"),
                    qty=satz.get("qty"),
                    status=satz.get("status", ""),
                )
        raise APIError(
            json.dumps({"code": 40410000, "message": "order not found"}),
            SimpleNamespace(response=SimpleNamespace(status_code=404), request=None),
        )

    def __getattr__(self, name: str) -> Any:
        """Alles Uebrige unveraendert an den Sim-Broker weiterreichen."""
        return getattr(self._inner, name)
