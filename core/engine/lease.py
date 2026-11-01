"""#3453 — synchroner Haken auf die Schreibberechtigung (fuer die Kettenabnahme).

Die Ketten-Vorrichtung fragt vor ihrer Order synchron, ob ihre Instanz schreiben darf
(``tests/chain/_ein_intent.py``, ``_berechtigung``). Dieser Haken ist **dieselbe**
Erwerbsfunktion wie im Engine-Start (``core.lease.sichere_berechtigung``), nur synchron
verpackt — keine zweite Mechanik.

**Warum ein eigener Ring.** Die Berechtigung lebt auf dem Ring, der sie erwirbt, und muss
den synchronen Aufruf ueberdauern: Die Absendung danach laeuft auf einem anderen Ring und
prueft dort (``core.lease._Eintrag.rufe``). Ein ``asyncio.run`` je Aufruf schloesse den
Ring sofort wieder, und die Pruefung faende einen toten Ring. Der Ring ist ein Daemon — er
haelt den Prozess nicht fest, und die Verbindung zur Ablage ist ohnehin je Operation kurz.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from core import lease as _lease

_ring: Optional[asyncio.AbstractEventLoop] = None
_ring_sperre = threading.Lock()


def _haken_ring() -> asyncio.AbstractEventLoop:
    global _ring
    with _ring_sperre:
        if _ring is None or _ring.is_closed() or not _ring.is_running():
            ring = asyncio.new_event_loop()
            faden = threading.Thread(
                target=ring.run_forever, daemon=True, name="EngineLeaseRing"
            )
            faden.start()
            _ring = ring
        return _ring


def erwerbe_schreibberechtigung(*, konto: str, instanz: str) -> bool:
    """``True``, wenn diese Instanz fuer ``konto`` schreiben darf.

    ``konto`` ist die Nutzerkennung, wie sie auch die Absendestelle benutzt; der Modus
    (paper/live) wird wie dort ergaenzt (``core.lease.konto_schluessel``), damit beide
    dieselbe Berechtigung meinen.
    """
    from config import get_config

    if not _lease.aktiv():
        return True
    schluessel = _lease.konto_schluessel(
        konto, paper=bool(getattr(get_config(), "PAPER_TRADING", True))
    )
    return bool(
        asyncio.run_coroutine_threadsafe(
            _lease.sichere_berechtigung(schluessel, instanz=instanz), _haken_ring()
        ).result(timeout=30)
    )
