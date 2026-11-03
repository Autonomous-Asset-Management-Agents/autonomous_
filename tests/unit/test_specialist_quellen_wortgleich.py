"""#4161 (ARC-E6 G-8a2) — die EDGAR- und Insider-Quellen sind wortgleich ins Mixin umgezogen.

Die Altfassung (``core/stock_specialist.py:693-1045`` vor dem Umzug) liegt als eingecheckte,
unveraendert eingerueckte Kopie in ``tests/fixtures/stock_specialist_quellen_vor_g8a2.py.txt``. Jede der zehn Methoden in
``core/specialist/quellen_edgar.py::EdgarQuellenMixin`` ist dazu AST-gleich, Docstring
eingeschlossen. Einzige angemeldete Abbildung: ``_ss.<name>`` statt ``<name>`` fuer die fuenf
Namen, die das Mixin zur Laufzeit ueber das Modul ``core.stock_specialist`` liest (Plan §2.2).

Plan: ``docs/4161-g8a2-edgar-quellen-mixin/implementation_plan.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.helpers.wortgleich import unterschiede

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ALT = PAKET / "tests" / "fixtures" / "stock_specialist_quellen_vor_g8a2.py.txt"
MIXIN = PAKET / "core" / "specialist" / "quellen_edgar.py"
SPECIALIST = PAKET / "core" / "stock_specialist.py"

#: Die zehn Methoden in der heutigen Reihenfolge (Plan §2.1).
METHODEN = (
    "_fetch_earnings_transcript",
    "_fetch_ml_prediction",
    "_fetch_vol_scenario",
    "_load_company_tokens",
    "_fetch_edgar",
    "_fetch_edgar_form4",
    "_enrich_form4_directions",
    "_fetch_edgar_8k",
    "_fetch_edgar_13d",
    "_fetch_congressional_trades",
)

#: Die Namen, die das Mixin als ``_ss.<name>`` liest (Plan §2.2).
UEBER_SS = frozenset(
    {
        "get_config",
        "_get_data_provider",
        "resolve_cik",
        "_now_utc",
        "_ETF_NO_INSIDER_FILINGS",
    }
)


class _SsZuName(ast.NodeTransformer):
    """``_ss.<name>`` → ``<name>`` fuer die angemeldeten Namen, sonst nichts."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == "_ss"
            and node.attr in UEBER_SS
        ):
            return ast.Name(id=node.attr, ctx=node.ctx)
        return self.generic_visit(node)


def _methoden(baum: ast.AST) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        f for f in baum.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _klasse(pfad: Path, name: str) -> ast.ClassDef:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    (klasse,) = [k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == name]
    return klasse


def _alt() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    # Der Ausschnitt ist unveraendert eingerueckt (Klassenebene), damit die Docstrings
    # Zeichen fuer Zeichen mitverglichen werden; ein Klassenkopf macht ihn parsebar.
    quelle = "class _Alt:\n" + ALT.read_text(encoding="utf-8")
    (klasse,) = ast.parse(quelle).body
    funktionen = _methoden(klasse)
    assert tuple(f.name for f in funktionen) == METHODEN
    return {f.name: f for f in funktionen}


def _neu() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {f.name: f for f in _methoden(_klasse(MIXIN, "EdgarQuellenMixin"))}


def test_das_mixin_traegt_genau_die_zehn_methoden_in_der_alten_reihenfolge():
    assert tuple(_neu()) == METHODEN


@pytest.mark.parametrize("name", METHODEN)
def test_methode_ist_bis_auf_ss_abbildung_ast_gleich(name):
    neu = _SsZuName().visit(ast.parse(ast.unparse(_neu()[name])).body[0])
    diff = unterschiede([neu], [_alt()[name]])
    assert not diff, f"{name} weicht von der Altfassung ab:\n{diff}"


def test_der_specialist_definiert_die_methoden_nicht_mehr_und_erbt_das_mixin():
    klasse = _klasse(SPECIALIST, "StockSpecialistAgent")
    assert {f.name for f in _methoden(klasse)}.isdisjoint(METHODEN)
    assert [ast.unparse(b) for b in klasse.bases] == ["EdgarQuellenMixin"]
