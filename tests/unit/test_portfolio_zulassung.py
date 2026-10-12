"""#4288 (H-5g) — Zulassung und Nachkauf leben in ``core/portfolio_zulassung.py``.

Plan: ``docs/4288-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitt H-5g.

Der Kern erbt die acht Methoden von ``ZulassungMixin``; einen Re-Export gibt es nicht, weil
niemand sie über das Modul ``core.portfolio_manager`` liest. Den Kern liest der Test über
``inspect`` (Import), nicht über einen Dateipfad: ein Pfad-Leser wäre ein Befund von
``test_h5_schnitt_portfolio_manager.py``.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

ZULASSUNG = "core.portfolio_zulassung"
KERN = "core.portfolio_manager"
METHODEN = (
    "should_open_new_position",
    "_slot_hold_reason",
    "_reserved_slots",
    "_topup_target_pct",
    "_topup_dead_band_reason",
    "topup_gap_value",
    "_clean_weight_target_pct",
    "_regime_target_factor",
)


def test_zulassung_lebt_in_portfolio_zulassung():
    mixin = importlib.import_module(ZULASSUNG).ZulassungMixin
    assert mixin.__module__ == ZULASSUNG
    fehlt = [n for n in METHODEN if n not in vars(mixin)]
    assert not fehlt, f"ZulassungMixin definiert nicht selbst: {fehlt}"
    assert "__init__" not in vars(mixin)
    assert isinstance(vars(mixin)["_regime_target_factor"], staticmethod)


def test_der_kern_erbt_die_zulassung():
    mixin = importlib.import_module(ZULASSUNG).ZulassungMixin
    kern = importlib.import_module(KERN).PortfolioManager
    assert mixin in kern.__mro__
    anders = [n for n in METHODEN if getattr(kern, n) is not getattr(mixin, n)]
    assert not anders, f"PortfolioManager löst nicht auf das Mixin auf: {anders}"


def test_der_kern_definiert_sie_nicht_mehr():
    baum = ast.parse(inspect.getsource(importlib.import_module(KERN)))
    knoten = list(baum.body)
    for k in baum.body:
        if isinstance(k, ast.ClassDef) and k.name == "PortfolioManager":
            knoten += k.body
    definiert = {
        k.name for k in knoten if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not sorted(definiert & set(METHODEN)), f"{KERN} definiert weiter selbst"
    # Einen Namen, den der Kern nicht mehr liest, führt der Kern nicht mehr (#4187 §3).
    namen = {n.id for n in ast.walk(baum) if isinstance(n, ast.Name)}
    namen |= {
        a.asname or a.name
        for n in ast.walk(baum)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    assert "engine_now" not in namen, f"{KERN} führt engine_now weiter"


def test_freie_namen_sind_im_modul_gebunden():
    """Ein vergessener Import bräche erst auf dem Kaufpfad mit ``NameError`` — hier vorher."""
    modul = importlib.import_module(ZULASSUNG)
    baum = ast.parse(inspect.getsource(modul))
    gebunden = set(dir(builtins)) | set(vars(modul))
    frei = set()
    for k in ast.walk(baum):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lokal = {a.arg for a in ast.walk(k.args) if isinstance(a, ast.arg)}
            lokal |= {
                n.id
                for n in ast.walk(k)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
            }
            # späte Importe in den Rümpfen (#4187 §3) binden ihren Namen lokal
            lokal |= {
                (a.asname or a.name).split(".")[0]
                for n in ast.walk(k)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in n.names
            }
            lokal |= {
                n.name
                for n in ast.walk(k)
                if isinstance(n, ast.ExceptHandler) and n.name
            }
            frei |= {
                n.id
                for n in ast.walk(k)
                if isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)
                and n.id not in lokal
            }
    assert not sorted(frei - gebunden), "freie Namen ohne Bindung im Modul"


def test_kein_import_aus_dem_kern():
    baum = ast.parse(inspect.getsource(importlib.import_module(ZULASSUNG)))
    importe = set()
    for n in ast.walk(baum):
        if isinstance(n, ast.ImportFrom) and n.module:
            importe.add(n.module)
        elif isinstance(n, ast.Import):
            importe |= {a.name for a in n.names}
    assert KERN not in importe, f"{ZULASSUNG} importiert {KERN} (Entscheidung §3)"
