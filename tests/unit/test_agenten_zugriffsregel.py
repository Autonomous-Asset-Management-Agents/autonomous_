"""#3831 (ARC-E6 G-6a) — Agenten lesen geteilte Abhaengigkeiten ueber das Modulobjekt.

Tests patchen geteilte Abhaengigkeiten ueber ``core.round_table.agents.<name>``
(``get_global_registry`` 27-mal, ``_specialist_registry_instance`` 10-mal, dazu
``get_llm_provider`` und ``datetime``). Ein Modul unter ``agenten/``, das einen dieser Namen
selbst bindet, haelt danach seine eigene Bindung; der Patch greift nicht mehr, der Test wird
still falsch (das Muster von G-4a, ``test_routes_zugriffsregel.py``). Erlaubt ist nur der
Laufzeitzugriff ``from core.round_table import agents as _ag`` → ``_ag.get_global_registry()``.

Zwei Befunde:
    1. Jeder Import eines geteilten Namens (oder ``*``) aus ``core.round_table.agents``,
       gleich wo er steht.
    2. Jeder Import eines geteilten Namens auf **Modulebene**, gleich aus welcher Quelle
       (``from datetime import datetime``, ``from core.agent_registry import
       get_global_registry``): Er ersetzt die Bindung, die der Patch auf
       ``core.round_table.agents`` trifft. Funktionslokale Importe aus der Originalquelle
       sahen den Patch schon in ``agents.py`` nie; sie wandern verhaltensneutral mit.

Plan: ``docs/3831-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
AGENTEN = PAKET / "core" / "round_table" / "agenten"

GETEILTE_ABHAENGIGKEITEN = frozenset(
    {
        "get_global_registry",
        "_specialist_registry_instance",
        "_warmup_warned_symbols",  # #4084 (G-6b): Neuladen ersetzt die Menge
        "_MOMENTUM_ABSTAIN_WARNED",  # #4085 (G-6c): Tests leeren agents._MOMENTUM_ABSTAIN_WARNED
        "get_llm_provider",
        "datetime",
    }
)
_QUELLE = "core.round_table.agents"


def pruefe_zugriffsregel(verzeichnis: Path) -> list[str]:
    """Meldet jeden Direktimport einer geteilten Abhaengigkeit mit Datei und Zeile."""
    meldungen = []
    for datei in sorted(verzeichnis.rglob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        modulebene = {id(k) for k in baum.body}
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.ImportFrom):
                continue
            aus_agents = knoten.module == _QUELLE
            if not aus_agents and id(knoten) not in modulebene:
                continue
            for alias in knoten.names:
                if alias.name in GETEILTE_ABHAENGIGKEITEN or (
                    aus_agents and alias.name == "*"
                ):
                    meldungen.append(
                        f"{datei.relative_to(verzeichnis).as_posix()}:{knoten.lineno}: "
                        f"'{alias.name}' direkt aus {knoten.module} importiert — "
                        f"lies es zur Laufzeit ueber 'from core.round_table import "
                        f"agents as _ag' → '_ag.{alias.name}'."
                    )
    return meldungen


def _schreibe(wurzel: Path, name: str, quelle: str) -> None:
    (wurzel / name).write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_ein_direktimport_ist_ein_befund_mit_datei_und_zeile(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from core.round_table.base_agent import VotingAgent
        from core.round_table.agents import get_global_registry
        """,
    )
    (meldung,) = pruefe_zugriffsregel(tmp_path)
    assert "probe.py:2" in meldung
    assert "'get_global_registry'" in meldung


def test_jeder_geteilte_name_ist_verboten(tmp_path):
    namen = ", ".join(sorted(GETEILTE_ABHAENGIGKEITEN))
    _schreibe(tmp_path, "probe.py", f"from core.round_table.agents import {namen}\n")
    assert len(pruefe_zugriffsregel(tmp_path)) == len(GETEILTE_ABHAENGIGKEITEN)


def test_der_sternimport_ist_ein_befund(tmp_path):
    _schreibe(tmp_path, "probe.py", "from core.round_table.agents import *\n")
    assert len(pruefe_zugriffsregel(tmp_path)) == 1


def test_auch_ein_funktionslokaler_import_aus_agents_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        def vote():
            from core.round_table.agents import get_llm_provider
            return get_llm_provider()
        """,
    )
    assert len(pruefe_zugriffsregel(tmp_path)) == 1


def test_modulebene_aus_der_originalquelle_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from datetime import datetime, timezone
        from core.agent_registry import get_global_registry
        """,
    )
    meldungen = pruefe_zugriffsregel(tmp_path)
    assert len(meldungen) == 2
    assert "'datetime'" in meldungen[0] and "'get_global_registry'" in meldungen[1]


def test_der_zugriff_ueber_das_modulobjekt_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from datetime import timezone
        from core.round_table import agents as _ag
        from core.round_table.agents import _hilfsfunktion

        def vote():
            from datetime import datetime
            from core.agent_registry import get_global_registry
            return _ag.get_global_registry(), _ag.datetime.now(timezone.utc)
        """,
    )
    assert pruefe_zugriffsregel(tmp_path) == []


def test_zugriffsregel_gegen_den_code():
    assert AGENTEN.is_dir(), f"{AGENTEN} fehlt"
    meldungen = pruefe_zugriffsregel(AGENTEN)
    assert not meldungen, "\n".join(meldungen)
