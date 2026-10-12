"""#3390 (ARC-E2.7) — Schreibberechtigung auf Zeit: genau ein aktiver Schreiber je Konto.

Je Konto haelt genau **eine** Engine-Instanz eine Berechtigung. Sie erneuert sie,
solange sie lebt **und vorankommt**; verliert oder bekommt sie sie nicht, handelt sie
nicht. Damit darf ``max-instances`` aus #3385 wieder ueber 1 steigen.

Die Berechtigung liegt auf dem ``StatePort`` aus #3388 — **nicht** auf einer eigenen
Ablage. Eine Sperre, die ihre eigene Mechanik mitbraechte, waere die dritte
Zustandsverwaltung im Haus neben Redis und ``LocalStateClient``, und die erste, die
niemand gegen die anderen prueft (Plan §4, Option A).

**Warum der Schluessel den Modus traegt.** ``user_id`` allein bezeichnet **nicht** ein
Broker-Konto: ``config.py:56-58`` und der Validator in ``:65-76`` tauschen bei
``PAPER_TRADING=False`` auf **getrennte** Schluesselfaecher, bei unveraenderter
``user_id`` und mit anderem Endpunkt. Ein Schluessel je Nutzer wuerde eine
Paper-Instanz gegen eine Live-Instanz sperren — und damit genau den Fall verhindern,
den #1425 ermoeglichen wollte. Auf dem Desktop kommt hinzu, dass ``user_id`` dort die
Konstante ``"global"`` ist (``order_executor.py:548`` und vier weitere Stellen).

**Warum die Erneuerung am Fortschritt haengt und nicht am Leben.** Ein Herzschlag in
einem eigenen Thread tickt auch dann weiter, wenn die Schleife festgefahren ist — dann
hortet eine haengende Engine ihre Berechtigung und die Uebernahme findet nie statt.
Das waere dieselbe Fehlerklasse wie in #3438, wo ``stop()`` zurueckgab, als haette es
gestoppt. Darum erneuert ``erneuern()`` nur, wenn der Aufrufer Fortschritt meldet.

**Die Zahlen und woher sie kommen.** Aus der laufenden Konfiguration gelesen
(``core/engine/time_budget.py:134``): ``agent=60s``, ``symbol=120s``, ``cycle=1800s``.
Ein Zyklus darf also eine halbe Stunde dauern — eine Berechtigung ohne Erneuerung
muesste so lange gelten, und so lange saesse das Konto nach einem Absturz fest. Die
Vorgabewerte unten sind daraus **abgeleitet**, nicht im Betrieb gemessen.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

from core.state import StatePort

logger = logging.getLogger(__name__)

# **Warum NICHT unter ``core/engine/``.** ``core/engine/__init__.py`` re-exportiert
# ``BotEngine`` und ``app`` — wer irgendetwas unter ``core.engine`` importiert, bootet
# damit die ganze Engine. Gemessen: ``import core.engine.lease`` zog 142 Module, lud
# PyQt6 und veraenderte ``PATH``. Fuer eine Berechtigung, die **vor** dem Engine-Start
# erworben werden muss — und die in einem schlanken Unterprozess der Kettenabnahme
# gebraucht wird —, ist das verkehrt herum. Dieses Modul haengt nur am ``StatePort``,
# und genau dort soll es auch im Import-Graphen stehen.

#: Frist der Berechtigung. Kurz genug fuer eine zuegige Uebernahme, lang genug, um
#: mehrere ausgefallene Herzschlaege zu ueberstehen.
FRIST_SEKUNDEN = 60.0

#: Abstand der Erneuerung. Fuenf Versuche je Frist — ein verpasster Takt kostet die
#: Berechtigung nicht.
TAKT_SEKUNDEN = 15.0

#: Ab wann ein Zyklus als nicht mehr fortschreitend gilt. Eine einzelne
#: Symbol-Auswertung darf 120 s brauchen; darunter waere die Schwelle ein Fehlalarm.
FORTSCHRITT_MAX_ALTER_SEKUNDEN = 150.0

#: Vermerk neben der Sperre: wer sie haelt (Rechner, PID, Token). Nur fuer die Uebernahme von
#: einem nachweislich toten Halter auf demselben Rechner (#3453, Owner-Entscheid 18.09.).
HALTER_PRAEFIX = "engine_lease_halter:"


def konto_schluessel(user_id: Optional[str], *, paper: bool) -> str:
    """Der Kontoname fuer die Berechtigung: Nutzer **und** Handelsmodus.

    Ohne den Modus waere der Schluessel zu grob (siehe Modul-Dokumentation).
    ``user_id`` darf leer sein — dann gilt derselbe Vorgabewert wie im Order-Pfad
    (``order_executor.py:548``), damit beide dasselbe Konto meinen.
    """
    return f"{user_id or 'global'}:{'paper' if paper else 'live'}"


class EngineLease:
    """Die Schreibberechtigung einer Instanz fuer ein Konto.

    Duenn ueber ``StatePort.lock`` — Erwerb, Erneuerung, Freigabe und Besitzpruefung
    kommen von dort und sind gegen **beide** Adapter abgenommen
    (``tests/unit/test_state_port_contract.py``).
    """

    def __init__(
        self,
        port: StatePort,
        *,
        konto: str,
        instanz: str,
        frist_s: float = FRIST_SEKUNDEN,
        fortschritt: Optional[Callable[[], bool]] = None,
    ):
        self._konto = konto
        self._instanz = instanz
        self._fortschritt = fortschritt
        self._port = port
        self._frist_s = frist_s
        self._sperre = port.lock(f"engine_lease:{konto}", ttl_seconds=frist_s)

    @property
    def _halter_schluessel(self) -> str:
        return HALTER_PRAEFIX + self._konto

    async def _halter_vermerken(self) -> None:
        """Schreibt neben die Sperre, wer sie haelt (#3453). Fehlt der Vermerk, wartet ein
        Nachfolger die Frist ab — die sichere Richtung."""
        try:
            await self._port.set(
                self._halter_schluessel,
                json.dumps(
                    {
                        "host": socket.gethostname(),
                        "pid": os.getpid(),
                        "token": self._sperre.besitz_token,
                        "instanz": self._instanz,
                    }
                ),
                ttl_seconds=self._frist_s,
            )
        except Exception:  # noqa: BLE001
            logger.exception("EngineLease: Halter-Vermerk fuer %s fehlt.", self._konto)

    async def _uebernehmen_wenn_tot(self) -> bool:
        """Bricht die Sperre eines **nachweislich toten** Halters auf diesem Rechner.

        Owner-Entscheid 18.09.2026 (#3453): Nach einem Absturz soll der Desktop nicht die volle
        Frist lang still stehen — auch keinen Stop-Loss absetzen koennen. Nachweislich tot heisst:
        derselbe Rechner, und der Prozess mit dieser PID existiert nicht mehr. Auf einem anderen
        Rechner (Enterprise) ist das nicht pruefbar; dort gilt weiter die Frist. Gebrochen wird nur
        die Sperre mit dem Token des Toten — hat inzwischen ein anderer uebernommen, nichts.
        """
        roh = await self._port.get(self._halter_schluessel)
        if not roh:
            return False
        try:
            halter = json.loads(roh)
            pid = int(halter.get("pid") or 0)
        except (ValueError, TypeError):
            logger.warning(
                "EngineLease: Halter-Vermerk fuer %s unlesbar (%r) — die Frist wird "
                "abgewartet (#3453).",
                self._konto,
                roh,
            )
            return False
        if halter.get("host") != socket.gethostname():
            return False
        if pid <= 0 or pid == os.getpid() or _prozess_lebt(pid):
            return False
        gebrochen = await self._sperre.brechen(str(halter.get("token") or ""))
        if gebrochen:
            logger.warning(
                "EngineLease: Halter %s (PID %s) ist nicht mehr am Leben — %s uebernimmt %s "
                "ohne die Frist abzuwarten (#3453).",
                halter.get("instanz", "?"),
                pid,
                self._instanz,
                self._konto,
            )
        return gebrochen

    async def erwerben(self) -> bool:
        """``True``, wenn diese Instanz ab jetzt der Schreiber ist."""
        erhalten = await self._sperre.acquire()
        if not erhalten and await self._uebernehmen_wenn_tot():
            erhalten = await self._sperre.acquire()
        if erhalten:
            await self._halter_vermerken()
            logger.info(
                "EngineLease: %s ist Schreiber fuer %s", self._instanz, self._konto
            )
        else:
            # WARNING, nicht DEBUG: Der Betreiber muss sehen, dass hier eine Instanz
            # bewusst NICHT handelt — sonst haelt er sie fuer haengend (#3390).
            logger.warning(
                "EngineLease: %s handelt NICHT — eine andere Instanz schreibt bereits "
                "auf %s.",
                self._instanz,
                self._konto,
            )
        return erhalten

    async def erneuern(self) -> bool:
        """Verlaengert die Berechtigung — **nur bei gemeldetem Fortschritt**.

        Ohne Fortschritt wird nicht erneuert. Die Berechtigung laeuft dann aus und eine
        andere Instanz darf uebernehmen. Das ist Absicht: Eine festgefahrene Engine
        soll das Konto nicht blockieren.
        """
        if self._fortschritt is not None and not self._fortschritt():
            logger.warning(
                "EngineLease: %s erneuert NICHT — der Zyklus meldet keinen Fortschritt. "
                "Die Berechtigung fuer %s laeuft aus, damit uebernommen werden kann "
                "(#3390).",
                self._instanz,
                self._konto,
            )
            return False
        erneuert = await self._sperre.renew()
        if erneuert:
            await self._halter_vermerken()
        return erneuert

    async def freigeben(self) -> bool:
        """Gibt die Berechtigung frei — wirkt nur fuer den Halter."""
        return await self._sperre.release()

    async def haelt_noch(self) -> bool:
        """``True``, wenn diese Instanz in diesem Moment der Schreiber ist.

        Der Aufrufer fragt das **vor jeder Order**. Eine abgeloeste Instanz, die das
        nicht fragt, setzt weiter Orders ab im Glauben, sie sei zustaendig.
        """
        return await self._sperre.held()


# ---------------------------------------------------------------------------
# #3453: Verdrahtung — Registratur, Pruefung vor der Order, Erneuerung, Freigabe
# ---------------------------------------------------------------------------
#
# **Warum eine Registratur je Konto, samt Ring.** Die Sperre lebt auf dem Ring, der sie
# erworben hat: Ihr Besitz-Token steckt im Sperr-Objekt, und eine SQLite-Verbindung ist
# ringgebunden. Sendet ein anderer Ring (die API, die Ketten-Vorrichtung), wird auf dem Ring
# der Sperre geprueft — nicht mit einem zweiten Token neu erworben, das gegen die eigene
# Berechtigung verloere. So bleibt der Port-Vertrag unveraendert (Plan #3453, F2-B verworfen).
#
# **Warum jede Sperre ihre eigene, kurze Verbindung hat.** Eine offene ``aiosqlite``-Verbindung
# laeuft in einem Nicht-Daemon-Thread und haelt den Prozess beim Beenden fest (#3449,
# ``zusammenbau.kurzer_port``). Eine Berechtigung wird aber lange gehalten. Darum oeffnet jede
# Operation die Verbindung und schliesst sie danach; die Berechtigung selbst liegt mit ihrer
# Frist in der Ablage und braucht keine offene Verbindung.


def _prozess_lebt(pid: int) -> bool:
    """Lebt ein Prozess mit dieser PID auf diesem Rechner? Im Zweifel ``True`` (Frist abwarten).

    Eine wiederverwendete PID laesst einen Toten lebendig aussehen — dann wird die Frist
    abgewartet, die sichere Richtung.
    """
    try:
        import psutil

        return bool(psutil.pid_exists(pid))
    except Exception:  # noqa: BLE001 — ohne Nachweis gilt er als lebendig
        logger.exception(
            "EngineLease: Ob PID %s lebt, ist nicht pruefbar — die Frist wird "
            "abgewartet (#3453).",
            pid,
        )
        return True


#: Wer diese Instanz ist — fuer die Meldungen, nicht fuer den Besitz (der steckt im Token).
INSTANZ = f"{socket.gethostname()}:{os.getpid()}"


class KeineSchreibberechtigung(Exception):
    """Diese Instanz haelt die Schreibberechtigung fuer das Konto nicht (#3453)."""


@dataclass
class _Eintrag:
    lease: EngineLease
    ring: asyncio.AbstractEventLoop
    port: StatePort
    kurz: bool

    async def rufe(self, operation: Callable[[EngineLease], Awaitable[bool]]) -> bool:
        """Fuehrt ``operation`` auf dem Ring der Sperre aus und schliesst danach."""

        async def _dort() -> bool:
            try:
                return await operation(self.lease)
            finally:
                if self.kurz:
                    await self.port.aclose()

        if _laufender_ring() is self.ring:
            return await _dort()
        if self.ring.is_closed() or not self.ring.is_running():
            raise KeineSchreibberechtigung(
                "Der Ring der Berechtigung laeuft nicht mehr."
            )
        return await asyncio.wrap_future(
            asyncio.run_coroutine_threadsafe(_dort(), self.ring)
        )


_registratur: Dict[str, _Eintrag] = {}
_OHNE_ABLAGE_GEMELDET = False


def eintrag(konto: str) -> Optional[_Eintrag]:
    return _registratur.get(konto)


def aktiv() -> bool:
    """Ist die Sperre eingeschaltet **und** gibt es eine gemeinsame Ablage?"""
    global _OHNE_ABLAGE_GEMELDET
    try:
        from config import get_config

        if not getattr(get_config(), "ENGINE_LEASE_ENABLED", True):
            return False
    except Exception:  # noqa: BLE001 — ohne lesbare Konfiguration gilt der Default
        logger.exception(
            "EngineLease: Konfiguration nicht lesbar — ENGINE_LEASE_ENABLED gilt als an "
            "(#3453)."
        )
    if (
        os.environ.get("AAA_USER_DATA_DIR", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
    ):
        return True
    if not _OHNE_ABLAGE_GEMELDET:
        _OHNE_ABLAGE_GEMELDET = True
        logger.warning(
            "EngineLease: inaktiv — keine gemeinsame Ablage (weder AAA_USER_DATA_DIR noch "
            "REDIS_URL). Ohne sie gibt es keine zweite Instanz, gegen die gesperrt werden "
            "koennte (#3453)."
        )
    return False


async def _erwirb_auf_diesem_ring(konto: str, instanz: str) -> Optional[_Eintrag]:
    """Ein Erwerbsversuch auf dem laufenden Ring. Registriert bei Erfolg."""
    from core.state import SqliteStateAdapter, zusammenbau

    if zusammenbau._ist_lokal():
        port: StatePort = SqliteStateAdapter(zusammenbau.ablage_pfad())
        kurz = True
    else:
        port = await zusammenbau.state_port()
        kurz = False
    neu = _Eintrag(
        lease=EngineLease(port, konto=konto, instanz=instanz),
        ring=asyncio.get_running_loop(),
        port=port,
        kurz=kurz,
    )
    if not await neu.rufe(lambda lease: lease.erwerben()):
        return None
    _registratur[konto] = neu
    return neu


async def sichere_berechtigung(konto: str, *, instanz: str = INSTANZ) -> bool:
    """``True``, wenn diese Instanz fuer ``konto`` schreiben darf — sonst **ein** Versuch.

    Haelt sie die Berechtigung (auf welchem Ring auch immer), gilt sie. Sonst genau ein
    Erwerbsversuch auf dem laufenden Ring: Die Sperre ist frei, wenn niemand uebernommen hat.
    Kein Warten, keine Schleife. Fehler werfen — der Aufrufer entscheidet, was sie bedeuten.
    """
    vorhanden = _registratur.get(konto)
    if vorhanden is not None:
        try:
            if await vorhanden.rufe(lambda lease: lease.haelt_noch()):
                return True
        except KeineSchreibberechtigung:
            logger.warning(
                "EngineLease: Der Ring der Berechtigung fuer %s laeuft nicht mehr — "
                "ein neuer Erwerbsversuch folgt (#3453).",
                konto,
            )
        _registratur.pop(konto, None)
    return await _erwirb_auf_diesem_ring(konto, instanz) is not None


async def darf_schreiben(konto: str, *, instanz: str = INSTANZ) -> bool:
    """Die Pruefung vor jeder Order. ``True`` auch, wenn die Sperre nicht aktiv ist.

    Ein Fehler beim Lesen heisst: nicht belegt, dass diese Instanz schreibt — ``False``. Die
    Sperre ist eine Schutzvorrichtung wie der Halt; im Zweifel wird nicht gehandelt.
    """
    if not aktiv():
        return True
    try:
        return await sichere_berechtigung(konto, instanz=instanz)
    except Exception:  # noqa: BLE001 — unlesbar ist nicht belegt
        logger.exception(
            "EngineLease: Berechtigung fuer %s nicht lesbar — die Order wird "
            "zurueckgehalten (#3453).",
            konto,
        )
        return False


async def erneuere_auf_diesem_ring(*, fortschritt: Callable[[], bool]) -> int:
    """Erneuert jede Berechtigung dieses Rings — nur bei Fortschritt. Gibt die Zahl zurueck."""
    eigene = [
        (k, e) for k, e in list(_registratur.items()) if e.ring is _laufender_ring()
    ]
    if not eigene:
        return 0
    if not fortschritt():
        logger.warning(
            "EngineLease: keine Erneuerung — der Zyklus meldet keinen Fortschritt. Die "
            "Berechtigung laeuft aus, damit uebernommen werden kann (#3453)."
        )
        return 0
    erneuert = 0
    for konto, e in eigene:
        try:
            if await e.rufe(lambda lease: lease.erneuern()):
                erneuert += 1
            else:
                _registratur.pop(konto, None)
        except Exception:  # noqa: BLE001 — die naechste Order erwirbt neu
            logger.exception("EngineLease: Erneuerung fuer %s gescheitert.", konto)
    return erneuert


async def beende_auf_diesem_ring() -> None:
    """Gibt jede Berechtigung dieses Rings frei (Stopp)."""
    for konto, e in list(_registratur.items()):
        if e.ring is not _laufender_ring():
            continue
        _registratur.pop(konto, None)
        try:
            await e.rufe(lambda lease: lease.freigeben())
        except Exception:  # noqa: BLE001 — sie laeuft nach der Frist von selbst ab
            logger.exception("EngineLease: Freigabe fuer %s gescheitert.", konto)


def _laufender_ring() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:  # kein Fehler: es laeuft schlicht kein Ring
        return None


def _vergessen_fuer_tests(*, freigeben: bool = False) -> None:
    """Nur fuer Tests: Registratur leeren (optional vorher auf fremden Ringen freigeben)."""
    global _OHNE_ABLAGE_GEMELDET
    _OHNE_ABLAGE_GEMELDET = False
    if freigeben:
        for e in list(_registratur.values()):
            if e.ring.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(
                        e.rufe(lambda lease: lease.freigeben()), e.ring
                    ).result(timeout=10)
                except Exception:  # noqa: BLE001 — nur Testaufraeumen
                    logger.exception(
                        "EngineLease: Freigabe beim Aufraeumen gescheitert."
                    )
    _registratur.clear()
