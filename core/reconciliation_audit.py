"""Protokoll der Abgleich-Sperre (#3430).

Eine JSON-Zeile je Bedienhandlung, unter ``USER_DATA_DIR`` wie das Kill-Switch-Protokoll
(``core/kill_switch.py``). Anders als dort ist das Schreiben hier **nicht** fail-safe: Die
Aufhebung gibt Einstiege auf echtem Kapital frei, und das Akzeptanzkriterium verlangt, dass sie
festgehalten ist. Laesst sich der Eintrag nicht schreiben, wirft ``festhalten`` — und die
Sperre bleibt. Eine stehende Sperre haelt nur Einstiege zurueck; Schutz-Exits laufen weiter.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.audit_paths import resolve_audit_log_path

logger = logging.getLogger(__name__)

DATEINAME = "reconciliation_audit.log"


def _anhaengen(pfad: str, zeile: str) -> None:
    with open(pfad, "a", encoding="utf-8") as datei:
        datei.write(zeile + "\n")
        datei.flush()


def festhalten(event: str, **felder: Any) -> dict:
    """Schreibt den Eintrag und gibt ihn zurueck. Wirft, wenn er nicht geschrieben werden kann.

    Der Pfad wird je Aufruf aufgeloest, nicht beim Import: ``AAA_USER_DATA_DIR`` setzt der
    Desktop-Start, und eine Bedienhandlung ist selten genug, dass das nichts kostet.
    """
    satz = {"event": event, "ts": datetime.now(timezone.utc).isoformat(), **felder}
    zeile = json.dumps(satz, default=str, ensure_ascii=False)
    _anhaengen(resolve_audit_log_path(DATEINAME), zeile)
    # Zusaetzlich ins Log: In Cloud Run ist die Datei fluechtig, die Logzeile nicht.
    logger.warning("ReconciliationAudit %s", zeile)
    return satz
