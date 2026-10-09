"""#4289 (H-5h+H-5i) — Gelegenheit und Verdrängung leben in ``core/portfolio_verdraengung.py``.

Plan: ``docs/4289-*/implementation_plan.md`` §6. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md``, Abschnitte H-5h und H-5i.

Der Kern erbt die sechs Methoden von ``VerdraengungMixin``; einen Re-Export gibt es nicht, weil
niemand die Konstanten oder ``_opportunity_score_cfg`` über das Modul ``core.portfolio_manager``
liest. Den Kern liest der Test über ``inspect`` (Import), nicht über einen Dateipfad: ein
Pfad-Leser wäre ein Befund von ``test_h5_schnitt_portfolio_manager.py``.

``test_debatte_grenzwerte`` hält die Zweiggrenzen von ``debate_position_swap`` fest, die die
bestehenden Verdrängungstests nicht eigens prüfen. Er ist gegen den unzerlegten Rumpf grün
gemacht worden und trägt die Zerlegung (H-5i).
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

VERDRAENGUNG = "core.portfolio_verdraengung"
KERN = "core.portfolio_manager"
METHODEN = (
    "score_opportunity",
    "_displacement_min_hold_days",
    "_displacement_session_cap",
    "_displacement_session_budget_ok",
    "_note_displacement",
    "debate_position_swap",
)
KONSTANTEN = (
    "_OPPORTUNITY_WEIGHT_SUM",
    "_CONFIDENCE_POINTS_PER_UNIT",
    "_CONFIDENCE_POINTS_PER_UNIT_LEGACY",
    "_RL_WEIGHT_NOMINAL",
)
SCHRITTE = (
    "_debatte_haltefrist_veto",
    "_debatte_konsens_veto",
    "_debatte_argumente_dafuer",
    "_debatte_argumente_dagegen",
    "_debatte_entscheid",
    "_debatte_protokollieren",
)
FUNKTION_GRENZE = 150  # Epic §8.2


def _laenge(knoten: ast.AST) -> int:
    """Zeilen ab Dekorator, wie ``regeln.py``."""
    start = min([knoten.lineno, *(d.lineno for d in knoten.decorator_list)])
    return knoten.end_lineno - start + 1


def test_verdraengung_lebt_in_portfolio_verdraengung():
    modul = importlib.import_module(VERDRAENGUNG)
    mixin = modul.VerdraengungMixin
    assert mixin.__module__ == VERDRAENGUNG
    assert modul._opportunity_score_cfg.__module__ == VERDRAENGUNG
    fehlt = [n for n in METHODEN if n not in vars(mixin)]
    assert not fehlt, f"VerdraengungMixin definiert nicht selbst: {fehlt}"
    assert "__init__" not in vars(mixin)
    fehlt = [n for n in KONSTANTEN if n not in vars(modul)]
    assert not fehlt, f"{VERDRAENGUNG} definiert nicht: {fehlt}"


def test_der_kern_erbt_die_verdraengung():
    mixin = importlib.import_module(VERDRAENGUNG).VerdraengungMixin
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
    for k in knoten:
        if isinstance(k, ast.Assign):
            definiert |= {z.id for z in k.targets if isinstance(z, ast.Name)}
        elif isinstance(k, ast.AnnAssign) and isinstance(k.target, ast.Name):
            definiert.add(k.target.id)
    elf = set(METHODEN) | set(KONSTANTEN) | {"_opportunity_score_cfg"}
    assert not sorted(definiert & elf), f"{KERN} definiert weiter selbst"
    # Einen Namen, den der Kern nicht mehr liest, führt der Kern nicht mehr (#4187 §3).
    importiert = {
        a.asname or a.name
        for n in ast.walk(baum)
        if isinstance(n, ast.ImportFrom)
        for a in n.names
    }
    assert not sorted(importiert & elf), f"{KERN} führt sie als Re-Export"


def test_freie_namen_sind_im_modul_gebunden():
    """Ein vergessener Import bräche erst auf dem Verdrängungspfad mit ``NameError``."""
    modul = importlib.import_module(VERDRAENGUNG)
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


def test_debatte_ist_in_schritte_zerlegt():
    """H-5i: der Dirigent und jeder Schritt höchstens 150 Zeilen (Epic §8.2)."""
    baum = ast.parse(inspect.getsource(importlib.import_module(VERDRAENGUNG)))
    (mixin,) = [
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "VerdraengungMixin"
    ]
    methoden = {k.name: k for k in mixin.body if isinstance(k, ast.FunctionDef)}
    fehlt = [n for n in SCHRITTE if n not in methoden]
    assert not fehlt, f"Schritte fehlen: {fehlt}"
    zu_lang = {
        n: _laenge(methoden[n])
        for n in ("debate_position_swap", *SCHRITTE)
        if _laenge(methoden[n]) > FUNKTION_GRENZE
    }
    assert not zu_lang, f"über {FUNKTION_GRENZE} Zeilen: {zu_lang}"
    # test_displacement_min_hold.py::test_hold_period_is_resolved_at_call_time liest den Rumpf
    # des Dirigenten; die Haltefrist wird dort aufgelöst, nicht in einem Schritt.
    aufrufe = {
        n.func.attr
        for n in ast.walk(methoden["debate_position_swap"])
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "_displacement_min_hold_days" in aufrufe


# --- Grenzwerte der Entscheidung -------------------------------------------------------------


def _pm(monkeypatch, min_hold_days=0.0):
    """Ein Manager nur mit dem Zustand, den die Debatte liest (``__new__``, kein Broker)."""
    from core.portfolio_manager import PortfolioManager

    monkeypatch.setattr(
        "core.consensus_retention.consensus_retention_veto", lambda *a, **k: False
    )
    pm = PortfolioManager.__new__(PortfolioManager)
    pm._debate_history = []
    pm._displacement_min_hold_days = lambda: min_hold_days
    pm._can_trade_symbol = lambda symbol: True
    return pm


def _paar(diff, pnl=0.0, dafuer=(), dagegen=(), days_held=3, age_known=False):
    from core.portfolio_typen import OpportunityScore, PositionScore

    weakest = PositionScore(
        symbol="WEAK",
        qty=1.0,
        avg_entry=100.0,
        current_price=100.0,
        market_value=100.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=pnl,
        total_score=50.0,
        days_held=days_held,
        age_known=age_known,
    )
    opp = OpportunityScore(
        symbol="NEW",
        current_price=10.0,
        total_score=50.0 + diff,
        arguments_for=list(dafuer),
        arguments_against=list(dagegen),
    )
    return opp, weakest


@pytest.mark.parametrize(
    "diff,pnl,dafuer,dagegen,erwartet",
    [
        # Abstand 15,0 ist kein „Strong upgrade“, 15,1 schon
        (15.0, 0.0, (), (), (True, "Upgrade: NEW vs WEAK (0 vs 0 args)")),
        (
            15.1,
            0.0,
            (),
            (),
            (True, "Strong upgrade: NEW (score 65) >> WEAK (score 50)"),
        ),
        # 10,1: Gleichstand der Argumente tauscht, Minderheit hält
        (10.1, 0.0, ("a",), ("b",), (True, "Upgrade: NEW vs WEAK (1 vs 1 args)")),
        (10.1, 0.0, ("a",), ("b", "c"), (False, "Keeping WEAK: b")),
        # 8,1: −2,1 % schneidet den Verlierer, −2,0 % nicht
        (
            8.1,
            -2.1,
            (),
            (),
            (True, "Cut loser WEAK (-2.1%) for better opportunity"),
        ),
        (
            8.1,
            -2.0,
            (),
            (),
            (
                False,
                "Keeping WEAK: Score difference only 8.1 points - not compelling",
            ),
        ),
        # reasoning bei leeren args_against
        (10.0, 0.0, (), (), (False, "Keeping WEAK: score diff 10.0")),
    ],
)
def test_debatte_grenzwerte(monkeypatch, diff, pnl, dafuer, dagegen, erwartet):
    pm = _pm(monkeypatch)
    opp, weakest = _paar(diff, pnl=pnl, dafuer=dafuer, dagegen=dagegen)
    assert pm.debate_position_swap(opp, weakest) == erwartet
    (zeile,) = pm._debate_history
    assert list(zeile) == [
        "timestamp",
        "new_opportunity",
        "opportunity_score",
        "weakest_position",
        "position_score",
        "arguments_for_swap",
        "arguments_against_swap",
        "decision",
        "reasoning",
    ]
    assert zeile["decision"] == ("SWAP" if erwartet[0] else "HOLD")
    assert zeile["reasoning"] == erwartet[1]


@pytest.mark.parametrize("pfad", ["veto", "debatte"])
def test_debatte_kuerzt_die_historie_auf_100(monkeypatch, pfad):
    pm = _pm(monkeypatch, min_hold_days=20.0 if pfad == "veto" else 0.0)
    pm._debate_history = [{"alt": i} for i in range(100)]
    opp, weakest = _paar(20.0, days_held=3, age_known=True)
    erlaubt, grund = pm.debate_position_swap(opp, weakest)
    assert erlaubt is (pfad == "debatte")
    assert len(pm._debate_history) == 100
    assert pm._debate_history[0] == {"alt": 1}
    assert pm._debate_history[-1]["reasoning"] == grund
