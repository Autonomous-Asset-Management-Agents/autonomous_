"""Zyklus-Typen und Zustandsschlüssel liegen in eigenen, zyklusfreien Modulen.

#4244 (H-2c), Teil von ARC-E6 (#3738). Reiner Umzug aus ``core/engine/trading_loop.py``
nach ``core/engine/zyklus.py`` (``Zyklus``, ``ZyklusZustand``) und
``core/engine/symbol_schluessel.py`` (``_POSITION_CONTEXT_CHANNELS`` und die fünf
``_*_state_keys``). Der Kern importiert sie wieder, damit bestehende Leser unverändert
gelten (Schnitt-Entscheidung #4184 §2).
Plan: ``docs/4244-*/implementation_plan.md`` §5.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.unit import _schleifen_quelle as sq

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/

SCHLUESSEL = (
    "_regime_conditioner_state_keys",
    "_implied_vol_state_keys",
    "_risk_reversal_state_keys",
    "_quality_state_keys",
    "_position_context_state_keys",
)
UMGEZOGEN = SCHLUESSEL + ("_POSITION_CONTEXT_CHANNELS", "Zyklus", "ZyklusZustand")


#: Laedt NUR die Moduldatei, am Paket ``core.engine`` vorbei: dessen ``__init__`` zieht
#: ``api_routes``/``BotEngine`` und damit den Kern ohnehin. Gemessen wird, was das Modul
#: selbst nachlaedt.
_SOLO = """
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("solo", sys.argv[1])
modul = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modul)
print(json.dumps({"module": sorted(sys.modules), "namen": sorted(vars(modul))}))
"""


def _solo_geladen(datei: str) -> dict:
    lauf = subprocess.run(
        [sys.executable, "-c", _SOLO, str(PAKET / "core" / "engine" / datei)],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr[-2000:]
    return json.loads(lauf.stdout.strip().splitlines()[-1])


def test_zyklus_modul_ist_zyklusfrei():
    geladen = _solo_geladen("zyklus.py")
    assert {"Zyklus", "ZyklusZustand"} <= set(geladen["namen"])
    assert "core.engine.trading_loop" not in geladen["module"]


def test_symbol_schluessel_modul_ist_zyklusfrei():
    geladen = _solo_geladen("symbol_schluessel.py")
    assert set(SCHLUESSEL) <= set(geladen["namen"])
    assert "core.engine.trading_loop" not in geladen["module"]


def test_kern_reicht_die_typen_durch():
    import core.engine.trading_loop as tl
    from core.engine import zyklus

    assert tl.Zyklus is zyklus.Zyklus
    assert tl.ZyklusZustand is zyklus.ZyklusZustand


@pytest.mark.parametrize("name", SCHLUESSEL)
def test_kern_reicht_die_schluessel_durch(name):
    import core.engine.trading_loop as tl
    from core.engine import symbol_schluessel

    assert getattr(tl, name) is getattr(symbol_schluessel, name)


def test_keine_definition_mehr_im_kern():
    baum = ast.parse(sq.texte_handelsschleife()[sq.PFAD])
    definiert = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            definiert.add(knoten.name)
        elif isinstance(knoten, ast.Assign):
            definiert.update(z.id for z in knoten.targets if isinstance(z, ast.Name))
        elif isinstance(knoten, ast.AnnAssign) and isinstance(knoten.target, ast.Name):
            definiert.add(knoten.target.id)
    assert not definiert & set(UMGEZOGEN)
