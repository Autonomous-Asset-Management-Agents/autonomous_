"""Ein Name, den ein Test ueber ``api_routes`` patcht, liest ein Router ueber ``ar``.

Die Zugriffsregel aus G-4a (``test_routes_zugriffsregel.py``) verbietet
``from core.engine.api_routes import engine``. Sie sieht aber nicht den Direktimport
aus der **Quelle** desselben Objekts. Beispiel: ``from core.usage_counters import
bump_usage`` in ``routes/kapitalpfad.py`` (G-4b). Der Test
``test_engine_diagnostics_usage.py::test_operator_action_counter_failure_never_breaks_endpoint``
patcht ``api_routes_mod.bump_usage`` und ruft ``/reset-kill-switch``. Seit dem Umzug
greift der Patch nicht mehr, der Sicherheitstest prueft still nichts (gemessen 04.10.2026).

Regel: Patcht ein Test ``core.engine.api_routes.<name>`` (als Pfad-String, per
``patch.object`` oder ``(monkeypatch.)setattr`` auf das Modul), und ein Router-Modul
bindet ``<name>`` per Import auf **Modulebene** und benutzt ihn, ist das ein Befund. Lies den
Namen dort zur Laufzeit als ``ar.<name>``. Funktionslokale Importe sind ausgenommen, denn
sie umgingen den Patch schon vor dem Umzug, im unveraenderten Rumpf.
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ROUTES = PAKET / "core" / "engine" / "routes"
TESTS = PAKET / "tests"

_PFAD = re.compile(r"[\"']core\.engine\.api_routes\.([A-Za-z_]\w*)[\"']")
_ALIAS = re.compile(
    r"import\s+core\.engine\.api_routes\s+as\s+(\w+)"
    r"|from\s+core\.engine\s+import\s+api_routes(?:\s+as\s+(\w+))?"
)


def gepatchte_namen(tests: Path) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn ueber das Modul ``api_routes`` patchen."""
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() == Path(__file__).resolve():
            continue  # die Beispiel-Quellen dieses Waechters sind keine Patches
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(_PFAD.findall(text))
        for m in _ALIAS.finditer(text):
            alias = m.group(1) or m.group(2) or "api_routes"
            namen |= set(
                re.findall(
                    rf"(?:patch\.object|setattr)\(\s*{re.escape(alias)}\s*,\s*[\"']([A-Za-z_]\w*)[\"']",
                    text,
                )
            )
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def pruefe(routes: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    meldungen = []
    for datei in sorted(routes.glob("*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        oben = {}
        for knoten in baum.body:
            if isinstance(knoten, (ast.Import, ast.ImportFrom)):
                for alias in knoten.names:
                    oben[(alias.asname or alias.name).split(".")[0]] = knoten.lineno
        benutzt = {
            x.id
            for x in ast.walk(baum)
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
        }
        for name in sorted(set(oben) & set(gepatcht) & benutzt):
            meldungen.append(
                f"{datei.name}:{oben[name]}: '{name}' ist auf Modulebene importiert, Tests patchen "
                f"aber core.engine.api_routes.{name} ({', '.join(sorted(gepatcht[name]))}). "
                f"Lies es zur Laufzeit als 'ar.{name}'."
            )
    return meldungen


def _schreibe(wurzel: Path, name: str, quelle: str) -> None:
    (wurzel / name).write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_direktimport_aus_der_quelle_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from core.usage_counters import bump_usage

        def handler():
            bump_usage("x")
        """,
    )
    (meldung,) = pruefe(tmp_path, {"bump_usage": {"test_a.py"}})
    assert "probe.py:1" in meldung and "ar.bump_usage" in meldung


def test_zugriff_ueber_ar_und_lokaler_import_sind_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "probe.py",
        """\
        from core.engine import api_routes as ar

        def handler():
            from core import hitl_gate
            ar.bump_usage("x")
            return hitl_gate
        """,
    )
    assert pruefe(tmp_path, {"bump_usage": {"t.py"}, "hitl_gate": {"t.py"}}) == []


def test_gepatchte_namen_erkennt_alle_drei_formen(tmp_path):
    _schreibe(
        tmp_path,
        "test_x.py",
        """\
        import core.engine.api_routes as api_routes_mod
        from core.engine import api_routes
        patch("core.engine.api_routes.engine")
        patch.object(api_routes, "_tracer")
        monkeypatch.setattr(api_routes_mod, "bump_usage", f)
        """,
    )
    assert set(gepatchte_namen(tmp_path)) == {"engine", "_tracer", "bump_usage"}


def test_patch_ziele_gegen_den_code():
    meldungen = pruefe(ROUTES, gepatchte_namen(TESTS))
    assert not meldungen, "\n".join(meldungen)
