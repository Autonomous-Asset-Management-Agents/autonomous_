"""#3449 — der Zusammenbau: welche Ablage hinter dem ``StatePort`` liegt.

Der Kern kennt nur ``StatePort``. Hier — und nur hier — faellt die Entscheidung, was
dahinterliegt. Sie folgt derselben Frage, die ``core/redis_client.py`` schon stellt: Ist
``REDIS_URL`` gesetzt? Das ist eine Frage an die Umgebung, keine Editionsabfrage.

* ohne ``REDIS_URL`` → ``SqliteStateAdapter`` auf ``<AAA_USER_DATA_DIR>/engine_state.db``
* mit ``REDIS_URL``  → ``RedisStateAdapter`` auf dem vorhandenen Redis-Client

**Ein Port je Ereignisring.** Die Engine hat mehrere Ringe (API, ``CloudLoggerWorker``,
kuenftig der Herzschlag der Engine-Sperre). Eine ``aiosqlite``-Verbindung und ein
``asyncio.Lock`` gehoeren dem Ring, der sie angelegt hat. Geteilt wird darum die Ablage,
nicht das Objekt — dasselbe Muster wie ``RedisClient._async_redis_pools``.

**Kein stiller Ersatz.** Ist Redis verlangt, aber nicht erreichbar, gibt es keinen Port.
Ein Ausweichen auf eine lokale Datei liesse zwei Instanzen je ihre eigene Wahrheit fuehren.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import weakref

from .port import StatePort
from .redis_adapter import RedisStateAdapter
from .sqlite_adapter import SqliteStateAdapter

logger = logging.getLogger(__name__)

DATEINAME = "engine_state.db"

# Schwach am Ring SELBST, nicht an ``id(loop)``: Python vergibt die id eines toten Rings
# neu. Ein Woerterbuch ueber ids reichte einem frischen Ring den Port eines toten — mit
# einer Verbindung, die an einem geschlossenen Ring haengt.
_ports: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, StatePort]" = (
    weakref.WeakKeyDictionary()
)


class StatePortUnavailable(RuntimeError):
    """Die verlangte Ablage ist nicht erreichbar — und es gibt bewusst keinen Ersatz."""


def _ist_lokal() -> bool:
    return not os.environ.get("REDIS_URL", "").strip()


def ablage_pfad() -> str:
    """Wo der Betriebszustand auf dem Desktop liegt.

    Unter ``AAA_USER_DATA_DIR``, weil nur dieses Verzeichnis ein Update der Anwendung
    ueberlebt (derselbe Grund wie in ``core/audit_paths.py``). Ohne die Variable —
    Entwicklung, Tests — relativ zum Arbeitsverzeichnis unter ``data/``.
    """
    verzeichnis = os.environ.get("AAA_USER_DATA_DIR", "").strip() or "data"
    os.makedirs(verzeichnis, exist_ok=True)
    return os.path.join(verzeichnis, DATEINAME)


async def state_port() -> StatePort:
    """Der ``StatePort`` dieses Ereignisrings. Wirft ``StatePortUnavailable``."""
    ring = asyncio.get_running_loop()
    port = _ports.get(ring)
    if port is not None:
        return port

    if _ist_lokal():
        port = SqliteStateAdapter(ablage_pfad())
    else:
        from core.redis_client import RedisClient

        client = await RedisClient.get_redis()
        if client is None:
            raise StatePortUnavailable(
                "REDIS_URL ist gesetzt, aber Redis ist nicht erreichbar. Es gibt bewusst "
                "keinen Ersatz durch eine lokale Datei (#3449)."
            )
        port = RedisStateAdapter(client)

    _ports[ring] = port
    return port


async def schliesse_alle() -> None:
    """Schliesst den Port **dieses** Rings und vergisst ihn.

    Nur den eigenen: Die Verbindung eines fremden Rings laesst sich von hier aus nicht
    sauber schliessen. Jeder Ring raeumt beim Herunterfahren selbst auf.
    """
    ring = asyncio.get_running_loop()
    port = _ports.pop(ring, None)
    if port is None:
        return
    # Den Redis-Client schliesst sein Besitzer (``RedisClient``), nicht wir.
    if isinstance(port, SqliteStateAdapter):
        await port.aclose()


@contextlib.asynccontextmanager
async def kurzer_port():
    """Ein Port nur fuer einen Vorgang — danach wieder zu (#3449).

    **Warum es das braucht.** Eine ``aiosqlite``-Verbindung laeuft in einem eigenen
    Nicht-Daemon-Thread. Bleibt sie offen, wartet Python beim Beenden ewig auf ihn — der
    Prozess endet nie. Gemessen an der Ketten-Vorrichtung (Timeout nach 120 s) und an einem
    Fuenfzeiler. Fuer die Engine hiesse das: Sie haengt beim Beenden, sobald irgendein
    Ereignisring einmal eine Order abgesetzt hat.

    Darum oeffnet ein kurzer Vorgang (eine Absendung, ein Abgleich) seine **eigene**
    SQLite-Verbindung und schliesst sie wieder — unabhaengig davon, welcher Ring wann endet.
    Mit Redis gibt es den geteilten Client aus ``RedisClient``; den schliesst sein Besitzer,
    nicht dieser Vorgang.
    """
    if _ist_lokal():
        port = SqliteStateAdapter(ablage_pfad())
        try:
            yield port
        finally:
            await port.aclose()
    else:
        yield await state_port()
