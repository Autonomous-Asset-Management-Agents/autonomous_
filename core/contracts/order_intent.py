"""OrderIntent — der Vertrag an der Uebergabe H2 (VC-2 -> VC-4) (#3378, ARC-E1.2).

Ein ``OrderIntent`` beschreibt, **was** bewegt werden soll und **aufgrund welcher
Entscheidung**. Er ist bewusst broker-neutral: kein ``MarketOrderRequest``, kein
Alpaca-Typ. Der Vertrag liegt zwischen den Schichten, nicht in einer davon — ein
``OrderIntent`` entsteht auch dort, wo mit Compliance nichts zu tun ist (Breaker in
``risk_manager.py:786``, Strategiewechsel in ``monitor_loop.py:262``).

``frozen=True``: Eine Zwischenstufe darf einen Intent nicht nachtraeglich umschreiben.
Nur so traegt die spaetere Beweisfuehrung „genau diese Entscheidung fuehrte zu genau
dieser Order".

``extra="forbid"``: Aus einem stillen Tippfehler wird eine Fehlermeldung mit Feldnamen.

In diesem Schritt ruft den Vertrag niemand auf — er bindet die Form, bevor #3379 die
Fassade einzieht.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Die sechs Pfade aus CH-2 (#3366) plus der Einstieg. Jeder von ihnen bewegt heute
# Kapital; „entry" und „stop" ueber den regulaeren Weg, die uebrigen daran vorbei.
IntentKind = Literal[
    "entry",
    "stop",
    "trim",
    "displacement",
    "panic",
    "breaker",
    "strategy_switch",
]

# Spiegelt classify_exit_kind (order_executor.py:591) — risk / rotation / trim.
# Ohne diese Unterscheidung ginge die Freistellungs-Logik aus #2065 beim Umbau verloren.
ExitKind = Literal["risk", "rotation", "trim"]


class OrderIntent(BaseModel):
    """Was bewegt werden soll, und warum."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision_id: str = Field(
        min_length=1, description="Rueckfuehrbarkeit auf die Entscheidung"
    )
    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    qty: float = Field(
        gt=0,
        description="Stueckzahl — bewusst float: fraktionale Positionen bleiben erhalten "
        "(Owner-Entscheid) und duerfen hier nicht auf ganze Stuecke gerundet werden.",
    )
    intent_kind: IntentKind

    # Schutz-Exits werden protokolliert, aber nie blockiert. Das Kennzeichen muss am
    # Intent haengen, damit das Tor es nicht aus dem Kontext raten muss.
    is_protective_exit: bool = False
    exit_kind: Optional[ExitKind] = None

    # Der Halt-Zustand zum Zeitpunkt der Entscheidung. CH-2 verlangt ihn ausdruecklich:
    # ohne ihn laesst sich hinterher nicht sagen, ob eine Order trotz Halt herausging.
    halted: bool

    limit_price: Optional[float] = Field(default=None, gt=0)
    stop_price: Optional[float] = Field(default=None, gt=0)
    time_in_force: Literal["day", "gtc"] = "day"
