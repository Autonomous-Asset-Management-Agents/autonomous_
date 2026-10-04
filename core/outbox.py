"""Outbox — der Order-Intent wird festgeschrieben, bevor gesendet wird (#3449, ARC-E2.5b).

**Der Befund, den dieses Modul adressiert (CH-4).** Stirbt der Prozess zwischen dem
Absenden einer Order und ihrer Bestaetigung, weiss der Neustart nichts von ihr. Er faellt
eine neue Entscheidung mit frischer ``decision_id`` — und weil der Idempotenz-Schluessel
aus #3387 an der ``decision_id`` haengt, ist auch der Schluessel neu. Der Broker erkennt
das Duplikat nicht und haelt zwei Orders fuer eine Entscheidung.

Die Outbox schliesst das Fenster von der anderen Seite: Der Intent liegt **dauerhaft** im
``StatePort``, bevor der Broker ihn sieht. Der Neustart findet ihn, samt ``decision_id``
und ``client_order_id``, und sendet **denselben** Schluessel erneut.

**Schnitt dieses Schritts.** Hier steht nur das Modul. Die Verdrahtung in den Absendepfad
(„erst schreiben, dann senden") ist ein eigener Schritt, weil sie dieselbe Funktion
beruehrt wie die Tor-Anbindung aus #3447.

**Fehlerrichtung.** Im Zweifel bleibt ein Intent **zu viel** unbestaetigt stehen, nie einer
zu wenig. Ein ueberzaehliger Intent fuehrt zu einem erneuten Senden mit demselben
Schluessel — der Broker weist es ab. Ein fehlender Intent fuehrt zur zweiten Order.
Darum laufen Zustaende nur vorwaerts, und geraeumt wird nur Erledigtes.

**Editionen (BORA).** Das Modul kennt keine Edition. Es spricht nur den ``StatePort``;
ob darunter SQLite (Desktop) oder Redis (Enterprise) liegt, entscheidet der Aufrufer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, replace
from typing import List, Optional

from core.state.port import StatePort

logger = logging.getLogger(__name__)

#: Die Zustaende in ihrer einzigen erlaubten Richtung. ``bestaetigt`` und ``verworfen``
#: sind beide Endzustaende und tragen denselben Rang — keiner ueberschreibt den anderen.
_RANG = {"offen": 0, "abgesendet": 1, "bestaetigt": 2, "verworfen": 2}
_ERLEDIGT = frozenset({"bestaetigt", "verworfen"})

_VERZEICHNIS = "outbox:verzeichnis"
_PRAEFIX = "outbox:intent:"

# Frist der Sperre um einen Zustandswechsel. Der Abschnitt darunter sind zwei bis drei
# Port-Aufrufe; die Frist ist nur das Netz fuer den Absturz mittendrin.
_SPERRFRIST_S = 10.0
# Die Port-Sperre wartet nicht (``StateLock.acquire``). Ein Zustandswechsel wiederholt
# darum selbst — kurz und begrenzt, denn er steht im Absendepfad.
_SPERRVERSUCHE = 25
_SPERRPAUSE_S = 0.02


@dataclass(frozen=True)
class OutboxEintrag:
    """Ein festgeschriebener Order-Intent.

    ``client_order_id`` ist der Idempotenz-Schluessel aus #3387 und zugleich der
    Schluessel in der Outbox: eine Order, ein Schluessel, ein Eintrag.
    """

    decision_id: str
    leg: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    user_id: str = ""
    zustand: str = "offen"
    broker_order_id: str = ""
    grund: str = ""

    def als_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def aus_json(cls, roh: str) -> Optional["OutboxEintrag"]:
        try:
            daten = json.loads(roh)
            bekannt = {f: daten[f] for f in cls.__dataclass_fields__ if f in daten}
            return cls(**bekannt)
        except Exception as exc:
            logger.warning("[Outbox] Eintrag nicht lesbar (%s): %.120s", exc, roh)
            return None


class Outbox:
    """Festschreiben, Zustand fortschreiben, Unbestaetigtes wiederfinden."""

    def __init__(self, port: StatePort) -> None:
        self._port = port

    # -- Lesen ---------------------------------------------------------------

    async def lese(self, client_order_id: str) -> Optional[OutboxEintrag]:
        roh = await self._port.get(_PRAEFIX + client_order_id)
        return OutboxEintrag.aus_json(roh) if roh is not None else None

    async def unbestaetigte(
        self, *, symbol: Optional[str] = None
    ) -> List[OutboxEintrag]:
        """Alle Intents, deren Order noch nicht bestaetigt ist — aelteste zuerst.

        ``offen`` **und** ``abgesendet``: Beides heisst „der Broker hat uns diese Order
        noch nicht bestaetigt". Genau der zweite Fall ist das CH-4-Fenster.
        """
        ergebnis: List[OutboxEintrag] = []
        for coid in await self._port.read(_VERZEICHNIS):
            eintrag = await self.lese(coid)
            if eintrag is None or eintrag.zustand in _ERLEDIGT:
                continue
            if symbol is not None and eintrag.symbol != symbol:
                continue
            ergebnis.append(eintrag)
        return ergebnis

    # -- Schreiben -----------------------------------------------------------

    async def festschreiben(self, eintrag: OutboxEintrag) -> bool:
        """Schreibt den Intent dauerhaft fest. ``True``, wenn er danach in der Ablage liegt.

        Doppeltes Festschreiben ist kein Fehler und aendert nichts — weder entsteht ein
        zweiter Eintrag, noch faellt ein fortgeschrittener Zustand auf ``offen`` zurueck.
        """
        coid = eintrag.client_order_id
        async with _Abschnitt(self._port, coid) as gesperrt:
            if not gesperrt:
                return False
            if await self._port.get(_PRAEFIX + coid) is not None:
                return True
            # Erst der Eintrag, dann das Verzeichnis. Stirbt der Prozess dazwischen,
            # liegt ein Eintrag ohne Verzeichniszeile da — unsichtbar, aber die Order
            # ist dann auch noch nicht gesendet. Umgekehrt zeigte das Verzeichnis auf
            # nichts.
            await self._port.set(
                _PRAEFIX + coid, replace(eintrag, zustand="offen").als_json()
            )
            await self._port.append(_VERZEICHNIS, coid)
            return True

    async def als_abgesendet(self, client_order_id: str) -> bool:
        return await self._schreibe_fort(client_order_id, "abgesendet")

    async def als_bestaetigt(
        self, client_order_id: str, *, broker_order_id: str
    ) -> bool:
        return await self._schreibe_fort(
            client_order_id, "bestaetigt", broker_order_id=str(broker_order_id or "")
        )

    async def als_verworfen(self, client_order_id: str, *, grund: str) -> bool:
        """Der Intent wird **sicher** nicht zur Order — das Tor hat abgelehnt.

        Nicht fuer Broker-Fehler: Bei einer Zeitueberschreitung weiss niemand, ob die
        Order angekommen ist. Der Intent bleibt dann ``abgesendet`` und gehoert dem
        Abgleich.
        """
        return await self._schreibe_fort(client_order_id, "verworfen", grund=grund)

    async def _schreibe_fort(self, coid: str, ziel: str, **felder: str) -> bool:
        """``True``, wenn der Eintrag jetzt in ``ziel`` steht. Wirft nicht bei Unbekanntem."""
        async with _Abschnitt(self._port, coid) as gesperrt:
            if not gesperrt:
                return False
            eintrag = await self.lese(coid)
            if eintrag is None:
                logger.warning(
                    "[Outbox] Meldung '%s' zu unbekanntem Intent %s (#3449).",
                    ziel,
                    coid,
                )
                return False
            if _RANG[ziel] <= _RANG.get(eintrag.zustand, 0):
                # Vorwaerts oder gar nicht. Eine verspaetete Meldung ist kein Fehler.
                return eintrag.zustand == ziel
            await self._port.set(
                _PRAEFIX + coid, replace(eintrag, zustand=ziel, **felder).als_json()
            )
            return True

    # -- Raeumen -------------------------------------------------------------

    async def aufraeumen(self) -> int:
        """Entfernt Erledigtes **vom Kopf** des Verzeichnisses. Gibt die Anzahl zurueck.

        Nur vom Kopf, weil der Port eine Warteschlange bietet und kein Loeschen aus der
        Mitte. Ein unbestaetigter Intent am Kopf haelt die Raeumung an: lieber ein
        Verzeichnis, das waechst, als ein Intent, der verschwindet.
        """
        geraeumt = 0
        async with _Abschnitt(self._port, "verzeichnis") as gesperrt:
            if not gesperrt:
                return 0
            for coid in await self._port.read(_VERZEICHNIS):
                roh = await self._port.get(_PRAEFIX + coid)
                if roh is not None:
                    eintrag = OutboxEintrag.aus_json(roh)
                    # Unlesbar heisst nicht erledigt. Ein Eintrag, den wir nicht
                    # verstehen, bleibt liegen — er koennte eine offene Order sein.
                    if eintrag is None or eintrag.zustand not in _ERLEDIGT:
                        break
                await self._port.pop(_VERZEICHNIS)
                await self._port.delete(_PRAEFIX + coid)
                geraeumt += 1
        return geraeumt


class _Abschnitt:
    """Der kritische Abschnitt um einen Eintrag — mit begrenztem Warten.

    Liefert ``False`` statt zu werfen, wenn die Sperre nicht zu bekommen ist: Die Outbox
    steht im Absendepfad, und was dann geschieht, entscheidet der Aufrufer — nicht eine
    Ausnahme aus der Tiefe.
    """

    def __init__(self, port: StatePort, name: str) -> None:
        self._sperre = port.lock(f"outbox:{name}", ttl_seconds=_SPERRFRIST_S)
        self._name = name
        self._gehalten = False

    async def __aenter__(self) -> bool:
        for _ in range(_SPERRVERSUCHE):
            if await self._sperre.acquire():
                self._gehalten = True
                return True
            await asyncio.sleep(_SPERRPAUSE_S)
        logger.warning(
            "[Outbox] Sperre um '%s' nach %d Versuchen nicht erhalten (#3449).",
            self._name,
            _SPERRVERSUCHE,
        )
        return False

    async def __aexit__(self, *exc_info) -> None:
        if self._gehalten:
            await self._sperre.release()


# ---------------------------------------------------------------------------
# #3449 Schritt 2 — Neustart: der Haken fuer die Wiederaufnahme und der Abgleich
# ---------------------------------------------------------------------------


async def _alle_eintraege(port: StatePort) -> List[OutboxEintrag]:
    eintraege = []
    for coid in await port.read(_VERZEICHNIS):
        roh = await port.get(_PRAEFIX + coid)
        eintrag = OutboxEintrag.aus_json(roh) if roh is not None else None
        if eintrag is not None:
            eintraege.append(eintrag)
    return eintraege


def offener_intent(symbol: str, datenverzeichnis: str):
    """Der Intent, an den ein Neustart fuer ``symbol`` anknuepfen muss — oder ``None``.

    Das ist der **juengste nicht verworfene** Intent des Symbols, bestaetigte eingeschlossen.
    Warum auch bestaetigte: Stirbt der Prozess nach dem Fill, bevor er es sich selbst notiert
    hat (CH-4, Szenario 2), weiss nur noch die Outbox, dass fuer dieses Symbol eine Order
    beim Broker liegt. Ein Neustart, der das nicht erfaehrt, faellt eine neue Entscheidung —
    und kauft ein zweites Mal.

    Synchron und fuer einen eigenen Prozess gedacht (Ketten-Vorrichtung,
    ``tests/chain/_ein_intent.py``): Er oeffnet die SQLite-Ablage unter
    ``datenverzeichnis`` in einem eigenen Ereignisring. Aus einem laufenden Ring heraus
    aufgerufen, wirft er — dort gehoert ``Outbox`` direkt benutzt.
    """
    import os

    pfad = os.path.join(datenverzeichnis, "engine_state.db")
    if not os.path.exists(pfad):
        return None

    async def _lies() -> Optional[OutboxEintrag]:
        from core.state.sqlite_adapter import SqliteStateAdapter

        port = SqliteStateAdapter(pfad)
        try:
            kandidaten = [
                e
                for e in await _alle_eintraege(port)
                if e.symbol == symbol and e.zustand != "verworfen"
            ]
        finally:
            await port.aclose()
        if not kandidaten:
            return None
        # Veraenderbar: Wer an den Intent anknuepft, notiert daran weiter (etwa die
        # Broker-Order-ID). Ein eingefrorener Eintrag brach den Neustart ab (so gemessen).
        from types import SimpleNamespace

        juengster = kandidaten[-1]
        return SimpleNamespace(
            **asdict(juengster), alpaca_order_id=juengster.broker_order_id or None
        )

    return asyncio.run(_lies())


@dataclass
class AbgleichBericht:
    bestaetigt: int = 0
    verworfen: int = 0
    ungeklaert: int = 0
    fremd: int = 0


def _ist_unbekannt(exc: Exception) -> bool:
    """Nur ein 404 heisst „der Broker kennt diesen Schluessel nicht"."""
    return getattr(exc, "status_code", None) == 404


async def abgleichen(
    outbox: "Outbox", client, *, konten: Optional[set] = None
) -> AbgleichBericht:
    """Gleicht unbestaetigte Intents nach einem Neustart beim Broker ab — **ohne zu senden**.

    Owner-Entscheid vom 18.09.2026: nie blind nachsenden.

    * Broker kennt den Schluessel → ``bestaetigt``.
    * Broker kennt ihn nicht (404) → ``verworfen``; der naechste Zyklus entscheidet frisch.
      Ein Stop-Exit geht dabei nicht verloren: Die Positions-Stops werden jeden Zyklus neu
      geprueft.
    * Jeder andere Fehler → **liegen lassen**. Ein Netzfehler ist kein „nicht angekommen";
      ihn als solchen zu werten, verwuerfe eine echte Order.

    ``konten``: nur Intents dieser Konten werden abgeglichen. Ein Mandanten-Intent waere im
    Konto eines anderen Clients „unbekannt" und wuerde faelschlich verworfen.
    """
    bericht = AbgleichBericht()
    for eintrag in await outbox.unbestaetigte():
        konto = eintrag.user_id or "global"
        if konten is not None and konto not in konten:
            bericht.fremd += 1
            continue
        try:
            order = await asyncio.to_thread(
                client.get_order_by_client_id, eintrag.client_order_id
            )
        except Exception as exc:
            if _ist_unbekannt(exc):
                await outbox.als_verworfen(
                    eintrag.client_order_id,
                    grund="nach Neustart nicht beim Broker — nie nachgesendet (#3449)",
                )
                bericht.verworfen += 1
            else:
                logger.warning(
                    "[Outbox] Abgleich fuer %s nicht moeglich, bleibt offen: %s",
                    eintrag.client_order_id,
                    exc,
                )
                bericht.ungeklaert += 1
            continue
        await outbox.als_bestaetigt(
            eintrag.client_order_id, broker_order_id=str(getattr(order, "id", "") or "")
        )
        bericht.bestaetigt += 1
    return bericht
