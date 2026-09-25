"""#3393 (ARC-E3) — Pruefskript fuer unbelegte Schliessungen, gegen feste Datensaetze.

Plan: ``docs/3393-board-wahrheit-autoclose/implementation_plan.md``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_SKRIPT = Path(__file__).resolve().parents[3] / "scripts" / "unbelegte_schliessungen.py"
_spec = importlib.util.spec_from_file_location("unbelegte_schliessungen", _SKRIPT)
us = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = us  # dataclasses brauchen das Modul in sys.modules
_spec.loader.exec_module(us)


def _knoten(nummer, closer, grund="COMPLETED", akteur="jemand"):
    return {
        "number": nummer,
        "title": f"Issue {nummer}",
        "closedAt": f"2026-09-14T12:00:{nummer % 60:02d}Z",
        "stateReason": grund,
        "timelineItems": {
            "nodes": [{"createdAt": "x", "actor": {"login": akteur}, "closer": closer}]
        },
    }


BOARD = {"__typename": "ProjectV2", "number": 5, "title": "AAA Portfolio Kanban"}
GEMERGT = {"__typename": "PullRequest", "number": 900, "merged": True}
NICHT_GEMERGT = {"__typename": "PullRequest", "number": 901, "merged": False}
COMMIT = {"__typename": "Commit", "oid": "abc"}


def test_eine_schliessung_ohne_belegenden_merge_erscheint_im_befund():
    alle = [
        us.aus_knoten(_knoten(1, BOARD, akteur="apeldorn")),
        us.aus_knoten(_knoten(2, GEMERGT)),
        us.aus_knoten(_knoten(3, None)),
        us.aus_knoten(_knoten(4, NICHT_GEMERGT)),
        us.aus_knoten(_knoten(5, COMMIT)),
    ]
    assert [s.nummer for s in us.unbelegte(alle)] == [1, 3, 4]


def test_der_bericht_nennt_weg_akteur_und_zahl():
    alle = [
        us.aus_knoten(_knoten(1, BOARD, akteur="apeldorn")),
        us.aus_knoten(_knoten(2, GEMERGT)),
    ]
    text = us.bericht(alle, "anfrage")
    assert "2 geschlossene Issues betrachtet, davon **1 ohne belegenden Merge**" in text
    assert "| #1 Issue 1 |" in text
    assert "Board-Zug (Projekt)" in text and "apeldorn" in text
    assert "#2 Issue 2" not in text


def test_auch_not_planned_ohne_merge_ist_unbelegt_und_traegt_den_grund():
    (s,) = us.unbelegte([us.aus_knoten(_knoten(7, None, grund="NOT_PLANNED"))])
    assert s.weg == "von Hand" and s.grund == "NOT_PLANNED"


def test_die_anfrage_fuer_einen_tag_betrachtet_genau_diesen_tag():
    anfrage = us.suchanfrage(us.REPO, tag="2026-09-14")
    assert anfrage == (
        "repo:Autonomous-Asset-Management-Agents/Dev-Enviroment is:issue is:closed "
        "closed:2026-09-14"
    )
    assert "is:pr" not in anfrage


def test_die_anfrage_seit_einem_tag():
    assert us.suchanfrage("o/r", seit="2026-09-01").endswith("closed:>=2026-09-01")


def test_lade_folgt_allen_seiten_und_zaehlt_vollstaendig():
    seiten = [
        {
            "issueCount": 3,
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
            "nodes": [_knoten(1, BOARD), _knoten(2, GEMERGT)],
        },
        {
            "issueCount": 3,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_knoten(3, BOARD)],
        },
    ]
    gesehen = []

    def abfrage(anfrage, nach):
        gesehen.append(nach)
        return seiten[len(gesehen) - 1]

    alle = us.lade("q", abfrage=abfrage)
    assert [s.nummer for s in alle] == [1, 2, 3] and gesehen == [None, "c1"]


def test_mehr_als_die_suche_liefert_wird_gemeldet_statt_abgeschnitten():
    def abfrage(anfrage, nach):
        return {"issueCount": 1500, "pageInfo": {"hasNextPage": False}, "nodes": []}

    with pytest.raises(RuntimeError, match="hoechstens 1000"):
        us.lade("q", abfrage=abfrage)


def test_das_skript_schreibt_nichts():
    """Nur lesend (Plan, CIA): kein mutierender gh-Aufruf im Skript."""
    quelle = _SKRIPT.read_text(encoding="utf-8")
    for verboten in (
        "mutation",
        "issue close",
        "issue reopen",
        "item-edit",
        "--method",
    ):
        assert verboten not in quelle, verboten
