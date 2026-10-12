"""#3827 (ARC-E6 G-4a) — Router lesen geteilten Zustand ueber das Modulobjekt.

Tests patchen geteilten Zustand ueber ``core.engine.api_routes.<name>``. Ein Router-Modul,
das ``from core.engine.api_routes import engine`` schreibt, haelt danach seine eigene
Bindung; der Patch greift nicht mehr, der Test wird still falsch (das Muster von G-1b).
Erlaubt ist nur der Laufzeitzugriff ``from core.engine import api_routes as ar`` → ``ar.engine``.

Plan: ``docs/3827-router-je-domaene-kapitalpfad-zuerst-einschliess/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ROUTES = PAKET / "core" / "engine" / "routes"

GETEILTER_ZUSTAND = frozenset(
    {"engine", "config", "RedisClient", "hitl_gate", "get_global_registry"}
)
_QUELLE = "core.engine.api_routes"


def pruefe_zugriffsregel(verzeichnis: Path) -> list[str]:
    """Meldet jeden Direktimport geteilten Zustands aus ``api_routes`` mit Datei und Zeile."""
    meldungen = []
    for datei in sorted(verzeichnis.rglob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.ImportFrom) or knoten.module != _QUELLE:
                continue
            for alias in knoten.names:
                if alias.name in GETEILTER_ZUSTAND or alias.name == "*":
                    meldungen.append(
                        f"{datei.relative_to(verzeichnis).as_posix()}:{knoten.lineno}: "
                        f"'{alias.name}' direkt aus {_QUELLE} importiert — "
                        f"lies es zur Laufzeit ueber 'from core.engine import "
                        f"api_routes as ar' → 'ar.{alias.name}'."
                    )
    return meldungen


def _schreibe(wurzel: Path, name: str, quelle: str) -> None:
    (wurzel / name).write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_ein_direktimport_ist_ein_befund_mit_datei_und_zeile(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from fastapi import APIRouter
        from core.engine.api_routes import engine
        """,
    )
    (meldung,) = pruefe_zugriffsregel(tmp_path)
    assert "probe.py:2" in meldung
    assert "'engine'" in meldung


def test_jeder_name_des_geteilten_zustands_ist_verboten(tmp_path):
    namen = ", ".join(sorted(GETEILTER_ZUSTAND))
    _schreibe(tmp_path, "probe.py", f"from core.engine.api_routes import {namen}\n")
    assert len(pruefe_zugriffsregel(tmp_path)) == len(GETEILTER_ZUSTAND)


def test_der_sternimport_ist_ein_befund(tmp_path):
    _schreibe(tmp_path, "probe.py", "from core.engine.api_routes import *\n")
    assert len(pruefe_zugriffsregel(tmp_path)) == 1


def test_der_zugriff_ueber_das_modulobjekt_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from core.engine import api_routes as ar
        from core.engine.api_routes import _hilfsfunktion

        def handler():
            return ar.engine, ar.config
        """,
    )
    assert pruefe_zugriffsregel(tmp_path) == []


def test_zugriffsregel_gegen_den_code():
    assert ROUTES.is_dir(), f"{ROUTES} fehlt"
    meldungen = pruefe_zugriffsregel(ROUTES)
    assert not meldungen, "\n".join(meldungen)
