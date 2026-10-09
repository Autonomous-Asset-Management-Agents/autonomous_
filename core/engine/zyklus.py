# core/engine/zyklus.py
# #4244 (H-2c, ARC-E6 #3738) — umgezogen aus core/engine/trading_loop.py, wortgleich.
# Verantwortlichkeit: Zyklus-Typen des Dirigenten (#4007/#4008).
# Zyklusfrei: kein Import aus core.engine.trading_loop (Entscheidung #4184 §2) — die
# Mixin-Module brauchen die Typen schon beim Import (Annotationen unter Python 3.12).

import dataclasses
import enum
from typing import Any


class Zyklus(enum.Enum):
    """#4007 (G-2b): was der Dirigent nach einem Schritt tut."""

    WEITER = "weiter"  # die naechste Stufe dieses Durchlaufs ausfuehren
    NAECHSTER = "naechster"  # Durchlauf beenden -> `continue` (sleep macht der Schritt)
    STOPP = "stopp"  # Schleife beenden -> `break` (Schritt hat _shutdown_event gesetzt)


@dataclasses.dataclass
class ZyklusZustand:
    """#4007 (G-2b): stufenuebergreifende Werte EINES Durchlaufs, je Durchlauf neu.

    Bewusst kein Engine-Attribut: ein Wert des Vorzyklus darf nicht lesbar bleiben,
    und die API-Routen sollen keinen halb fertigen Zyklus sehen (Plan, Dual Design).
    """

    local_active_strategy: Any = None
    cycle_market_closed: bool = False
    symbols_to_process: list = dataclasses.field(default_factory=list)
    # Die Marktuhr dieses Durchlaufs; der Bericht bei geschlossenem Markt liest next_open.
    clock: Any = None
    # #4008 (G-2c): Zyklus-Kontext; Bewertung und Auswertung lesen ihn.
    t_start: float = 0.0
    t_data_fetched: float = 0.0
    t_strategy_done: float = 0.0
    current_time_utc: Any = None
    snapshots: dict = dataclasses.field(default_factory=dict)
    symbols_order: list = dataclasses.field(default_factory=list)
    portfolio_context: Any = None
    positions_by_symbol: dict = dataclasses.field(default_factory=dict)
    position_confirmed: bool = False
    # Die Auswertung und die Fehler-Handler lesen sie; je Durchlauf leer (#4017).
    graph_states: list = dataclasses.field(default_factory=list)
    results: list = dataclasses.field(default_factory=list)
