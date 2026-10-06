"""#3899 — schreibt mit, welche Tests ein pytest-Lauf eingesammelt hat.

Aufgerufen aus dem Root-`conftest.py` (`pytest_collection_finish`). Ohne die Variable
`KOLLEKTION_JOB` tut es nichts — lokal und in jedem Job, der sie nicht setzt, bleibt
alles beim Alten.

Format: die Ausgabe von `pytest --collect-only -q` (`datei.py::test`), davor die Zeile
`# wurzel: <rootdir relativ zum Repository>`. Genau das liest
`scripts/bericht_kollektion.py`. Kein Import von dort: `ai_trading_bot/` importiert
keine Dev-Werkzeuge (Airlock, CLAUDE.md §5.7) — die Naht haelt ein Test fest.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

VARIABLE = "KOLLEKTION_JOB"
VERZEICHNIS = "kollektion"

# Ein Jobname, kein Pfad: Buchstaben, Ziffern, Bindestrich, Unterstrich.
_JOBNAME = re.compile(r"^[A-Za-z0-9_-]+$")


def aufzeichnen(
    env: Mapping[str, str],
    rootdir: Path,
    repo: Path,
    node_ids: Iterable[str],
) -> Path | None:
    """Haengt die Node-IDs an `<repo>/kollektion/<job>.txt` an; gibt den Pfad zurueck.

    Der Job nennt nur seinen Namen, keinen Pfad: In Container-Jobs zeigt
    `github.workspace` auf den Host, nicht in den Container.

    Anhaengen statt ueberschreiben: Ein Job ruft pytest teils mehrfach
    (`backend-iron-dome`, ci.yml:278 und :289), und jeder Aufruf zaehlt. Unter
    pytest-xdist sammelt jeder Arbeiter dieselben Tests ein — nur `gw0` schreibt,
    sonst stuenden vier Prozesse gleichzeitig in derselben Datei.
    """
    job = env.get(VARIABLE, "").strip()
    if not _JOBNAME.match(job):
        return None
    arbeiter = env.get("PYTEST_XDIST_WORKER", "")
    if arbeiter and arbeiter != "gw0":
        return None
    try:
        wurzel = Path(os.path.relpath(Path(rootdir).resolve(), Path(repo).resolve()))
        wurzel_text = wurzel.as_posix()
        if wurzel_text.startswith(".."):
            wurzel_text = "."
    except ValueError:  # anderes Laufwerk unter Windows
        wurzel_text = "."
    pfad = Path(repo) / VERZEICHNIS / f"{job}.txt"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    with pfad.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# wurzel: {wurzel_text}\n")
        for node_id in node_ids:
            fh.write(f"{node_id}\n")
    return pfad
