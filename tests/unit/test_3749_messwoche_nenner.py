"""#3749 — die Messwoche braucht ihren Nenner, auch ohne einen einzigen Befund.

``_report`` schreibt eine Laufbuch-Zeile bisher **nur fuer nicht saubere Laeufe**
(frueher Ruecksprung im sauberen Zweig). Der Nenner ``laeufe`` steht ausschliesslich IN
diesen Zeilen. Gibt es in der Messwoche (ADR-R02, #3677) keinen Befund, bleibt die Datei
leer — und ein leeres Laufbuch ist nicht unterscheidbar von „die Engine lief nie".

Genau der Fall, den die Woche belegen soll (Fehlalarmquote im Normalbetrieb nahe null),
waere damit **nicht belegbar**. Deshalb schreibt der saubere Zweig alle
``LAUFBUCH_NENNER_JE_LAEUFE`` Laeufe eine Zeile mit ``grund="nenner"``.

Die Schreibrichtung bleibt fail-soft wie in #3677: Mitschreiben ist eine Beobachtung,
kein Tor.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc5


@pytest.fixture
def dienst(tmp_path, monkeypatch):
    from core import reconciliation as modul

    monkeypatch.setattr(
        modul,
        "resolve_audit_log_path",
        lambda name: str(tmp_path / name),
    )
    d = modul.ReconciliationService(api=MagicMock(), redis_client=MagicMock())
    return d


def _satz(*, breaks=()):
    from core.contracts.reconciliation_record import ReconciliationRecord

    jetzt = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
    return ReconciliationRecord(
        run_id="lauf-1",
        started_at=jetzt,
        finished_at=jetzt,
        broker_orders=3,
        broker_positions=2,
        breaks=tuple(breaks),
    )


def _zeilen(dienst) -> list[dict]:
    pfad = Path(dienst._laufbuch_pfad)
    if not pfad.exists():
        return []
    return [
        json.loads(z)
        for z in pfad.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def test_nach_dem_intervall_steht_eine_nenner_zeile(dienst):
    """Der Kern: eine Woche ohne Befund muss trotzdem zaehlbar sein."""
    from core import reconciliation as modul

    for _ in range(modul.LAUFBUCH_NENNER_JE_LAEUFE):
        dienst._report(_satz())

    zeilen = _zeilen(dienst)
    assert len(zeilen) == 1, (
        f"Nach {modul.LAUFBUCH_NENNER_JE_LAEUFE} sauberen Laeufen steht "
        f"{len(zeilen)} Zeile(n) im Laufbuch — ohne Nenner-Zeile ist eine Woche ohne "
        "Befund nicht von einer Woche ohne Engine zu unterscheiden."
    )
    assert zeilen[0]["grund"] == "nenner"
    assert zeilen[0]["offen"] == 0
    assert zeilen[0]["laeufe"] == modul.LAUFBUCH_NENNER_JE_LAEUFE


def test_dazwischen_bleibt_das_laufbuch_leer(dienst):
    """Kein Geschwaetz: 119 saubere Laeufe schreiben nichts."""
    from core import reconciliation as modul

    for _ in range(modul.LAUFBUCH_NENNER_JE_LAEUFE - 1):
        dienst._report(_satz())

    assert _zeilen(dienst) == []


def test_ein_offener_befund_schreibt_weiterhin_sofort(dienst):
    from core.contracts.reconciliation_record import ReconciliationBreak

    bruch = ReconciliationBreak(kind="orphaned_order", order_id="x-1")
    dienst._report(_satz(breaks=[bruch]))

    zeilen = _zeilen(dienst)
    assert len(zeilen) == 1
    assert zeilen[0]["grund"] == "befund"
    assert zeilen[0]["offen"] == 1


def test_ein_geheilter_befund_schreibt_weiterhin_sofort(dienst):
    from core.contracts.reconciliation_record import ReconciliationBreak

    bruch = ReconciliationBreak(kind="missing_fill", order_id="x-2", geheilt=True)
    dienst._report(_satz(breaks=[bruch]))

    zeilen = _zeilen(dienst)
    assert len(zeilen) == 1
    assert zeilen[0]["grund"] == "befund"
    assert zeilen[0]["offen"] == 0
    assert zeilen[0]["geheilt"] == 1


def test_ein_schreibfehler_haelt_den_abgleich_nicht_an(dienst, monkeypatch, caplog):
    """Fail-soft wie #3677: Mitschreiben ist eine Beobachtung, kein Tor."""
    from core import reconciliation as modul

    def _explodiert(*a, **k):
        raise OSError("Datentraeger voll")

    monkeypatch.setattr("builtins.open", _explodiert)

    for _ in range(modul.LAUFBUCH_NENNER_JE_LAEUFE):
        dienst._report(_satz())  # darf nicht werfen

    assert any("Laufbuch nicht schreibbar" in s for s in caplog.messages)
