"""Kein Test patcht eine Methode der ``kill_switch``-Instanz per ``monkeypatch.setattr``.

#4375, Teil der Abnahme von ARC-E6 (#3738). ``monkeypatch.undo`` stellt bei einer *Instanz* den
Wert zurück, den ``getattr`` vor dem Patch lieferte: die gebundene Methode. Sie bleibt danach in
``vars(kill_switch)`` liegen und verdeckt jeden späteren Patch auf der Klasse. Das Geld-Gate des
Strategiepfads setzt den Halt auf der Klasse (``tests/unit/_geld_gate_strategie.py``); lief
vorher ein solcher Test im selben Prozess, sah das Gate Orders trotz Halt. Gemessen mit
``test_vc3_execution_nahttest.py``, ``test_gateway_senke_intent_4126.py`` und
``test_strftime_safeguards.py``.

Regel: Halt-Methoden auf der Klasse patchen (``monkeypatch.setattr(type(kill_switch), ...)``)
oder mit ``unittest.mock.patch.object``. Letzteres löscht beim Rückbau ein Attribut, das vorher
nicht in der Instanz lag, und hinterlässt nichts.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

TESTS = Path(__file__).resolve().parents[1]
_PFAD_STRING = ".kill_switch.kill_switch."


def _ist_instanz(knoten: ast.expr) -> bool:
    """``kill_switch`` oder ``<irgendwas>.kill_switch``, aber nicht ``type(...)``."""
    if isinstance(knoten, ast.Name):
        return knoten.id == "kill_switch"
    return isinstance(knoten, ast.Attribute) and knoten.attr == "kill_switch"


def befunde_in(quelle: str) -> list[int]:
    """Zeilen mit ``<x>.setattr(<kill_switch-Instanz>, ...)`` oder dem Pfad-String dazu."""
    zeilen = []
    for knoten in ast.walk(ast.parse(quelle)):
        if not (
            isinstance(knoten, ast.Call)
            and isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "setattr"
            and isinstance(knoten.func.value, (ast.Name, ast.Attribute))
            and knoten.args
        ):
            continue
        ziel = knoten.args[0]
        if _ist_instanz(ziel) or (
            isinstance(ziel, ast.Constant)
            and isinstance(ziel.value, str)
            and _PFAD_STRING in ziel.value
        ):
            zeilen.append(knoten.lineno)
    return zeilen


def test_kein_test_patcht_die_kill_switch_instanz_per_monkeypatch():
    gefunden = []
    for datei in sorted(TESTS.rglob("*.py")):
        if datei.name == Path(__file__).name:
            continue
        for zeile in befunde_in(datei.read_text(encoding="utf-8-sig")):
            gefunden.append(f"{datei.relative_to(TESTS)}:{zeile}")
    assert gefunden == [], (
        "monkeypatch auf der kill_switch-Instanz hinterlässt nach dem Rückbau eine gebundene "
        "Methode in vars(kill_switch) und verdeckt Klassen-Patches (#4375). Auf "
        "type(kill_switch) patchen oder patch.object nehmen:\n" + "\n".join(gefunden)
    )


@pytest.mark.parametrize(
    "quelle",
    [
        'monkeypatch.setattr(ks_mod.kill_switch, "is_halted", f)',
        'm.setattr(kill_switch, "check_halt", f)',
        'monkeypatch.setattr("core.kill_switch.kill_switch.is_halted", f)',
    ],
)
def test_der_waechter_erkennt_den_instanz_patch(quelle):
    assert befunde_in(quelle) == [1]


@pytest.mark.parametrize(
    "quelle",
    [
        'monkeypatch.setattr(type(ks_mod.kill_switch), "is_halted", f)',
        'patch.object(kill_switch, "check_halt")',
        'monkeypatch.setattr(oe, "kill_switch", f)',
    ],
)
def test_der_waechter_laesst_klasse_und_patch_object_durch(quelle):
    assert befunde_in(quelle) == []


def test_der_mechanismus_monkeypatch_auf_der_instanz_hinterlaesst_einen_rest():
    """Belegt die Ursache: der Rückbau legt die gebundene Methode in die Instanz."""
    from core.kill_switch import kill_switch

    assert "is_halted" not in vars(kill_switch)
    mp = pytest.MonkeyPatch()
    mp.setattr(type(kill_switch), "is_halted", lambda self, user_id=None: True)
    mp.undo()
    assert "is_halted" not in vars(kill_switch)  # Klasse: sauber

    mp.setattr(kill_switch, "is_halted", lambda user_id=None: True)
    mp.undo()
    try:
        assert "is_halted" in vars(kill_switch)  # Instanz: Rest bleibt
    finally:
        del kill_switch.is_halted
