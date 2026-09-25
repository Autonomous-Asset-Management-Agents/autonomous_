"""#3388 (ARC-E2.5) — die Schnittstelle zum Betriebszustand der Engine.

Der Kern kennt diesen Port, nicht den Adapter. Desktop legt den Zustand nach
SQLite, Enterprise nach Redis, und beide werden gegen **dieselbe** Abnahme
gefahren (``tests/unit/test_state_port_contract.py``). Das ist BORA in
ausfuehrbarer Form: Die Frage „verhaelt sich Desktop wie Enterprise" wird nicht
behauptet, sondern bei jedem CI-Lauf beantwortet.

**Warum kein Abbild der Redis-API.** ``LocalStateClient`` ist ausdruecklich als
„subset of the redis-py API" gebaut (``local_state_client.py:71-72``). Eine API
nachzubauen heisst, ihre Semantik zu erben, ohne sie zu garantieren — und der
heutige Sperr-Stub (``:94-95``, gibt bedingungslos ``True`` zurueck) ist der
Beweis, wohin das fuehrt. Dieser Port benennt darum *Absichten* und nicht
Redis-Befehle: ``increment`` statt ``INCRBYFLOAT``, ``append``/``pop`` statt
``RPUSH``/``LPOP``, eine Sperre mit Besitzer statt ``SETNX`` plus ``DEL``.

**Zeitbezug.** Fristen laufen nach Wanduhr, nicht nach ``engine_now()``. Redis
misst seine ``PX``-Frist am Server, und eine Ablage, deren Frist im Sim-Lauf
anders tickt als die andere, waere genau die Abweichung, die der Vertragstest
verhindern soll. Fristen sind hier eine Eigenschaft der *Ablage*, nicht der
Handelslogik.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional


class StateLockNotAcquired(RuntimeError):
    """``async with lock:`` konnte die Sperre nicht bekommen.

    Bewusst eine Ausnahme und kein Rueckgabewert: siehe ``StateLock.__aenter__``.
    Wer Nebenlaeufigkeit als **regulaeren** Ausgang behandeln will — „ich bin nicht
    der Schreiber, also handle ich nicht" —, benutzt ``acquire()`` direkt und faengt
    hier gar nichts.
    """


class StateLock(ABC):
    """Eine Sperre mit Besitzer.

    Die Freigabe wirkt nur fuer den Halter. Das ist der Unterschied zur heutigen
    Freigabe in ``redis_client.py:262-269``, die den Schluessel bedingungslos
    loescht: Laeuft die Frist des ersten Halters ab und uebernimmt ein zweiter,
    dann loescht die verspaetete Freigabe des ersten die Sperre des zweiten —
    und beide halten sich fuer den einzigen Schreiber.

    **Zwei Benutzungsarten, beide fail-closed.**

    Fuer den kritischen Abschnitt — der Block laeuft nur mit Sperre::

        async with port.lock("konto:1", ttl_seconds=30):
            ...  # hier gehoert die Sperre uns

    Fuer den Fall, dass Nebenlaeufigkeit ein regulaerer Ausgang ist (#3390: „eine
    andere Instanz schreibt, ich handle diesen Zyklus nicht")::

        sperre = port.lock("konto:1", ttl_seconds=30)
        if not await sperre.acquire():
            return
        try:
            ...
        finally:
            await sperre.release()
    """

    @abstractmethod
    async def acquire(self) -> bool:
        """``True``, wenn die Sperre jetzt uns gehoert; ``False``, wenn sie belegt ist.

        Wartet nicht. Wer warten will, wiederholt selbst — ein blockierender
        Erwerb im Handelspfad waere ein Aufhaenger ohne Zeitgrenze.
        """

    @abstractmethod
    async def renew(self) -> bool:
        """Verlaengert die Frist um ihre volle Laenge — **nur, wenn sie uns noch gehoert**.

        ``True``, wenn verlaengert wurde; ``False``, wenn die Sperre inzwischen einem
        anderen gehoert oder abgelaufen ist.

        **Warum das zum Vertrag gehoert (#3390).** Ohne Erneuerung muss die Frist so
        lang sein wie die laengste Arbeit, die unter ihr stattfindet. Fuer die
        Engine-Sperre waere das ein ganzer Zyklus — gemessen bis zu **1800 s**
        (``core/engine/time_budget.py``). Genau so lange saesse das Konto nach einem
        Absturz fest, bevor eine zweite Instanz uebernehmen darf. Mit Erneuerung
        entkoppeln sich beide Zahlen: kurze Frist, haeufige Verlaengerung, schnelle
        Uebernahme.

        Ein Erwerben-nach-Freigeben waere kein Ersatz — dazwischen laege ein Fenster,
        in dem ein anderer die Sperre bekommt.
        """

    @abstractmethod
    async def release(self) -> bool:
        """``True``, wenn *wir* freigegeben haben; ``False``, wenn sie uns nicht (mehr) gehoerte."""

    @abstractmethod
    async def held(self) -> bool:
        """``True``, wenn die Sperre in diesem Moment **uns** gehoert.

        Fuer den Aufrufer, der waehrend seiner Arbeit wissen muss, ob er noch zustaendig
        ist. Ohne diese Frage haelt sich eine abgeloeste Instanz weiter fuer den
        Schreiber und arbeitet weiter — und das Akzeptanzkriterium von #3390 verlangt
        ausdruecklich, dass sie es merkt und ihren Zustand meldet.
        """

    @property
    @abstractmethod
    def besitz_token(self) -> str:
        """Das Besitz-Token dieser Sperre (#3453).

        Wird neben die Sperre geschrieben, damit ein Nachfolger auf demselben Rechner einen
        **nachweislich toten** Halter erkennen und genau dessen Sperre brechen kann. Kein
        Nachschluessel: Mit dem Token laesst sich die Sperre nur brechen, nicht halten.
        """

    @abstractmethod
    async def brechen(self, fremdes_token: str) -> bool:
        """Entfernt die Sperre, **wenn** sie noch ``fremdes_token`` traegt (#3453).

        Fuer genau einen Fall: Der Halter ist nachweislich tot (derselbe Rechner, der
        Prozess existiert nicht mehr) und ein Neustart soll nicht die volle Frist warten.
        Hat inzwischen ein anderer uebernommen, wirkt es nicht — sonst hielten sich zwei
        fuer den einzigen Schreiber. ``True``, wenn gebrochen wurde.
        """

    async def __aenter__(self) -> "StateLock":
        """Erwirbt die Sperre oder wirft ``StateLockNotAcquired``.

        **Warum eine Ausnahme und kein ``bool``** (Review zu #3443, FINDING-01):
        Eine fruehere Fassung gab ``bool`` zurueck und verliess sich darauf, dass
        der Aufrufer prueft. Das war ein Fehler. ``async with lock:`` — ohne
        ``as`` — fuehrt den Block in Python **bedingungslos** aus, gleich was
        ``__aenter__`` zurueckgibt. Wer die Klammer so schreibt, laeuft also ohne
        Sperre weiter, und zwar still. Das ist derselbe Fehlermodus wie der
        No-Op-Stub in ``local_state_client.py:94-95``, den dieser Port gerade
        beheben soll — eine Sperre, die nicht sperrt und es nicht sagt.

        Mit der Ausnahme kann die Klammer nur zwei Ausgaenge haben: Der Block
        laeuft **mit** Sperre, oder er laeuft gar nicht.
        """
        if not await self.acquire():
            raise StateLockNotAcquired(
                f"{type(self).__name__}: Sperre ist belegt. Wer Nebenlaeufigkeit als "
                "regulaeren Ausgang behandeln will, benutzt acquire() direkt statt "
                "'async with'."
            )
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.release()


class StatePort(ABC):
    """Der Betriebszustand der Engine: Halt, Tagesbudget, Guards, HITL-Warteschlange."""

    # ── Schluessel und Werte ────────────────────────────────────────────────

    @abstractmethod
    async def get(self, key: str) -> Optional[str]:
        """Der Wert, oder ``None``, wenn es ihn nicht (mehr) gibt.

        ``None`` und der Leerstring sind ausdruecklich verschieden: Ein
        Halt-Zustand, der als Leerstring zurueckkaeme, wuerde von einer
        Wahrheitspruefung als „nicht gesetzt" gelesen.
        """

    @abstractmethod
    async def set(
        self, key: str, value: str, *, ttl_seconds: Optional[float] = None
    ) -> None:
        """Setzt den Wert. Ohne ``ttl_seconds`` faellt eine bestehende Frist weg.

        Sonst verschwaende ein frisch gesetzter Halt nach der Frist des alten.
        """

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """``True``, wenn etwas geloescht wurde."""

    @abstractmethod
    async def increment(
        self, key: str, amount: float = 1.0, *, ttl_seconds: Optional[float] = None
    ) -> float:
        """Zaehlt atomar hoch (oder mit negativem ``amount`` herunter) und gibt den neuen Stand zurueck.

        Das Tagesbudget ist ein Zaehler. „Ungefaehr" ist dort kein Ergebnis —
        darum atomar und nicht als Lesen-Rechnen-Schreiben.
        """

    # ── Warteschlange ───────────────────────────────────────────────────────

    @abstractmethod
    async def append(
        self, key: str, value: str, *, max_length: Optional[int] = None
    ) -> int:
        """Haengt hinten an und gibt die neue Laenge zurueck.

        Mit ``max_length`` fallen die aeltesten Eintraege weg — eine
        Warteschlange ohne Deckel ist ein Speicherleck mit Anlauf.
        """

    @abstractmethod
    async def read(self, key: str) -> List[str]:
        """Die Warteschlange von vorn nach hinten; leer, wenn es sie nicht gibt."""

    @abstractmethod
    async def pop(self, key: str) -> Optional[str]:
        """Nimmt den aeltesten Eintrag heraus; ``None`` bei leerer Warteschlange."""

    # ── Sperre ──────────────────────────────────────────────────────────────

    @abstractmethod
    def lock(self, name: str, *, ttl_seconds: float) -> StateLock:
        """Eine Sperre mit Frist. Die Frist ist Pflicht.

        Ohne Frist haelt ein abgestuerzter Prozess die Sperre fuer immer und
        legt das Konto still — eine Sperre ohne Frist ist ein Stillstand mit
        Ansage. Auf dieser Sperre setzt #3390 auf.
        """

    # ── Betrieb ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def ping(self) -> bool:
        """``True``, wenn die Ablage erreichbar ist."""

    @abstractmethod
    async def aclose(self) -> None:
        """Gibt die Verbindung frei. Danach ist der Port nicht mehr zu benutzen."""
