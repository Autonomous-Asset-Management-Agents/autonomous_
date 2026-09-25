"""``FillEvent`` — die Uebergabe H4, vom Broker zurueck in die Buecher (#3386, ARC-E2).

Heute endet dieser Rueckweg im Nichts. Die Broker-Order-ID wird am Kontext gesetzt
(``order_executor.py:1720``, ``:1783``), das Modell fuehrt das Feld
(``cloud_logger.py:162``) — und der Schreibpfad wirft es an der Persistenzgrenze weg
(``cloud_logger.py:496``, Filter auf ``model.__table__.columns.keys()``). Zu einem Fill
gibt es damit keinen Datensatz, der ihn mit der Entscheidung verbindet, die ihn ausgeloest
hat. Genau diese Verbindung haelt dieser Vertrag an einem Ort zusammen.

**Unveraenderlich**, weil ein Fill eine Tatsache ist und kein Zwischenstand: er ist beim
Broker passiert, und nichts in unserem Prozess darf ihn nachtraeglich anders erzaehlen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["buy", "sell"]


class FillEvent(BaseModel):
    """Eine Ausfuehrung beim Broker, mit ihrer Herkunft.

    ``fill_id`` traegt die Identitaet der **einzelnen Ausfuehrung**, nicht die der Order:
    eine Order kann in mehreren Teilen gefuellt werden, und zwei Teil-Fills derselben
    Order teilen sich die ``broker_order_id``. Ohne eigene Identitaet waeren sie nicht
    auseinanderzuhalten — und ein doppelt eingetroffenes Ereignis nicht als Wiederholung
    erkennbar.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Identitaet dieser einen Ausfuehrung.
    fill_id: str = Field(min_length=1)
    #: Die Order beim Broker. Mehrere Teil-Fills teilen sich diesen Wert.
    broker_order_id: str = Field(min_length=1)
    #: Der abgeleitete Idempotenz-Schluessel, mit dem die Order abgesendet wurde (#3387).
    client_order_id: str = ""
    #: Die Entscheidung, aus der die Order folgte. Ohne sie waere der Fill eine
    #: Kapitalbewegung ohne Urheber — genau der Zustand, den ARC-E2 beendet.
    decision_id: str = Field(min_length=1)

    symbol: str = Field(min_length=1)
    side: Side
    #: Bruchteilig zulaessig — bruchteilige Positionen bleiben (Owner-Entscheidung).
    filled_qty: float = Field(gt=0)
    price: float = Field(gt=0)
    filled_at: datetime
