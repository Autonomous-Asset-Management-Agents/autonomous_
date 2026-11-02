"""#4287 (H-5f) — Das Handelsbuch lebt in ``core/portfolio_handelsbuch.py``.

Plan: ``docs/4287-*/implementation_plan.md`` §6. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitt H-5f.

Der Kern erbt die acht Methoden von ``HandelsbuchMixin`` und führt ``_trading_day_et`` nur
noch als Re-Export. Den Kern liest der Test über ``inspect`` (Import), nicht über einen
Dateipfad: ein Pfad-Leser wäre ein Befund von ``test_h5_schnitt_portfolio_manager.py``.
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

HANDELSBUCH = "core.portfolio_handelsbuch"
KERN = "core.portfolio_manager"
METHODEN = (
    "_can_trade_symbol_when_room",
    "_within_order_cooldown",
    "_can_trade_symbol",
    "record_trade",
    "can_sell_position",
    "record_sell_signal",
    "reset_sell_signals",
    "clear_sell_signals_after_sale",
)
MODULNAMEN = ("_MARKET_TZ", "_trading_day_et")


def test_handelsbuch_lebt_in_portfolio_handelsbuch():
    modul = importlib.import_module(HANDELSBUCH)
    assert modul.HandelsbuchMixin.__module__ == HANDELSBUCH
    assert modul._trading_day_et.__module__ == HANDELSBUCH
    fehlt = [n for n in METHODEN if n not in vars(modul.HandelsbuchMixin)]
    assert not fehlt, f"HandelsbuchMixin definiert nicht selbst: {fehlt}"
    assert "__init__" not in vars(modul.HandelsbuchMixin)


def test_der_kern_erbt_das_handelsbuch():
    mixin = importlib.import_module(HANDELSBUCH).HandelsbuchMixin
    kern = importlib.import_module(KERN).PortfolioManager
    assert mixin in kern.__mro__
    anders = [n for n in METHODEN if getattr(kern, n) is not getattr(mixin, n)]
    assert not anders, f"PortfolioManager löst nicht auf das Mixin auf: {anders}"


def test_der_kern_definiert_sie_nicht_mehr():
    baum = ast.parse(inspect.getsource(importlib.import_module(KERN)))
    gesucht = set(METHODEN) | set(MODULNAMEN)
    knoten = list(baum.body)
    for k in baum.body:
        if isinstance(k, ast.ClassDef) and k.name == "PortfolioManager":
            knoten += k.body
    definiert = set()
    for k in knoten:
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definiert.add(k.name)
        elif isinstance(k, ast.Assign):
            definiert.update(t.id for t in k.targets if isinstance(t, ast.Name))
    assert not sorted(definiert & gesucht), f"{KERN} definiert weiter selbst"


def test_re_export_ist_dasselbe_objekt():
    kern = importlib.import_module(KERN)
    handelsbuch = importlib.import_module(HANDELSBUCH)
    assert kern._trading_day_et is handelsbuch._trading_day_et


def test_freie_namen_sind_im_modul_gebunden():
    """Ein vergessener Import bräche erst beim Aufruf mit ``NameError`` — hier vorher."""
    modul = importlib.import_module(HANDELSBUCH)
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
            frei |= {
                n.id
                for n in ast.walk(k)
                if isinstance(n, ast.Name)
                and isinstance(n.ctx, ast.Load)
                and n.id not in lokal
            }
    assert not sorted(frei - gebunden), "freie Namen ohne Bindung im Modul"
