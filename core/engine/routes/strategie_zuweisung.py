"""Helfer fuer Lesepfad und Loeschsperre der Strategie-Zuweisung (#4891, STW-4d, Epic #4625).

``zuweisungen.json`` spiegelt die Kette (Plan Option A): ``strategies_assign_live`` schreibt
den Spiegel nach dem WORM-Eintrag (``zuweisung_spiegeln``), ``strategies_delete`` prueft ihn
vor Nonce und Eintrag (``loeschsperre``), ``GET /api/strategy-assignments`` liest ihn ohne
Kettenzugriff (``zuweisungen_laden``). Eine Zuweisung zaehlt ab ihrem Eintrag; wirksam wird
sie erst beim Neustart durch die Shell.

Die Routen liegen in ``routes/steuerung.py``. Eine eigene Datei, weil Routen und Helfer
zusammen ueber der Datei-Schwelle von 1000 Zeilen laegen (``tests/architecture/vertrag.toml``,
``[groessen]``) — Muster ``routes/benchmark_daten.py``. Dieses Modul hat keine Routen.

Plan: ``docs/4891-stw-4d-feat-engine-read-path-for-the-live-assign/implementation_plan.md``.
"""

from __future__ import annotations

from typing import Dict

from core.engine import api_routes as ar
from core.engine.fehler import CodedHTTPException

#: Textgleich mit T48 in ``StrategyList.tsx`` — Console und API sagen dasselbe.
ZUGEWIESEN = (
    "This strategy is assigned to an account. "
    "Assign another strategy to that account first."
)


def zuweisungen_laden() -> Dict[str, Dict[str, str]]:
    """Konto → Zuweisung; unlesbar → 503 ``strategy_store_unreadable``."""
    from core import strategie_profile as sp

    try:
        return sp.lade_zuweisungen()
    except sp.StrategieAblageError as exc:
        raise CodedHTTPException(503, str(exc), code=exc.code) from exc


def loeschsperre(name: str) -> None:
    """409, wenn ``name`` einem Konto zugewiesen ist; 503, wenn das nicht feststellbar ist."""
    from core import strategie_profile as sp

    konten = sp.zugewiesene_konten(zuweisungen_laden(), name)
    if konten:
        ar.logger.info(
            "[STRATEGIE] Löschen von %r abgelehnt: zugewiesen an %s",
            name,
            ", ".join(konten),
        )
        raise CodedHTTPException(
            409,
            ZUGEWIESEN,
            code="strategy_assigned",
            params={"name": name, "accounts": konten},
        )


def zuweisung_spiegeln(name: str, strategie_hash: str, zeitpunkt: object) -> bool:
    """Spiegel nach gelungenem Eintrag schreiben; ``False`` = nicht geschrieben (WARNING).

    Scheitert er, bleibt die Zuweisung versiegelt und die Antwort 201: Die Shell muss sie
    wirksam machen. Anzeige und Sperre kennen dann bis zur naechsten Zuweisung den alten Stand.
    """
    from core import strategie_profile as sp

    try:
        if not isinstance(zeitpunkt, str) or not zeitpunkt:
            raise ValueError(f"no timestamp from the chain entry: {zeitpunkt!r}")
        sp.speichere_zuweisung("live", name, strategie_hash, zeitpunkt)
    except Exception as exc:  # noqa: BLE001 — versiegelt bleibt versiegelt
        ar.logger.warning(
            "[STRATEGIE] Zuweisung %r versiegelt, zuweisungen.json nicht geschrieben: %s",
            name,
            exc,
        )
        return False
    return True
