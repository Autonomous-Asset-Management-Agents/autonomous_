"""#4443 — ``decision_outcomes`` erst nach ihrer Entscheidung senden.

Der ``CloudLogger``-Worker leerte zuerst ``_decision_queue`` und sendete sie, danach
``_decision_outcome_queue``. Ein Paar, das während des Decisions-Sendens eintraf, brachte
sein Ergebnis noch in diesem Durchlauf in die DB, seine Entscheidung erst im nächsten. Der
Fremdschlüssel ``decision_outcomes.decision_id -> decisions`` verwarf dann den ganzen
Batch in die Ersatzdatei (rc6, 09.10.2026: 133 Zeilen, alle Eltern kamen später an).

- **Option A:** Die Ergebnis-Warteschlange wird VOR der Entscheidungs-Warteschlange
  geleert. Jeder Produzent wartet ``log_decision`` ab, bevor er ``_capture_outcome``
  einreiht; die Entscheidung jedes geleerten Ergebnisses steckt also spätestens im
  Decisions-Batch, der unmittelbar danach geleert und gesendet wird.
- **Option C:** Ergebnisse, deren Elternzeile dennoch fehlt (HITL-Pfad, gescheiterter
  Decisions-Batch), werden zurückgestellt statt gesendet und nach
  ``OUTCOME_DEFER_MAX_CYCLES`` Durchläufen in die Ersatzdatei gelegt. Ein solches
  Ergebnis verwirft keinen Batch gültiger Zeilen mehr.

Die Funktionen arbeiten auf der ``CloudLogger``-Instanz (``lg``) und sind dort als
Methoden eingehängt. Nur ORM, kein Dialektzweig: SQLite (Desktop) und PostgreSQL
(Enterprise) verhalten sich gleich (BORA).
"""

from __future__ import annotations

import logging
import queue
from typing import Any, Dict, List, Tuple

try:
    from sqlalchemy import select

    from core.database.models import Decision
except Exception:  # pragma: no cover - ORM fehlt: der Logger hat dann keine DB
    select = Decision = None  # type: ignore[assignment,misc]

# Wie viele Worker-Durchläufe ein Ergebnis auf seine `decisions`-Zeile wartet, bevor es
# in die Ersatzdatei geht. Ein Durchlauf dauert ~0,5 s plus Sendezeit; 3 Durchläufe
# decken eine Entscheidung, die noch hinter dem laufenden Batch steht. Fehlt die
# Elternzeile danach noch, kommt sie auf diesem Weg nicht mehr (HITL, gescheiterter
# Decisions-Batch); ab dort übernehmen Ersatzdatei und `fallback_nachlesen`.
OUTCOME_DEFER_MAX_CYCLES = 3


def leere(q) -> List[Dict]:
    items: List[Dict] = []
    while q is not None and not q.empty():
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            break
    return items


def _zaehle(lg, schluessel: str, n: int) -> None:
    lg.stats[schluessel] = lg.stats.get(schluessel, 0) + n


async def drain_decisions_and_outcomes(lg) -> None:
    """Entscheidungen und ihre Ergebnisse, in jedem Durchlauf — Eltern zuerst.

    Haltbarkeit: in JEDEM Durchlauf geleert, nicht nur bei flush()/shutdown; eine hart
    beendete Desktop-Engine verlöre sonst alles Eingereihte (#2113, R-B).
    """
    # getattr-defensiv: von Hand gebaute Testinstanzen tragen die Queue evtl. nicht.
    ergebnisse = leere(getattr(lg, "_decision_outcome_queue", None))  # Option A: zuerst
    entscheidungen = leere(lg._decision_queue)
    if entscheidungen:
        await lg._send_batch("decisions", entscheidungen)

    zurueck = getattr(lg, "_deferred_outcomes", None)
    if zurueck is None:
        zurueck = lg._deferred_outcomes = []
    offen = zurueck + [[item, 0] for item in ergebnisse]
    zurueck.clear()
    if not offen:
        return

    bereit, verwaist = await teile_nach_eltern(lg, offen)
    lg._last_outcome_split = (len(lg._deferred_outcomes), len(verwaist))
    if verwaist:
        _zaehle(lg, "outcomes_orphaned", len(verwaist))
        try:
            logging.warning(
                "decision_outcomes: %d Ergebnis(se) ohne decisions-Zeile nach %d "
                "Durchläufen -> Ersatzdatei: %s",
                len(verwaist),
                OUTCOME_DEFER_MAX_CYCLES,
                ", ".join(str(o.get("decision_id")) for o in verwaist),
            )
        except ValueError:
            pass  # geschlossener Log-Stream beim Herunterfahren
        lg._write_fallback("decision_outcomes", verwaist)
    if bereit:
        await lg._send_batch("decision_outcomes", bereit)


async def teile_nach_eltern(
    lg, offen: List[List[Any]]
) -> Tuple[List[Dict], List[Dict]]:
    """Teilt ``[item, gewartet]``-Einträge danach, ob ihre Entscheidung in der DB steht.

    Gibt ``(bereit, verwaist)`` zurück; wartende Einträge gehen nach
    ``lg._deferred_outcomes``. Eine ``SELECT … IN (…)``-Abfrage je Aufruf. Ohne DB oder
    wenn die Abfrage selbst scheitert, ist alles ``bereit`` — ``_send_batch`` behält dann
    sein bisheriges Ersatzdatei-Verhalten.
    """
    items = [eintrag[0] for eintrag in offen]
    if select is None or not lg.is_connected or not hasattr(lg, "thread_session_maker"):
        return items, []
    ids = {str(i.get("decision_id")) for i in items if i.get("decision_id")}
    try:
        async with lg.thread_session_maker() as session:
            result = await session.execute(
                select(Decision.decision_id).where(Decision.decision_id.in_(ids))
            )
            vorhanden = {str(row[0]) for row in result}
    except Exception as e:
        try:
            logging.warning(
                "decision_outcomes: Elternprüfung gescheitert (%s) -> ungeprüft senden",
                e,
            )
        except ValueError:
            pass
        return items, []

    bereit: List[Dict] = []
    verwaist: List[Dict] = []
    for item, gewartet in offen:
        if str(item.get("decision_id")) in vorhanden:
            bereit.append(item)
        elif gewartet >= OUTCOME_DEFER_MAX_CYCLES:
            verwaist.append(item)
        else:
            lg._deferred_outcomes.append([item, gewartet + 1])
            _zaehle(lg, "outcomes_deferred", 1)
    return bereit, verwaist
