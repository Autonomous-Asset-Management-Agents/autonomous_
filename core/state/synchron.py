"""#3449 Schritt 5 — die synchrone Seite des ``StatePort`` (Plan: docs/3449-schritt5-…, Option A).

``is_halted()``, ``trip()`` und ``record_trade()`` sind synchron und haben rund zwanzig Aufrufer.
Der ``StatePort`` ist asynchron. Ein Halt muss aber dauerhaft sein, **bevor** ``trip()``
zurueckkehrt — ein nachgereichtes asynchrones Schreiben liesse ein Fenster, in dem ein Absturz
den Halt verliert (Plan, Option B verworfen).

**Die Form ist die des synchronen Redis-Clients** (``get``, ``set(ex=)``, ``delete``,
``incrbyfloat``, ``ping``). Enterprise benutzt ihn heute schon fuer den Halt; der Desktop bekommt
hier dieselbe Form auf SQLite — auf **derselben Datei und Tabelle** wie der asynchrone Adapter
(``sqlite_adapter.py``). Damit gibt es eine Wahrheit ueber den Halt, und der Kill-Switch braucht
keinen zweiten Codepfad je Edition.

Die Verbindung ist ``sqlite3`` aus der Standardbibliothek: kein Hintergrund-Thread, der den
Prozess beim Beenden festhielte (#3449), und nutzbar aus jedem Thread (``check_same_thread=False``
plus eigene Sperre).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .sqlite_adapter import _SCHEMA

#: Oeffnen zwei Prozesse eine frische Ablage gleichzeitig, antwortet SQLite einem sofort
#: „database is locked" (WAL-Wechsel, Schema) — die Wartezeit greift dabei nicht; gemessen an
#: der Zwei-Instanzen-Vorrichtung (#3453). Begrenzt wiederholt; danach bleibt es ein Fehler.
_WAL_VERSUCHE = 20
_WAL_PAUSE_S = 0.1


def _wiederholt(schritt) -> Any:
    """Begrenzte Wiederholung bei „database is locked" (wie im asynchronen Adapter, #3453)."""
    for versuch in range(1, _WAL_VERSUCHE + 1):
        try:
            return schritt()
        except sqlite3.OperationalError as fehler:
            if "locked" not in str(fehler) or versuch == _WAL_VERSUCHE:
                raise
            time.sleep(_WAL_PAUSE_S)


class SqliteSyncAblage:
    """Synchroner Zugriff auf den Betriebszustand in SQLite — in der Form des Redis-Clients."""

    def __init__(self, pfad: str | os.PathLike[str]):
        self.pfad = Path(pfad)
        self._sperre = threading.Lock()
        self._db: Optional[sqlite3.Connection] = None

    def _verbindung(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(
            self.pfad, isolation_level=None, check_same_thread=False, timeout=5.0
        )
        try:
            db.execute("PRAGMA busy_timeout=5000")
            _wiederholt(lambda: db.execute("PRAGMA journal_mode=WAL"))
            _wiederholt(lambda: db.executescript(_SCHEMA))
        except BaseException:
            db.close()
            raise
        self._db = db
        return db

    def ping(self) -> bool:
        with self._sperre:
            self._verbindung().execute("SELECT 1")
        return True

    def get(self, key: str) -> Optional[str]:
        with self._sperre:
            zeile = (
                self._verbindung()
                .execute(
                    "SELECT value, expires_at FROM engine_state WHERE key = ?", (key,)
                )
                .fetchone()
            )
        if zeile is None:
            return None
        wert, frist = zeile
        if frist is not None and frist <= time.time():
            return None
        return wert

    def set(self, key: str, value: Any, ex: Optional[float] = None, **_: Any) -> bool:
        frist = None if ex is None else time.time() + float(ex)
        with self._sperre:
            _wiederholt(
                lambda: self._verbindung().execute(
                    "INSERT INTO engine_state(key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "expires_at=excluded.expires_at",
                    (key, str(value), frist),
                )
            )
        return True

    def delete(self, *keys: str) -> int:
        geloescht = 0
        with self._sperre:
            for key in keys:
                cur = _wiederholt(
                    lambda k=key: self._verbindung().execute(
                        "DELETE FROM engine_state WHERE key = ?", (k,)
                    )
                )
                geloescht += cur.rowcount
        return geloescht

    def incrbyfloat(self, key: str, amount: float = 1.0) -> float:
        """Atomar ueber Prozesse: ``BEGIN IMMEDIATE`` haelt den Schreiber fuer die Dauer."""
        with self._sperre:
            db = self._verbindung()
            _wiederholt(lambda: db.execute("BEGIN IMMEDIATE"))
            try:
                zeile = db.execute(
                    "SELECT value, expires_at FROM engine_state WHERE key = ?", (key,)
                ).fetchone()
                stand = 0.0
                frist = None
                if zeile is not None:
                    wert, frist = zeile
                    if frist is not None and frist <= time.time():
                        frist = None
                    else:
                        stand = float(wert)
                stand += float(amount)
                db.execute(
                    "INSERT INTO engine_state(key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "expires_at=excluded.expires_at",
                    (key, repr(stand), frist),
                )
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return stand

    def expire(self, key: str, sekunden: float) -> bool:
        with self._sperre:
            cur = self._verbindung().execute(
                "UPDATE engine_state SET expires_at = ? WHERE key = ?",
                (time.time() + float(sekunden), key),
            )
        return cur.rowcount > 0

    def schliessen(self) -> None:
        with self._sperre:
            if self._db is not None:
                self._db.close()
                self._db = None


def synchrone_ablage() -> Optional[Any]:
    """Die dauerhafte synchrone Ablage dieser Installation — oder ``None``.

    Enterprise (``REDIS_URL``): der synchrone Redis-Client. Desktop (``AAA_USER_DATA_DIR``): die
    SQLite-Datei des ``StatePort``. Ohne beides gibt es keine dauerhafte Ablage — dann bleibt der
    Aufrufer bei seinem bisherigen Verhalten (Arbeitsspeicher). Eine Datei relativ zum
    Arbeitsverzeichnis ist keine Installation (#3468).
    """
    if os.environ.get("REDIS_URL", "").strip():
        from core.redis_client import RedisClient

        return RedisClient.get_sync_redis()
    if os.environ.get("AAA_USER_DATA_DIR", "").strip():
        from .zusammenbau import ablage_pfad

        return SqliteSyncAblage(ablage_pfad())
    return None
