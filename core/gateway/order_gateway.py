"""OrderGateway — ein Tor zum Broker (#3379, ARC-E1.3, Epic #3366).

Der Befund des Epics ist nicht „eine Stufe ruft die naechste falsch", sondern: **es gibt
Pfade, die an allen Stufen vorbeigehen**. 21 Stellen im Kern setzen heute Orders ab; der
Verdraengungs-SELL meldet sich erst danach beim Iron Dome, Notverkauf, Breaker und
Strategiewechsel schreiben ueberhaupt keine Entscheidung. Solange das so ist, laesst sich
„keine Kapitalbewegung ohne geprueffte Entscheidung" nicht belegen.

Dieses Modul ist der eine Ort, an dem ein Broker-Aufruf stehen soll.

**Schnitt dieses Schritts.** Das Gateway uebernimmt hier drei Dinge: den Halt-Zustand
(mit der Freistellung fuer Schutz-Exits aus #3380), den **Datensatz** zu jeder Order — auch
zu jeder abgelehnten — und den Broker-Aufruf selbst. Die Compliance-Pruefung bleibt
vorerst dort, wo sie heute steht, und reicht ihr Ergebnis als ``ComplianceDecision``
herein. Sie mitzuverschieben wuerde bedeuten, `record_trade` und das Wash-Fenster in
derselben Aenderung zu bewegen — die Rueckrollstufe waere dann das ganze Epic statt ein
Pfad (Plan §4, Option C ausdruecklich verworfen).

**Die eine Regel, die ueber allem steht:** Ein Schutz-Exit wird protokolliert, aber nie
blockiert. Ein Tor, das alles prueft, ist genau dann gefaehrlich, wenn es auch den
Notausgang prueft.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode

logger = logging.getLogger(__name__)


#: Die Freistellungs-Matrix — Intent-Arten, die protokolliert, aber nie blockiert werden.
#:
#: ``displacement``: Owner-Entscheid vom 16.09.2026 zu #3279 (Option B). Ein geblockter
#: Verdraengungs-SELL liesse das Konto mit einer Position ueber dem Buch-Deckel und einer
#: bereits ausgefuehrten BUY-Seite zurueck — genau der Zustand, den #2712 vermeiden
#: wollte, und auf dem Default-Pfad gibt es dorthin keinen Rollback-Weg.
#:
#: Schutz-Exits stehen NICHT in dieser Liste: sie sind ueber ``is_protective_exit``
#: gekennzeichnet, weil das Kennzeichen am Intent haengt und nicht an seiner Art.
#:
#: ``panic``, ``breaker``, ``strategy_switch`` (#3383): die drei Ausnahmepfade bewegen auf
#: einen Schlag den groessten Teil des Bestands. Sie duerfen nicht an einer Einstiegsregel
#: haengenbleiben — ein Notverkauf, den eine Einstiegspruefung abweist, ist ein zugesperrter
#: Notausgang. Besonders beim Notverkauf: er legt den Kill-Switch selbst um, bevor er
#: liquidiert (``api_routes.py``, ``kill_switch.trip``); wuerde der Halt danach greifen,
#: haette er sich selbst ausgesperrt. Protokolliert werden sie trotzdem, mit eigenem
#: Grund-Code — genau das fehlte bisher.
#:
#: Wer hier etwas hinzufuegt, nimmt einen Pfad von der Pruefung aus. Das gehoert begruendet
#: in den PR — und in einen Test.
NEVER_BLOCKED_KINDS = frozenset({"displacement", "panic", "breaker", "strategy_switch"})

#: Welcher Grund-Code die Freistellung im Datensatz benennt. Ohne diese Zuordnung traege
#: jede freigestellte Bewegung dasselbe ``exit_exempt`` — ein Notverkauf waere im Audit
#: nicht von einem Schutz-Stop zu unterscheiden.
EXEMPT_REASON_BY_KIND = {
    "panic": ReasonCode.EMERGENCY,
    "breaker": ReasonCode.BREAKER,
    "strategy_switch": ReasonCode.STRATEGY_SWITCH,
}


class KwargsAuftrag(dict):
    """Ein Auftrag in der Kwargs-Form (#3447, Schritt 3).

    Der alpaca-py-``TradingClient`` nimmt **ein** Request-Objekt. Clients ohne
    ``order_data``-Parameter (Tests, Altclients des Strategiepfads) erwarten dagegen
    ``submit_order(symbol=…, qty=…, side=…, …)``. Ein eigener Typ, damit ein Auftrag nie mit
    einem Request verwechselt wird — und damit das Tor beide Formen ueber **eine**
    Aufrufstelle absetzt. Ein Adapter daneben waere ein zweiter Weg zum Broker.
    """


def mit_schluessel(intent: OrderIntent, request: Any) -> Any:
    """#3429: Das Tor garantiert den Idempotenz-Schluessel — wo es das verantworten kann.

    * Ein **gesetzter** Schluessel hat immer Vorrang (der HITL-Freigabepfad setzt ihn bewusst).
    * Fehlt er, wird er abgeleitet aus Entscheidung **und Symbol**: Eine Liquidation benutzt
      eine ``decision_id`` fuer alle Positionen — ohne Symbol haette jede denselben Schluessel,
      und der Notverkauf bliebe ab dem zweiten Symbol als Duplikat stehen.
    * Aus einer **Ersatz-ID** wird kein Schluessel: Sie ist nicht eindeutig je Entscheidung.
    * Eine Form ohne das Feld (``KwargsAuftrag``) wird gemeldet, nicht umgebaut.

    Der Auftrag des Aufrufers wird nie veraendert; das Tor sendet eine Kopie.
    """
    from pydantic import BaseModel

    from core.idempotency import derive_client_order_id, ist_ersatz

    if isinstance(request, KwargsAuftrag):
        logger.warning(
            "[Gateway] %s: Auftragsform ohne Schluesselfeld — die Order geht ohne "
            "Idempotenz-Schluessel hinaus (#3429).",
            intent.symbol,
        )
        return request
    if (
        not isinstance(request, BaseModel)
        or "client_order_id" not in type(request).model_fields
    ):
        return request
    vorhanden = getattr(request, "client_order_id", None)
    if isinstance(vorhanden, str) and vorhanden:
        return request
    if ist_ersatz(intent.decision_id):
        logger.info(
            "[Gateway] %s: Ersatz-Entscheidung %s — kein abgeleiteter Schluessel, weil sie "
            "nicht eindeutig je Entscheidung ist (#3429).",
            intent.symbol,
            intent.decision_id,
        )
        return request
    try:
        schluessel = derive_client_order_id(
            f"{intent.decision_id}-{intent.symbol}", intent.intent_kind, 0
        )
    except ValueError as exc:
        logger.warning(
            "[Gateway] %s: kein Schluessel ableitbar (%s) — Order geht ohne (#3429).",
            intent.symbol,
            exc,
        )
        return request
    return request.model_copy(update={"client_order_id": schluessel})


class OrderGateway:
    """Nimmt einen ``OrderIntent`` entgegen, entscheidet, fuehrt aus, protokolliert.

    Die Abhaengigkeiten kommen von aussen herein — Broker, Halt-Abfrage, Datensenke —,
    damit das Tor ohne Engine, ohne Netz und ohne Konfiguration pruefbar bleibt. Das ist
    zugleich die Naht, an der spaeter der ``BrokerPort`` aus #3398 ansetzt.
    """

    def __init__(
        self,
        *,
        broker: Any,
        is_halted: Callable[..., bool],
        record: Callable[[ComplianceDecision], None],
    ) -> None:
        self._broker = broker
        self._is_halted = is_halted
        self._record = record

    # -- Entscheidung ------------------------------------------------------

    def _decide(
        self, intent: OrderIntent, prior: Optional[ComplianceDecision]
    ) -> ComplianceDecision:
        """Die Pruefreihenfolge dieses Schritts: Schutz-Exit → Halt → Vorabentscheid.

        Der Schutz-Exit steht **vorn**, nicht hinten. Stuende er am Ende, muesste jede
        neue Pruefung daran denken, ihn auszunehmen — und die erste, die es vergisst,
        sperrt den Notausgang zu.
        """
        halted = bool(self._is_halted(getattr(intent, "user_id", None)))

        freigestellt = (
            intent.is_protective_exit or intent.intent_kind in NEVER_BLOCKED_KINDS
        )
        if freigestellt:
            detail = (
                "Schutz-Exit: protokolliert, nicht blockiert"
                if intent.is_protective_exit
                else f"{intent.intent_kind}: protokolliert, nicht blockiert (Freistellungs-Matrix)"
            )
            if prior is not None and not prior.approved:
                # Die Ablehnung geht nicht verloren — sie wird sichtbar getragen.
                detail = f"{detail} (uebergangene Ablehnung: {prior.reason_code.value})"
                logger.warning(
                    "[Gateway] %s: Schutz-Exit trotz Ablehnung '%s' ausgefuehrt (#3379).",
                    intent.symbol,
                    prior.reason_code.value,
                )
            if halted:
                logger.warning(
                    "[Gateway] %s: Schutz-Exit trotz Halt ausgefuehrt (#3380).",
                    intent.symbol,
                )
            return ComplianceDecision(
                decision_id=intent.decision_id,
                approved=True,
                reason_code=EXEMPT_REASON_BY_KIND.get(
                    intent.intent_kind, ReasonCode.EXIT_EXEMPT
                ),
                detail=detail,
                halted=halted,
            )

        if halted:
            return ComplianceDecision(
                decision_id=intent.decision_id,
                approved=False,
                reason_code=ReasonCode.TRADING_HALTED,
                detail="Handel gehalten — nur Schutz-Exits passieren",
                halted=True,
            )

        if prior is not None:
            # Das Gateway ueberstimmt eine getroffene Pruefung nicht. Es fuehrt sie aus.
            return prior

        return ComplianceDecision(
            decision_id=intent.decision_id,
            approved=True,
            reason_code=ReasonCode.APPROVED,
            detail="",
            halted=False,
        )

    # -- Ausfuehrung -------------------------------------------------------

    def submit(
        self,
        intent: OrderIntent,
        *,
        request: Any,
        decision: Optional[ComplianceDecision] = None,
    ) -> ComplianceDecision:
        """Setzt den Intent um und gibt die getroffene Entscheidung zurueck.

        ``request`` ist die broker-spezifische Auftragsform. Sie bleibt vorerst beim
        Aufrufer, weil die fuenf heutigen Request-Formen unterschiedlich gebaut werden;
        ihre Vereinheitlichung gehoert zu #3398 (BrokerPort), nicht hierher.
        """
        entschieden, _antwort = self.submit_with_result(
            intent, request=request, decision=decision
        )
        return entschieden

    def submit_with_result(
        self,
        intent: OrderIntent,
        *,
        request: Any,
        decision: Optional[ComplianceDecision] = None,
    ) -> "tuple[ComplianceDecision, Any]":
        """Wie ``submit``, gibt aber zusaetzlich die Antwort des Brokers zurueck (#3447).

        Die Haupt-Absendungen des Executors brauchen das Order-Objekt: Sie pollen mit
        ``order.id`` bis zur Fuellung. Solange das Tor die Antwort verwarf, konnte keiner
        dieser Pfade hindurch — und der CH-2-Zaehler waere nie auf null gekommen.

        ``submit`` delegiert hierher. Es bleibt bei **einem** Weg zum Broker; zwei
        Methoden mit je eigenem Aufruf waeren zwei Wege, und der zweite der, den
        irgendwann jemand vergisst abzusichern.

        Returns:
            ``(entscheidung, broker_antwort)`` — bei einer Ablehnung ist die Antwort
            ``None``: kein erfundener Platzhalter, der Aufrufer liest den Unterschied an
            ``entscheidung.approved`` ab.
        """
        entschieden = self._decide(intent, decision)

        if not entschieden.approved:
            self._record(entschieden)
            return entschieden, None

        try:
            request = mit_schluessel(intent, request)
            if isinstance(request, KwargsAuftrag):
                argumente, schluessel = (), dict(request)
            else:
                argumente, schluessel = (request,), {}
            # Die eine Stelle, an der das Tor den Broker ruft — fuer beide Formen.
            antwort = self._broker.submit_order(*argumente, **schluessel)
        except Exception as exc:
            # Ein gescheiterter Versuch ist eine Tatsache und gehoert in den Datensatz.
            # Ohne ihn sieht das Audit eine Entscheidung ohne Order und kann nicht
            # unterscheiden, ob sie nie abgesetzt wurde oder unterwegs verlorenging.
            gescheitert = ComplianceDecision(
                decision_id=intent.decision_id,
                approved=entschieden.approved,
                reason_code=ReasonCode.SYSTEM_ERROR,
                detail=f"Broker-Aufruf fehlgeschlagen: {exc}",
                halted=entschieden.halted,
            )
            self._record(gescheitert)
            logger.error(
                "[Gateway] %s: Broker-Aufruf fehlgeschlagen: %s", intent.symbol, exc
            )
            raise

        self._record(entschieden)
        return entschieden, antwort
