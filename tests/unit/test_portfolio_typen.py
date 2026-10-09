"""#4284 (H-5c) — Typen und Uhr leben in ``core/portfolio_typen.py``.

Plan: ``docs/4284-*/implementation_plan.md`` §6. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitt H-5c.

Der Kern führt die fünf Namen nur noch als Re-Export — dasselbe Objekt, keine eigene
Definition. Den Kern liest der Test über ``inspect`` (Import), nicht über einen Dateipfad:
ein Pfad-Leser wäre ein Befund von ``test_h5_schnitt_portfolio_manager.py``.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
TYPEN = "core.portfolio_typen"
KERN = "core.portfolio_manager"
NAMEN = (
    "_now_utc",
    "_ensure_aware_utc",
    "_book_cap_enforced",
    "PositionScore",
    "OpportunityScore",
)


def test_die_fuenf_symbole_leben_in_portfolio_typen():
    typen = importlib.import_module(TYPEN)
    herkunft = {n: getattr(typen, n).__module__ for n in NAMEN}
    assert herkunft == {n: TYPEN for n in NAMEN}


def test_der_kern_exportiert_dieselben_objekte():
    typen = importlib.import_module(TYPEN)
    kern = importlib.import_module(KERN)
    anders = [n for n in NAMEN if getattr(kern, n) is not getattr(typen, n)]
    assert not anders, f"{KERN} führt eigene Objekte statt des Re-Exports: {anders}"


def test_der_kern_definiert_sie_nicht_mehr():
    baum = ast.parse(inspect.getsource(importlib.import_module(KERN)))
    definiert = sorted(
        k.name
        for k in baum.body
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and k.name in NAMEN
    )
    assert not definiert, f"{KERN} definiert weiter selbst: {definiert}"


@pytest.mark.parametrize("reihenfolge", [(TYPEN, KERN), (KERN, TYPEN)])
def test_typen_laden_im_frischen_interpreter_zuerst(reihenfolge):
    erstes, zweites = reihenfolge
    probe = (
        "import importlib\n"
        f"a = importlib.import_module({erstes!r})\n"
        f"b = importlib.import_module({zweites!r})\n"
        f"typen = importlib.import_module({TYPEN!r})\n"
        f"kern = importlib.import_module({KERN!r})\n"
        f"assert all(getattr(kern, n) is getattr(typen, n) for n in {NAMEN!r})\n"
    )
    ergebnis = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ergebnis.returncode == 0, probe + ergebnis.stderr[-2000:]
