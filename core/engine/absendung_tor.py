# core/engine/absendung_tor.py
# ARC-E6 H-1h (#4237) — Outbox und Tor, ausgezogen aus core/engine/order_executor.py.
"""Outbox und Tor: die eine Stelle, ueber die der Executor zum Broker geht (#3447).

Hierher gezogen sind die fuenf freien Funktionen der Outbox (#3449) und der
Duplikat-Uebernahme (#3473) samt Modulzustand und Konstanten, dazu ``TorMixin`` mit
``_submit_with_market_failsafe`` und ``_sende_durchs_tor``. Die Ruempfe sind wortgleich mit
dem Stand im Executor, Schnitt-Entscheidung #4183
(``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1h). Einzige
Ausnahme ist der Schnitt von ``_sende_durchs_tor`` unter 150 Zeilen: Die innere Funktion
``_absenden`` wurde ``_tor_absenden`` mit ausdruecklichen Parametern, die Sperren vor dem
Festschreiben (Lease #3453, Abgleich #3491) wurden ``_tor_gesperrt``.

``TorMixin`` ist **Basisklasse** von ``OrderExecutorMixin``, nicht komponiert: Die Aufrufer
rufen das Tor ohne Instanz als ``OrderExecutorMixin._sende_durchs_tor`` (Entscheidung §3,
„Klassenbezuege"), und ``patch.object(oe.OrderExecutorMixin, ...)`` trifft nur so. Darum
rufen auch die Methoden hier einander ueber ``order_executor.OrderExecutorMixin``, nie
ueber ``self`` oder ``TorMixin``.

Abbildung nach der Zugriffsregel (Entscheidung §3, Weg b): ``config``, ``kill_switch``,
``gateway_for``, ``logging`` und ``OrderExecutorMixin`` werden als ``order_executor.<name>``
gelesen — die Tests ersetzen sie am Kern. ``_OUTBOX_OHNE_ABLAGE_GEMELDET`` ist Modulzustand
dieses Moduls (Weg a). Der Kern importiert dieses Modul und re-exportiert
``_outbox_sitzung``, ``_outbox_schritt`` und ``ist_duplikat``; dieses Modul importiert den
Kern **nur funktionslokal** (kein Zirkelimport, Muster ``monitor_loop.py``).

Plan: ``docs/4237-*/implementation_plan.md``.
"""

import asyncio
import contextlib
import os
import uuid
from typing import Optional

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

import core.engine.broker_stop_pflege as _pflege
from core.contracts import OrderIntent
from core.exceptions import TradingHaltedError
from core.idempotency import ersatz_decision_id, next_attempt

_OUTBOX_OHNE_ABLAGE_GEMELDET = False


@contextlib.asynccontextmanager
async def _outbox_sitzung():
    """Die Outbox fuer genau eine Absendung (oder einen Abgleich) — oder ``None`` (#3449).

    ``None`` heisst: Die Order geht ohne Festschreiben hinaus. Das ist die Richtung, die der
    Owner am 18.09.2026 festgelegt hat — Buchfuehrung verhindert nie eine Order, erst recht
    keinen Stop-Loss.

    Aus bleibt sie, wenn das Flag aus ist, und **ohne dauerhafte Ablage**: weder
    ``AAA_USER_DATA_DIR`` (Desktop) noch ``REDIS_URL`` (Enterprise). Eine Zustandsdatei
    relativ zum Arbeitsverzeichnis ist keine Installation — im Repo lagen dort schon 1,3 GB
    Hinterlassenschaften von Testlaeufen (#3468).

    Der Port ist kurzlebig (``zusammenbau.kurzer_port``): Eine offen gelassene
    SQLite-Verbindung hielte den Prozess beim Beenden fest.
    """
    from core.engine import order_executor

    global _OUTBOX_OHNE_ABLAGE_GEMELDET
    if not getattr(order_executor.config.get_config(), "ORDER_OUTBOX_ENABLED", True):
        yield None
        return
    dauerhaft = (
        os.environ.get("AAA_USER_DATA_DIR", "").strip()
        or os.environ.get("REDIS_URL", "").strip()
    )
    if not dauerhaft:
        if not _OUTBOX_OHNE_ABLAGE_GEMELDET:
            _OUTBOX_OHNE_ABLAGE_GEMELDET = True
            order_executor.logging.warning(
                "[Outbox] inaktiv: keine dauerhafte Ablage (weder AAA_USER_DATA_DIR "
                "noch REDIS_URL). Orders gehen ohne Festschreiben hinaus (#3449)."
            )
        yield None
        return
    try:
        from core.outbox import Outbox
        from core.state import zusammenbau

        stapel = contextlib.AsyncExitStack()
        port = await stapel.enter_async_context(zusammenbau.kurzer_port())
    except Exception:  # noqa: BLE001 — Buchfuehrung verhindert keine Order
        order_executor.logging.exception(
            "[Outbox] Ablage nicht erreichbar — die Order geht OHNE Festschreiben "
            "hinaus (#3449)"
        )
        yield None
        return
    # Nicht ``async with stapel``: Das Oeffnen darf die Order nicht verhindern, das Schliessen
    # ihr Ergebnis nicht verschlucken. Beide Fehler werden gemeldet, keiner weitergereicht —
    # und das Schliessen erfaehrt, ob der Rumpf gescheitert ist (Review #3476).
    try:
        yield Outbox(port)
    except BaseException as fehler:
        await _outbox_schliessen(stapel, type(fehler), fehler, fehler.__traceback__)
        raise
    else:
        await _outbox_schliessen(stapel, None, None, None)


async def _outbox_schliessen(stapel, art, fehler, spur) -> None:
    from core.engine import order_executor

    try:
        await stapel.__aexit__(art, fehler, spur)
    except Exception:  # noqa: BLE001
        order_executor.logging.exception(
            "[Outbox] Ablage liess sich nicht schliessen (#3449)"
        )


#: Alpacas Antwort auf einen bereits bekannten ``client_order_id`` (#3473, belegt im
#: Kommentar an #3473: „How to Fix 30 Common Errors in Alpaca's Trading API").
_DUPLIKAT_STATUS = 422
_DUPLIKAT_CODE = 40010001
_DUPLIKAT_TEXT = "client_order_id must be unique"


def ist_duplikat(fehler: BaseException) -> bool:
    """Weist der Broker den Auftrag ab, WEIL sein Schluessel schon bekannt ist? (#3473)

    Eng mit Absicht: HTTP 422 **und** (Code 40010001 **oder** genau dieser Text). Eine
    abgelehnte Order fuer angekommen zu halten, ist der teure Irrtum — im Zweifel bleibt
    es ein Fehler. Fehlt der HTTP-Status, ist es keins.
    """
    if not isinstance(fehler, APIError):
        return False
    try:
        status = fehler.status_code
    except Exception:  # noqa: BLE001 — unlesbar heisst: kein Beleg
        return False
    if status != _DUPLIKAT_STATUS:
        return False
    try:
        code = fehler.code
    except Exception:  # noqa: BLE001
        code = None
    try:
        text = str(fehler.message)
    except Exception:  # noqa: BLE001
        text = str(fehler)
    return code == _DUPLIKAT_CODE or _DUPLIKAT_TEXT in text


async def _bestehende_order(client, coid, symbol: str, fehler: BaseException):
    """Die Order, die der Broker unter ``coid`` bereits haelt — oder ``None`` (#3473).

    ``None`` heisst: Der Fehler bleibt ein Fehler. Das gilt, wenn es kein Duplikat ist, kein
    Schluessel vorliegt, das Nachschlagen scheitert oder die gefundene Order zu einem anderen
    Symbol gehoert (dann stimmt am Schluessel etwas nicht — nie eine fremde Order ausgeben).
    """
    from core.engine import order_executor

    if not (isinstance(coid, str) and coid) or not ist_duplikat(fehler):
        return None
    try:
        bestehend = await asyncio.to_thread(client.get_order_by_client_id, coid)
    except Exception:  # noqa: BLE001 — ohne die Order bleibt es beim Fehler
        order_executor.logging.exception(
            "[Order] %s: Duplikat %s gemeldet, die bestehende Order liess sich nicht "
            "holen — bleibt ein Fehler (#3473).",
            symbol,
            coid,
        )
        return None
    gefunden = str(getattr(bestehend, "symbol", "") or "")
    if bestehend is None or (gefunden and gefunden != symbol):
        order_executor.logging.error(
            "[Order] %s: Duplikat %s gehoert laut Broker zu %r — keine Order ausgegeben "
            "(#3473).",
            symbol,
            coid,
            gefunden,
        )
        return None
    order_executor.logging.warning(
        "[Order] %s: Schluessel %s war dem Broker bekannt — bestehende Order %s "
        "uebernommen, keine zweite gesendet (#3473).",
        symbol,
        coid,
        getattr(bestehend, "id", "?"),
    )
    return bestehend


async def _outbox_schritt(was: str, schritt) -> None:
    """Fuehrt einen Outbox-Schritt aus; ein Fehler wird laut gemeldet, nie weitergereicht."""
    from core.engine import order_executor

    try:
        await schritt
    except Exception:  # noqa: BLE001 — Buchfuehrung verhindert keine Order
        order_executor.logging.exception(
            "[Outbox] %s fehlgeschlagen — die Order ist davon nicht betroffen (#3449)",
            was,
        )


class TorMixin:
    """Das Tor des Executors; Basisklasse von ``OrderExecutorMixin`` (#4237, H-1h)."""

    async def _submit_with_market_failsafe(
        self,
        *,
        client,
        primary_req,
        symbol: str,
        qty: float,
        side_enum: OrderSide,
        user_id,
        is_exit: bool,
        decision_id: str = "",
        is_protective_exit: bool = False,
    ):
        """#2558 fail-safe — submit an order; if it is a marketable-limit EXIT that the
        broker rejects, fall back to a plain MARKET order so the position always closes.

        For a non-exit (BUY) the broker rejection propagates unchanged to the caller's
        existing displacement-recovery / daily-slot-refund handler — buy-side behaviour
        is byte-identical to today. If the market fallback ALSO raises, that error
        propagates (fail-closed) and the caller handles it as a normal submit failure.
        """
        from core.engine import order_executor

        # #3447 Schritt 2: beide Versuche gehen durchs Tor (ADR-019 §1). Vorher riefen sie
        # den Broker unmittelbar — ohne ComplianceDecision, und ein gescheiterter Aufruf
        # hinterliess keinen Datensatz.
        tor = {
            "client": client,
            "symbol": symbol,
            "side_enum": side_enum,
            "qty": qty,
            "user_id": user_id,
            "decision_id": decision_id,
            "is_protective_exit": is_protective_exit,
        }
        try:
            return await order_executor.OrderExecutorMixin._sende_durchs_tor(
                request=primary_req, **tor
            )
        except APIError as submit_err:
            if not is_exit:
                raise
            order_executor.logging.warning(
                "[User %s] %s marketable-limit EXIT rejected by broker (%s) — "
                "submitting MARKET fallback so the position still closes (#2558).",
                user_id,
                symbol,
                submit_err,
            )
            # #3387: War `str(uuid.uuid4())`. Das ist der gefaehrliche Fall — eine
            # ZWEITE Order fuer dasselbe Leg, die der Broker nicht als Folgeversuch
            # erkennen kann. Der Schluessel des Erstversuchs steckt im primary_req;
            # `next_attempt` behaelt den Entscheidungsanteil und zaehlt den Versuch hoch.
            market_req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side_enum,
                time_in_force=TimeInForce.DAY,
                client_order_id=next_attempt(
                    getattr(primary_req, "client_order_id", None) or str(uuid.uuid4())
                ),
            )
            return await order_executor.OrderExecutorMixin._sende_durchs_tor(
                request=market_req, **tor
            )

    @staticmethod
    async def _sende_durchs_tor(
        *,
        client,
        request,
        symbol: str,
        side_enum,
        qty: float,
        user_id,
        decision_id: str = "",
        is_protective_exit: bool = False,
        intent_kind: Optional[str] = None,
    ):
        """Setzt eine Order ueber das ``OrderGateway`` ab und gibt die Broker-Order zurueck.

        Die eine Stelle, ueber die der Executor kuenftig zum Broker geht (#3447, ADR-019
        §1: „Kein Pfad spricht den Broker direkt an"). Das Tor laeuft im Thread, weil
        sein Broker-Aufruf blockiert — dieselbe Begruendung wie beim ``to_thread``, das
        hier vorher stand.

        **Was das Tor hier entscheidet — und was nicht.** Ohne Vorabentscheid blockiert
        es genau einen Fall: Halt bei einem nicht freigestellten Intent. Dieses Urteil
        hat das Halt-Tor des Aufrufers bereits gefaellt (``halt_exempt_protective_exit``
        / ``check_halt``); es wird als ``is_protective_exit`` hereingereicht, damit das
        Tor es **nicht ein zweites Mal anders** faellt. Neu ist allein der Fall, dass
        der Halt *zwischen* Halt-Tor und Absendung eintritt — bisher ging die Order dann
        trotzdem hinaus.

        Eine Ablehnung wird als dieselbe Art Ausnahme gemeldet, die ``check_halt``
        wirft: Die Fehlerbehandlung des Aufrufers greift unveraendert.
        """
        from core.engine import order_executor

        seite = "sell" if str(side_enum).lower().endswith("sell") else "buy"
        art = "stop" if is_protective_exit else ("entry" if seite == "buy" else "trim")
        # #3449: Kam keine Entscheidung herein, steckt sie oft im Schluessel der Anfrage
        # (``entry-0-<decision_id>``, #3387). Ein Ersatzschluessel an ihrer Stelle liesse
        # den Neustart einen anderen Schluessel ableiten — zwei Orders fuer eine
        # Entscheidung (CH-4, so gemessen).
        if not (isinstance(decision_id, str) and decision_id):
            from core.idempotency import decision_id_aus

            decision_id = (
                decision_id_aus(getattr(request, "client_order_id", None)) or ""
            )
        # Nur die Verdraengung darf die Art uebersteuern: Sie ist aus Seite und
        # Schutz-Kennzeichen nicht ableitbar. Alles andere bleibt abgeleitet — eine frei
        # waehlbare Art waere ein frei waehlbarer Weg an der Halt-Pruefung vorbei.
        if intent_kind == "displacement" and not is_protective_exit:
            art = "displacement"
        intent = OrderIntent(
            # Faellt auf denselben Ersatzschluessel zurueck wie der aufgeschobene
            # Verdraengungs-Verkauf — ein Datensatz ohne Entscheidungsbezug ist
            # schlechter als einer mit, aber besser als eine nicht abgesetzte Order.
            decision_id=(
                decision_id
                if isinstance(decision_id, str) and decision_id
                # Kein String oder leer: Ersatzschluessel statt Validierungsfehler. Ein
                # Buchfuehrungsproblem darf NIEMALS eine Order verhindern — waere der
                # Auftrag ein Stop-Loss, bliebe die Position offen wegen eines Feldes,
                # das nur der Nachvollziehbarkeit dient.
                else ersatz_decision_id(art, user_id, symbol)
            ),
            symbol=symbol,
            side=seite,
            qty=float(qty),
            intent_kind=art,
            is_protective_exit=is_protective_exit,
            # Im Thread, nicht im Ereignisring: `is_halted` ist auf Enterprise ein
            # synchroner Redis-Aufruf. Dieselbe Lehre wie POLICY-01 zu #3452 — diesmal
            # vor dem Review angewandt statt danach.
            halted=await asyncio.to_thread(
                order_executor.kill_switch.is_halted, user_id
            ),
        )
        # #3429 + #3449: Fehlt der Schluessel, ergaenzt ihn das Tor. Die Outbox muss unter
        # DEMSELBEN Schluessel festschreiben, sonst gleicht der Neustart nichts ab. Darum
        # wendet die Absendestelle die Regel des Tors vorher an — eine Regel, zwei Nutzer;
        # das Tor behaelt einen gesetzten Schluessel, der Auftrag kommt unveraendert an.
        from core.gateway.order_gateway import mit_schluessel

        request = mit_schluessel(intent, request)
        # #3449: erst festschreiben, dann senden. Schluessel ist der Idempotenz-Schluessel
        # der Anfrage (#3387) — eine Order, ein Schluessel, ein Eintrag.
        coid = getattr(request, "client_order_id", None)
        if not (isinstance(coid, str) and coid):
            # Ohne Schluessel gibt es nichts, unter dem der Neustart abgleichen koennte
            # (Ersatz-Entscheidung, Auftragsform ohne Feld). Die Order geht trotzdem.
            coid = None

        # #3453 + #3491: die Sperren, VOR dem Festschreiben (``_tor_gesperrt``). Eine
        # zurueckgehaltene Order darf keinen offenen Intent hinterlassen, den der Neustart
        # fuer unterwegs hielte; die Ausnahme ist dieselbe Art wie die von ``check_halt``.
        if grund := await order_executor.OrderExecutorMixin._tor_gesperrt(
            symbol=symbol, user_id=user_id, art=art
        ):
            raise TradingHaltedError(grund)

        if seite == "sell":  # #4033: eigene Broker-Stops binden die Stuecke (40310000)
            intent, request = await _pflege.gib_verkauf_frei(client, intent, request)
        # #4237 (H-1h): Vorher las die innere Funktion ``_absenden`` ``intent`` und
        # ``request`` erst beim Aufruf — also NACH der Freigabe. Darum gehen hier die
        # Werte nach ``gib_verkauf_frei`` hinein, ``coid`` wie zuvor von davor.
        werte = {
            "client": client,
            "intent": intent,
            "request": request,
            "coid": coid,
            "art": art,
            "seite": seite,
            "symbol": symbol,
            "user_id": user_id,
        }
        if not (isinstance(coid, str) and coid):
            return await order_executor.OrderExecutorMixin._tor_absenden(None, **werte)
        async with _outbox_sitzung() as outbox:
            return await order_executor.OrderExecutorMixin._tor_absenden(
                outbox, **werte
            )

    @staticmethod
    async def _tor_gesperrt(*, symbol, user_id, art) -> Optional[str]:
        """Lease (#3453) und Abgleich-Sperre (#3491): der Text der Ausnahme oder ``None``.

        Ausgezogen aus ``_sende_durchs_tor`` (#4237, H-1h). Meldet wie zuvor; die Ausnahme
        wirft der Aufrufer. Reihenfolge unveraendert: Lease vor Abgleich.
        """
        # #3453: Nur der Schreiber des Kontos sendet — auch einen Schutz-Exit (Issue, Schritt
        # 6). Geprueft VOR dem Festschreiben: Eine zurueckgehaltene Order darf keinen offenen
        # Intent hinterlassen, den der Neustart fuer unterwegs hielte. Ein Erwerbsversuch,
        # kein Warten; die Ausnahme ist dieselbe Art wie die von ``check_halt``, damit die
        # Fehlerbehandlung des Aufrufers unveraendert greift.
        from core import lease as _lease
        from core.engine import order_executor

        _konto = _lease.konto_schluessel(
            str(user_id) if user_id else None,
            paper=bool(
                getattr(order_executor.config.get_config(), "PAPER_TRADING", True)
            ),
        )
        if not await _lease.darf_schreiben(_konto):
            order_executor.logging.warning(
                "[Order] %s NICHT abgesetzt: Diese Instanz haelt die Schreibberechtigung "
                "fuer %s nicht — eine andere schreibt (#3453).",
                symbol,
                _konto,
            )
            return (
                f"TRADING HALTED: {symbol} nicht abgesetzt — keine Schreibberechtigung "
                f"fuer {_konto} (#3453)."
            )

        # #3491: die Abgleich-Sperre (#3389) haelt Risikoaufnahme an, nie den Rueckzug —
        # ``blocks`` laesst Schutz-Exits, Trims, Verdraengung und die Notpfade immer durch.
        # Ohne laufenden Abgleich keine Pruefung. Vor dem Festschreiben, wie die Sperre oben.
        from core import reconciliation as _abgleich

        _dienst = _abgleich.aktiver_abgleich()
        if _dienst is not None and _dienst.blocks(art):
            order_executor.logging.warning(
                "[Order] %s NICHT abgesetzt: Abgleich-Sperre — %s (#3389/#3491). Aufheben "
                "nur durch einen Menschen (/api/reconciliation/release).",
                symbol,
                (
                    "Abweichung zwischen Broker und Engine"
                    if _dienst.entries_blocked
                    else "Start-Abgleich noch nicht gelaufen"
                ),
            )
            return (
                f"TRADING HALTED: {symbol} nicht abgesetzt — Abgleich-Sperre (#3491)."
            )
        return None

    @staticmethod
    async def _tor_absenden(
        outbox, *, client, intent, request, coid, art, seite, symbol, user_id
    ):
        """Festschreiben, Abgesendet-Vermerk, Tor, Duplikat-Uebernahme, Verwerfen, Bestaetigen.

        Vormals die innere Funktion ``_absenden`` von ``_sende_durchs_tor`` (#4237, H-1h);
        die Namen der Closure sind jetzt Parameter, der Rumpf ist wortgleich.
        """
        from core.engine import order_executor

        if coid is None:
            outbox = None
        if outbox is not None:
            from core.outbox import OutboxEintrag

            await _outbox_schritt(
                "Festschreiben",
                outbox.festschreiben(
                    OutboxEintrag(
                        decision_id=intent.decision_id,
                        leg=art,
                        client_order_id=coid,
                        symbol=symbol,
                        side=seite,
                        qty=intent.qty,
                        user_id=str(user_id or ""),
                    )
                ),
            )
            # Vor dem Tor-Aufruf: Stirbt der Prozess danach, weiss der Neustart, dass
            # diese Order unterwegs sein kann — und gleicht sie ab, statt zu senden.
            await _outbox_schritt("Abgesendet-Vermerk", outbox.als_abgesendet(coid))

        # Wirft der Broker, bleibt der Intent ``abgesendet``: Ob die Order ankam,
        # weiss niemand — das klaert der Abgleich beim naechsten Start.
        try:
            entscheidung, order = await asyncio.to_thread(
                order_executor.gateway_for(client).submit_with_result,
                intent,
                request=request,
            )
        except Exception as fehler:
            # #3473: Kennt der Broker den Schluessel schon, ist die Order bereits
            # dort — eine idempotente Absendung liefert dann dieselbe Order wie beim
            # ersten Mal. Alles andere bleibt ein Fehler.
            bestehend = await _bestehende_order(client, coid, symbol, fehler)
            if bestehend is None:
                raise
            if outbox is not None:
                await _outbox_schritt(
                    "Bestaetigen",
                    outbox.als_bestaetigt(
                        coid,
                        broker_order_id=str(getattr(bestehend, "id", "") or ""),
                    ),
                )
            return bestehend
        if not entscheidung.approved:
            if outbox is not None:
                await _outbox_schritt(
                    "Verwerfen",
                    outbox.als_verworfen(
                        coid, grund=f"Tor: {entscheidung.reason_code.value}"
                    ),
                )
            raise TradingHaltedError(
                f"TRADING HALTED: {symbol} nicht abgesetzt — das Tor hat abgelehnt "
                f"({entscheidung.reason_code.value}: {entscheidung.detail})"
            )
        if outbox is not None:
            await _outbox_schritt(
                "Bestaetigen",
                outbox.als_bestaetigt(
                    coid, broker_order_id=str(getattr(order, "id", "") or "")
                ),
            )
        return order
