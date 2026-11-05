"""#4232 (H-1c) — PM-Zustand und Bemessungs-Helfer wohnen in eigenen Modulen.

Plan: ``docs/4232-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1c.

Fünf freie Funktionen ziehen wörtlich aus ``order_executor.py`` um: zwei nach
``pm_zustand.py``, drei nach ``bemessung_helfer.py``. Der Kern re-exportiert sie, damit
jeder Leser und jeder Patch auf ``order_executor.<name>`` dasselbe Objekt trifft
(Entscheidung §3, Weg b). Gelesen wird per ``ast`` ohne Import (Muster
``test_h1b_order_aufbau.py``); die Identität prüft ein echter Import.
"""

from __future__ import annotations

import ast
import datetime as _dt
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

ENGINE = Path(__file__).resolve().parents[2] / "core" / "engine"
KERN = ENGINE / "order_executor.py"
ZIELE = {
    "pm_zustand": (
        "restore_pm_state_from_redis",
        "persist_pm_state_to_redis",
    ),
    "bemessung_helfer": (
        "_earnings_guard_veto",
        "_topup_gap_capped_qty",
        "_regime_throttled_size",
    ),
}
NAMEN = tuple(n for namen in ZIELE.values() for n in namen)


def _definiert(pfad: Path) -> set[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    return {
        k.name
        for k in baum.body
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


@pytest.mark.parametrize("modul", sorted(ZIELE))
def test_funktionen_wohnen_im_zielmodul(modul):
    pfad = ENGINE / f"{modul}.py"
    assert pfad.exists(), f"core/engine/{modul}.py fehlt"
    assert set(ZIELE[modul]) <= _definiert(pfad)


def test_kern_definiert_sie_nicht_mehr():
    assert not set(NAMEN) & _definiert(KERN)


def test_re_export_ist_dasselbe_objekt():
    import importlib

    from core.engine import order_executor, signal_uebergabe

    for modul, namen in ZIELE.items():
        ziel = importlib.import_module(f"core.engine.{modul}")
        for name in namen:
            assert getattr(order_executor, name) is getattr(ziel, name), name
    from core.engine import bemessung_helfer

    assert (
        signal_uebergabe._topup_gap_capped_qty is bemessung_helfer._topup_gap_capped_qty
    )


@pytest.mark.parametrize("modul", sorted(ZIELE))
def test_zielmodule_importieren_den_kern_nicht(modul):
    pfad = ENGINE / f"{modul}.py"
    assert pfad.exists(), f"core/engine/{modul}.py fehlt"
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module = [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            module = [knoten.module or ""] + [a.name for a in knoten.names]
        else:
            continue
        assert not any(
            m.rpartition(".")[2] == "order_executor" for m in module
        ), f"{modul}.py:{knoten.lineno} importiert den Kern"


def test_fixed_clock_trifft_pm_zustand():
    from core.engine import pm_zustand
    from tests.helpers.stubs import FixedClock

    fest = _dt.datetime(2026, 10, 7, 14, 30, tzinfo=_dt.timezone.utc)
    with FixedClock(fest):
        assert pm_zustand.datetime.now(_dt.timezone.utc) == fest
