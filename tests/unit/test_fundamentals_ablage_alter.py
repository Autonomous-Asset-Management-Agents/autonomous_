"""#4571 Schritt 3.4 — das Alter der Fundamentals-Ablage ist beim Start sichtbar.

Heute altert die Ablage lautlos. Gemessen am 20.10.2026: **476 Dateien, alle vom
03.09.2026 14:20 — 47 Tage alt**, und kein Wort davon im Protokoll. Der
``FundamentalsAgent`` meldete stattdessen ``EXCLUDED — no verified fundamentals data
yet``, was wie eine Datenlücke aussieht und keine war.

**Beim Start und nicht je Zyklus.** Zwei Gruende, im Plan entschieden: dort laeuft die
Saat schon (``api_routes.py``), eine Altersmeldung gehoert in dieselbe Boot-Diagnose; und
je Zyklus stuende dieselbe WARNING alle paar Minuten im Protokoll, was eine Warnung
entwertet.

Das ist der Teil, der verhindert, dass dieses Ticket ein zweites Mal entsteht.

Benennung: die Tests heissen ``alterspruefung`` statt ``altersprüfung`` wie im Plan —
der Bestand transliteriert Umlaute durchgaengig (``juengstes``, ``Pruefung``), und ein
Unicode-Bezeichner im Testnamen laeuft Gefahr, in Werkzeugketten zu verrutschen.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core.report import financials_snapshot as snap


def _ablage(anzahl: int, alter_tage: float) -> str:
    """Eine Ablage mit ``anzahl`` Dateien, deren Aenderungszeit ``alter_tage`` zurueckliegt."""
    d = tempfile.mkdtemp()
    ts = (datetime.now(timezone.utc) - timedelta(days=alter_tage)).timestamp()
    for i in range(anzahl):
        p = os.path.join(d, f"SYM{i}.json")
        with open(p, "w", encoding="utf-8") as h:
            h.write("{}")
        os.utime(p, (ts, ts))
    return d


@pytest.mark.vc1
def test_alterspruefung_warnt_ab_frist(caplog):
    """Der gemessene Fall: 47 Tage alt bei einer Frist von 25."""
    d = _ablage(3, 47)
    with caplog.at_level(logging.DEBUG):
        alter = snap.melde_ablage_alter(d, frist_tage=25)

    assert alter is not None and alter >= 46, f"Alter {alter!r}"
    assert any(
        r.levelno >= logging.WARNING for r in caplog.records
    ), f"keine WARNING: {[r.levelname for r in caplog.records]}"


@pytest.mark.vc1
def test_alterspruefung_schweigt_bei_frischer_ablage(caplog):
    """Eine Warnung, die immer kommt, ist keine Warnung."""
    d = _ablage(3, 2)
    with caplog.at_level(logging.DEBUG):
        snap.melde_ablage_alter(d, frist_tage=25)

    assert not [
        r for r in caplog.records if r.levelno >= logging.WARNING
    ], "frische Ablage darf nicht warnen"


@pytest.mark.vc1
def test_alterspruefung_nennt_datum_und_anzahl(caplog):
    """Ohne Datum und Dateizahl ist die Meldung nicht handlungsfaehig."""
    d = _ablage(7, 60)
    with caplog.at_level(logging.WARNING):
        snap.melde_ablage_alter(d, frist_tage=25)

    text = " ".join(r.getMessage() for r in caplog.records)
    erwartetes_datum = (
        (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
    )
    assert erwartetes_datum in text, f"Datum fehlt: {text!r}"
    assert "7" in text, f"Dateizahl fehlt: {text!r}"


@pytest.mark.vc1
def test_alterspruefung_vertraegt_eine_leere_ablage(caplog):
    """Beim ersten Start ist die Ablage leer — das ist kein Altersproblem."""
    with caplog.at_level(logging.DEBUG):
        assert snap.melde_ablage_alter(tempfile.mkdtemp(), frist_tage=25) is None
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.vc1
def test_alterspruefung_vertraegt_ein_fehlendes_verzeichnis(caplog):
    """Darf den Start nie aufhalten."""
    with caplog.at_level(logging.DEBUG):
        assert snap.melde_ablage_alter("/gibt/es/nicht", frist_tage=25) is None


@pytest.mark.vc1
def test_boot_ruft_die_alterspruefung_nach_der_saat():
    """Der Aufruf existiert — und steht NACH der Saat, sonst meldet er das Alter von
    Dateien, die der Lauf gerade selbst geschrieben hat."""
    import pathlib

    quelle = (
        pathlib.Path(__file__).resolve().parents[2]
        / "core"
        / "engine"
        / "api_routes.py"
    ).read_text(encoding="utf-8")

    assert "melde_ablage_alter" in quelle, "die Altersmeldung ist nicht verdrahtet"
    assert quelle.index("seed_bundled_fundamentals") < quelle.index(
        "melde_ablage_alter"
    ), "die Altersmeldung steht vor der Saat"


@pytest.mark.vc1
def test_4642_alter_ignoriert_fremde_datei(caplog):
    """#4642: Die Rueckkauf-Sperrliste darf das Alter der Fundamentaldaten nicht verdecken.

    Gemessen am 20.10.2026 an der Live-Ablage: ``melde_ablage_alter`` nahm
    ``reentry_lockout.json`` (29.09.) als juengste Datei und rechnete 20 Tage statt der
    wahren 33 — unter der Frist von 25, also **keine Warnung**, obwohl eine faellig war.
    Die Sperrliste wird bei jeder Ausstoppung neu geschrieben und ist damit regelmaessig
    die juengste Datei des Verzeichnisses.
    """
    d = _ablage(3, 40)
    fremd = os.path.join(d, "reentry_lockout.json")
    with open(fremd, "w", encoding="utf-8") as h:
        h.write('{"LOW": {"until": "2026-09-30"}}')  # heute geschrieben

    with caplog.at_level(logging.DEBUG):
        alter = snap.melde_ablage_alter(d, frist_tage=25)

    assert (
        alter is not None and alter >= 39
    ), f"Alter {alter!r} — die Fremddatei hat das Alter verdeckt"
    assert any(
        r.levelno >= logging.WARNING for r in caplog.records
    ), f"keine WARNING trotz 40 Tagen: {[r.levelname for r in caplog.records]}"
