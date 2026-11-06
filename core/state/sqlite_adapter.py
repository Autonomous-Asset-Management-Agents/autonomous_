"""#3388 (ARC-E2.5) — SQLite-Adapter des ``StatePort`` (Desktop).

Loest das, was ``LocalStateClient`` ausdruecklich nicht kann: „ephemeral (no disk
persistence)" (``local_state_client.py:72``). Ein gesetzter Halt, ein verbrauchtes
Tagesbudget und eine gefuellte HITL-Warteschlange ueberleben hier den Neustart.

Eigene Tabellen, keine ORM-Kopplung: Der Betriebszustand ist kein Fachdatum und
soll nicht an Migrationen des Handelsschemas haengen. ``CREATE TABLE IF NOT
EXISTS`` beim ersten Zugriff genuegt — das Schema hat zwei Tabellen und keine
Fremdschluessel.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import List, Optional

import aiosqlite

from .port import StateLock, StatePort

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at REAL
);
CREATE TABLE IF NOT EXISTS engine_state_queue (
    seq   INTEGER PRIMARY KEY AUTOINCREMENT,
    key   TEXT NOT NULL,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engine_state_queue_key ON engine_state_queue(key, seq);
"""


#: #3453: Wie oft und in welchem Abstand das Einrichten einer frischen Ablage wiederholt wird.
#: Gemessen an der Zwei-Instanzen-Vorrichtung: Oeffnen zwei Prozesse eine frische Ablage
#: gleichzeitig, antwortet SQLite einem von beiden SOFORT „database is locked" — beim
#: WAL-Wechsel (beide wollen exklusiv) und beim Anlegen des Schemas (erst lesen, dann schreiben;
#: der Lesestand ist veraltet, sobald der andere geschrieben hat). Die Wartezeit (busy_timeout)
#: greift in beiden Faellen nicht. Beide Schritte sind idempotent; der naechste Versuch findet
#: sie meist erledigt. 20 × 0,1 s deckt das mit Abstand; eine wirklich gesperrte Ablage bleibt
#: danach ein Fehler.
_WAL_VERSUCHE = 20
_WAL_PAUSE_S = 0.1


async def _wiederholt_bei_sperre(schritt) -> None:
    for versuch in range(1, _WAL_VERSUCHE + 1):
        try:
            await schritt()
            return
        except sqlite3.OperationalError as fehler:
            if "locked" not in str(fehler) or versuch == _WAL_VERSUCHE:
                raise
            await asyncio.sleep(_WAL_PAUSE_S)


class _SqliteLock(StateLock):
    """Sperre als Zeile mit Frist und Besitzer-Kennung.

    Der Erwerb ist ein einziges ``INSERT ... ON CONFLICT DO UPDATE WHERE
    abgelaufen`` — ein Schritt, damit zwischen Pruefen und Setzen nichts
    dazwischenkommt.
    """

    def __init__(self, adapter: "SqliteStateAdapter", name: str, ttl_seconds: float):
        self._adapter = adapter
        self._key = f"__lock__:{name}"
        self._ttl = ttl_seconds
        self._token = uuid.uuid4().hex

    async def acquire(self) -> bool:
        jetzt = time.time()
        db = await self._adapter._verbindung()
        async with self._adapter._schreibsperre:
            cur = await db.execute(
                """
                INSERT INTO engine_state(key, value, expires_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at
                WHERE engine_state.expires_at IS NOT NULL AND engine_state.expires_at <= ?
                """,
                (self._key, self._token, jetzt + self._ttl, jetzt),
            )
            await db.commit()
            return cur.rowcount > 0

    async def renew(self) -> bool:
        # Verlaengern nur, solange die Zeile uns gehoert UND noch nicht abgelaufen
        # ist. Ohne die Ablauf-Bedingung wuerde eine Instanz eine Sperre wiederbeleben,
        # die zwischenzeitlich niemandem mehr gehoerte — und womoeglich waehrend ein
        # anderer sie gerade uebernimmt.
        jetzt = time.time()
        db = await self._adapter._verbindung()
        async with self._adapter._schreibsperre:
            cur = await db.execute(
                "UPDATE engine_state SET expires_at = ? "
                "WHERE key = ? AND value = ? "
                "AND expires_at IS NOT NULL AND expires_at > ?",
                (jetzt + self._ttl, self._key, self._token, jetzt),
            )
            await db.commit()
            return cur.rowcount > 0

    async def release(self) -> bool:
        db = await self._adapter._verbindung()
        async with self._adapter._schreibsperre:
            cur = await db.execute(
                "DELETE FROM engine_state WHERE key = ? AND value = ?",
                (self._key, self._token),
            )
            await db.commit()
            return cur.rowcount > 0

    async def held(self) -> bool:
        db = await self._adapter._verbindung()
        async with db.execute(
            "SELECT 1 FROM engine_state WHERE key = ? AND value = ? "
            "AND expires_at IS NOT NULL AND expires_at > ?",
            (self._key, self._token, time.time()),
        ) as cur:
            return await cur.fetchone() is not None

    @property
    def besitz_token(self) -> str:
        return self._token

    async def brechen(self, fremdes_token: str) -> bool:
        if not fremdes_token or fremdes_token == self._token:
            return False
        db = await self._adapter._verbindung()
        async with self._adapter._schreibsperre:
            cur = await db.execute(
                "DELETE FROM engine_state WHERE key = ? AND value = ?",
                (self._key, fremdes_token),
            )
            await db.commit()
            return cur.rowcount > 0


class SqliteStateAdapter(StatePort):
    """Betriebszustand in einer SQLite-Datei."""

    def __init__(self, path: str | os.PathLike[str]):
        self._path = Path(path)
        self._db: Optional[aiosqlite.Connection] = None
        # Serialisiert Lesen-Rechnen-Schreiben (``increment``, Warteschlangen-
        # Deckel) im Prozess. Zwischen Prozessen traegt das ``BEGIN IMMEDIATE``
        # der Transaktion.
        self._schreibsperre = asyncio.Lock()
        self._oeffnen = asyncio.Lock()

    async def _verbindung(self) -> aiosqlite.Connection:
        if self._db is not None:
            return self._db
        async with self._oeffnen:
            if self._db is not None:
                return self._db
            self._path.parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(self._path, isolation_level=None)
            # WAL: ein Leser blockiert den Schreiber nicht. Auf Desktop laufen
            # Engine und Konsole im selben Prozess, aber nicht im selben Task.
            # Scheitert das Einrichten, wird die Verbindung geschlossen: Ihr Thread ist kein
            # Daemon und hielte den Prozess sonst beim Beenden fest (#3449).
            try:
                await db.execute("PRAGMA busy_timeout=5000")
                await _wiederholt_bei_sperre(
                    lambda: db.execute("PRAGMA journal_mode=WAL")
                )
                await _wiederholt_bei_sperre(lambda: db.executescript(_SCHEMA))
            except BaseException:
                await db.close()
                raise
            self._db = db
            return db

    # ── Schluessel und Werte ────────────────────────────────────────────────

    async def get(self, key: str) -> Optional[str]:
        db = await self._verbindung()
        async with db.execute(
            "SELECT value, expires_at FROM engine_state WHERE key = ?", (key,)
        ) as cur:
            zeile = await cur.fetchone()
        if zeile is None:
            return None
        wert, frist = zeile
        if frist is not None and frist <= time.time():
            # Traege Raeumung: Die Zeile faellt beim naechsten Schreibzugriff
            # weg. Ein Loeschen im Lesepfad wuerde jeden Lesevorgang zu einem
            # Schreibvorgang machen.
            return None
        return wert

    async def set(
        self, key: str, value: str, *, ttl_seconds: Optional[float] = None
    ) -> None:
        frist = None if ttl_seconds is None else time.time() + ttl_seconds
        db = await self._verbindung()
        async with self._schreibsperre:
            await db.execute(
                "INSERT INTO engine_state(key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, expires_at=excluded.expires_at",
                (key, value, frist),
            )
            await db.commit()

    async def delete(self, key: str) -> bool:
        db = await self._verbindung()
        async with self._schreibsperre:
            cur = await db.execute("DELETE FROM engine_state WHERE key = ?", (key,))
            await db.commit()
            return cur.rowcount > 0

    async def increment(
        self, key: str, amount: float = 1.0, *, ttl_seconds: Optional[float] = None
    ) -> float:
        db = await self._verbindung()
        async with self._schreibsperre:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT value, expires_at FROM engine_state WHERE key = ?", (key,)
                ) as cur:
                    zeile = await cur.fetchone()
                stand = 0.0
                if zeile is not None:
                    wert, frist = zeile
                    if frist is None or frist > time.time():
                        try:
                            stand = float(wert)
                        except ValueError:
                            # Ein Zaehler, der keiner ist, wird nicht stumm auf
                            # Null gesetzt — das waere ein verschwundenes Budget.
                            raise ValueError(
                                f"StatePort: '{key}' ist kein Zaehler (Wert {wert!r}); "
                                "increment wuerde den bisherigen Stand verlieren (#3388)."
                            )
                neu = stand + amount
                frist_neu = None if ttl_seconds is None else time.time() + ttl_seconds
                await db.execute(
                    "INSERT INTO engine_state(key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                    "expires_at=excluded.expires_at",
                    (key, repr(neu), frist_neu),
                )
                await db.execute("COMMIT")
                return neu
            except BaseException:
                await db.execute("ROLLBACK")
                raise

    # ── Warteschlange ───────────────────────────────────────────────────────

    async def append(
        self, key: str, value: str, *, max_length: Optional[int] = None
    ) -> int:
        db = await self._verbindung()
        async with self._schreibsperre:
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "INSERT INTO engine_state_queue(key, value) VALUES (?, ?)",
                    (key, value),
                )
                if max_length is not None:
                    await db.execute(
                        "DELETE FROM engine_state_queue WHERE key = ? AND seq NOT IN "
                        "(SELECT seq FROM engine_state_queue WHERE key = ? "
                        " ORDER BY seq DESC LIMIT ?)",
                        (key, key, max_length),
                    )
                async with db.execute(
                    "SELECT COUNT(*) FROM engine_state_queue WHERE key = ?", (key,)
                ) as cur:
                    (laenge,) = await cur.fetchone()
                await db.execute("COMMIT")
                return int(laenge)
            except BaseException:
                await db.execute("ROLLBACK")
                raise

    async def read(self, key: str) -> List[str]:
        db = await self._verbindung()
        async with db.execute(
            "SELECT value FROM engine_state_queue WHERE key = ? ORDER BY seq", (key,)
        ) as cur:
            return [z[0] for z in await cur.fetchall()]

    async def pop(self, key: str) -> Optional[str]:
        db = await self._verbindung()
        async with self._schreibsperre:
            await db.execute("BEGIN IMMEDIATE")
            try:
                async with db.execute(
                    "SELECT seq, value FROM engine_state_queue WHERE key = ? "
                    "ORDER BY seq LIMIT 1",
                    (key,),
                ) as cur:
                    zeile = await cur.fetchone()
                if zeile is None:
                    await db.execute("COMMIT")
                    return None
                seq, wert = zeile
                await db.execute("DELETE FROM engine_state_queue WHERE seq = ?", (seq,))
                await db.execute("COMMIT")
                return wert
            except BaseException:
                await db.execute("ROLLBACK")
                raise

    # ── Sperre ──────────────────────────────────────────────────────────────

    def lock(self, name: str, *, ttl_seconds: float) -> StateLock:
        return _SqliteLock(self, name, ttl_seconds)

    # ── Betrieb ─────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            db = await self._verbindung()
            await db.execute("SELECT 1")
            return True
        except Exception as exc:
            logger.warning(
                "SqliteStateAdapter: Ablage %s nicht erreichbar (%s)",
                self._path,
                exc,
                exc_info=True,
            )
            return False

    async def aclose(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
