"""Das eine Tor zum Broker (#3379, Epic #3366).

`OrderGateway` ist der Ort, an dem eine Kapitalbewegung entschieden, ausgefuehrt und
protokolliert wird. Der Architektur-Test aus #3377 (CH-2, Szenario „genau ein
Broker-Aufrufer") zaehlt die Stellen, die heute daran vorbeigehen — jede umgehaengte
Stelle senkt die Zahl.

`liquidate_positions` (#3383) loest die Sammelaufrufe der drei Ausnahmepfade in eine
Schleife ueber die gehaltenen Positionen auf, damit je Bewegung ein Datensatz entsteht.
"""

from core.gateway.liquidation import (
    LiquidationReport,
    liquidate_positions,
    positions_summary,
)
from core.gateway.order_gateway import (
    EXEMPT_REASON_BY_KIND,
    NEVER_BLOCKED_KINDS,
    OrderGateway,
)

__all__ = [
    "EXEMPT_REASON_BY_KIND",
    "LiquidationReport",
    "NEVER_BLOCKED_KINDS",
    "OrderGateway",
    "liquidate_positions",
    "positions_summary",
]
