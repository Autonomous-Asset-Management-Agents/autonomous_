"""Die Audit-Ablage — eine Senke fuer Tor-Entscheidungen und Verkaufsgruende (#4125/#4126).

Plan ``docs/4125-4126-audit-ablage/implementation_plan.md`` §2 Punkt 1 und §9. Jedes Ereignis
geht an zwei Stellen:

- in die Hash-Kette (``LocalJSONAuditLogger``, Ereignis ``AuditAblageEvent``) — auf dem Desktop
  die einzige dauerhafte Ablage, denn die Desktop-Engine hat kein Logfile;
- ueber ``log_compliance_event`` mit eigenem ``event_type`` in ``mifid_decision_log`` — auf
  Enterprise die WORM-Tabelle, auf dem Desktop die lokale SQLite-Tabelle.

**Fail-soft.** Ein Fehler wird als WARNING geloggt und in ``/engine-diagnostics.audit_write``
gezaehlt (``write_fail``), aber nie geworfen: Ein Schutz-Exit darf an der Ablage nie scheitern.
Die Kette wird im Hintergrund geschrieben — im laufenden Loop als Task, aus einem Worker-Thread
(der Normalfall am Tor, ``asyncio.to_thread``) ueber einen eigenen Hintergrund-Loop. Seit Teil 1a
ist der Ketten-Anhang prozessweit geordnet, egal aus welchem Loop.

Die Zeit kommt aus dem ``ClockPort`` (Plan §9d), unter ``SIM_MODE`` also aus der Sim-Uhr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Mapping, Optional

from core.ports.audit_ablage_port import AuditAblagePort
from core.ports.clock_port import ClockPort

logger = logging.getLogger(__name__)


def _als_text(wert: Any) -> str:
    """Kettenwert ohne Kommazahl-Risiko: bool klein, alles andere ``str``."""
    if isinstance(wert, bool):
        return "true" if wert else "false"
    return "" if wert is None else str(wert)


def _eigene_kette():
    """Die Kette dieses Prozesses: der Logger des Round Tables, sonst ein eigener.

    Auf Enterprise ist der Round-Table-Logger ``SenateProtocol``, der HITL-Ereignisse nur
    loggt — die Hash-Kette ist dort wie auf dem Desktop ``LocalJSONAuditLogger``. Mehrere
    Instanzen teilen sich seit Teil 1a Sperre und Kettenkopf.
    """
    from core.round_table import runner
    from core.round_table.senate_log import LocalJSONAuditLogger

    vorhanden = runner.get_audit_logger()
    if isinstance(vorhanden, LocalJSONAuditLogger):
        return vorhanden
    return LocalJSONAuditLogger()


def _wolke():
    from core.cloud_logger import get_cloud_logger

    return get_cloud_logger()


def _ist_simulation() -> bool:
    from config import get_config

    return bool(get_config().SIM_MODE)


def _zaehle_fehler() -> None:
    from core.round_table.senate_log import _bump_audit

    _bump_audit("write_fail")


class AuditAblage(AuditAblagePort):
    """Umsetzung des ``AuditAblagePort``. Gebaut und angemeldet von der Composition Root."""

    def __init__(
        self,
        *,
        clock: ClockPort,
        kette: Optional[Callable[[], Any]] = None,
        wolke: Callable[[], Any] = _wolke,
        ist_simulation: Callable[[], bool] = _ist_simulation,
    ) -> None:
        self._clock = clock
        self._kette_bauen = kette or _eigene_kette
        self._kette: Any = None
        self._wolke = wolke
        self._ist_simulation = ist_simulation
        self._ring: Optional[asyncio.AbstractEventLoop] = None
        self._ring_sperre = threading.Lock()

    # -- Port ---------------------------------------------------------------

    def lege_ab(self, art: str, daten: Mapping[str, Any]) -> None:
        try:
            zeitpunkt = self._clock.now().isoformat()
        except Exception as exc:  # noqa: BLE001 — fail-soft, nie zum Aufrufer
            self._fehler("Uhr", art, exc)
            return
        self._in_die_wolke(art, daten)
        self._in_die_kette(art, daten, zeitpunkt)

    # -- Ziele --------------------------------------------------------------

    def _in_die_wolke(self, art: str, daten: Mapping[str, Any]) -> None:
        try:
            order = dict(daten)
            order.setdefault("quantity", daten.get("qty", 0) or 0)
            self._wolke().log_compliance_event(
                order=order,
                approved=bool(daten.get("approved", False)),
                reason=str(daten.get("reason_code", "")),
                is_simulation=self._ist_simulation(),
                event_type=art,
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft, nie zum Aufrufer
            self._fehler("mifid_decision_log", art, exc)

    def _in_die_kette(self, art: str, daten: Mapping[str, Any], zeitpunkt: str) -> None:
        try:
            from core.round_table.senate_log import AuditAblageEvent

            if self._kette is None:
                self._kette = self._kette_bauen()
            text = json.dumps(
                {k: _als_text(v) for k, v in daten.items()}, sort_keys=True
            )
            ereignis = AuditAblageEvent(timestamp=zeitpunkt, art=art, daten=text)
            self._im_hintergrund(self._schreibe(self._kette, ereignis, art))
        except Exception as exc:  # noqa: BLE001 — fail-soft, nie zum Aufrufer
            self._fehler("Hash-Kette", art, exc)

    async def _schreibe(self, kette: Any, ereignis: Any, art: str) -> None:
        try:
            await kette.log_hitl_event(ereignis)
        except Exception as exc:  # noqa: BLE001 — fail-soft im Hintergrund
            self._fehler("Hash-Kette", art, exc)

    # -- Hintergrund --------------------------------------------------------

    def _im_hintergrund(self, coro) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run_coroutine_threadsafe(coro, self._hintergrund_loop())
            return
        from core.round_table.senate_log import spawn_audit_task

        spawn_audit_task(coro)

    def _hintergrund_loop(self) -> asyncio.AbstractEventLoop:
        """Ein eigener Loop in einem Daemon-Thread — fuer Aufrufe aus Worker-Threads."""
        with self._ring_sperre:
            if self._ring is None or self._ring.is_closed():
                ring = asyncio.new_event_loop()
                threading.Thread(
                    target=ring.run_forever, name="audit-ablage", daemon=True
                ).start()
                self._ring = ring
            return self._ring

    @staticmethod
    def _fehler(ziel: str, art: str, exc: Exception) -> None:
        try:
            _zaehle_fehler()
        except Exception:  # noqa: BLE001 — ein kaputter Zaehler bricht nichts
            pass
        logger.warning(
            "Audit-Ablage: %s fuer %s nicht geschrieben: %s",
            ziel,
            art,
            exc,
            exc_info=True,
        )
