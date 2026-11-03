"""Ein Name, den ein Test ueber ``core.stock_specialist`` patcht, liest ein Quellen-Mixin ueber ``_ss``.

Seit G-8a2 (#4161) liegen die EDGAR- und Insider-Quellen in ``core/specialist/quellen_edgar.py``.
Die Tests patchen weiter ``core.stock_specialist.resolve_cik``, ``.get_config``,
``._get_data_provider`` und andere. Benutzt ein Mixin einen solchen Namen frei (per Import an
sich gebunden), greift der Patch dort nicht mehr: laut, wo ``patch()`` den Namen nicht findet,
still, wo ein AUS-Schalter ohnehin der Default ist.

Regel: Patcht ein Test ``core.stock_specialist.<name>`` (als Pfad-String oder per
``(monkeypatch.)setattr``/``patch.object`` auf das Modul), und ein Modul
``core/specialist/quellen*.py`` benutzt ``<name>`` als freien Namen, ist das ein Befund. Lies
den Namen dort zur Laufzeit als ``_ss.<name>``. Muster: ``test_routes_patch_ziele.py`` (G-4).
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
SPECIALIST = PAKET / "core" / "specialist"
TESTS = PAKET / "tests"

_PFAD = re.compile(r"[\"']core\.stock_specialist\.([A-Za-z_]\w*)[\"']")
_ALIAS = re.compile(
    r"import\s+core\.stock_specialist\s+as\s+(\w+)"
    r"|from\s+core\s+import\s+stock_specialist(?:\s+as\s+(\w+))?"
)


def gepatchte_namen(tests: Path) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn ueber das Modul ``core.stock_specialist`` patchen."""
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() == Path(__file__).resolve():
            continue  # die Beispiel-Quellen dieses Waechters sind keine Patches
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(_PFAD.findall(text))
        for m in _ALIAS.finditer(text):
            alias = m.group(1) or m.group(2) or "stock_specialist"
            namen |= set(
                re.findall(
                    rf"(?:patch\.object|setattr)\(\s*{re.escape(alias)}\s*,\s*[\"']([A-Za-z_]\w*)[\"']",
                    text,
                )
            )
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def pruefe(quellen: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    meldungen = []
    for datei in sorted(quellen.glob("quellen*.py")):
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        frei: dict[str, int] = {}
        for x in ast.walk(baum):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                frei.setdefault(x.id, x.lineno)
        for name in sorted(set(frei) & set(gepatcht)):
            meldungen.append(
                f"{datei.name}:{frei[name]}: '{name}' wird frei benutzt, Tests patchen "
                f"aber core.stock_specialist.{name} ({', '.join(sorted(gepatcht[name]))}). "
                f"Lies es zur Laufzeit als '_ss.{name}'."
            )
    return meldungen


def _schreibe(wurzel: Path, name: str, quelle: str) -> None:
    (wurzel / name).write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_freier_name_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "quellen_probe.py",
        """\
        from core.specialist.edgar_cik import resolve_cik

        class M:
            def f(self):
                return resolve_cik(self.symbol)
        """,
    )
    (meldung,) = pruefe(tmp_path, {"resolve_cik": {"test_a.py"}})
    assert "quellen_probe.py:5" in meldung and "_ss.resolve_cik" in meldung


def test_zugriff_ueber_ss_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "quellen_probe.py",
        """\
        class M:
            def f(self):
                return _ss.resolve_cik(self.symbol), _ss.get_config()

        from core import stock_specialist as _ss
        """,
    )
    assert pruefe(tmp_path, {"resolve_cik": {"t.py"}, "get_config": {"t.py"}}) == []


def test_nur_quellen_module_werden_geprueft(tmp_path):
    _schreibe(tmp_path, "cards.py", "def f():\n    return resolve_cik('X')\n")
    assert pruefe(tmp_path, {"resolve_cik": {"t.py"}}) == []


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path,
        "test_x.py",
        """\
        import core.stock_specialist as ss
        from core import stock_specialist
        patch("core.stock_specialist.resolve_cik")
        patch.object(stock_specialist, "_get_data_provider")
        monkeypatch.setattr(ss, "_now_utc", f)
        """,
    )
    assert set(gepatchte_namen(tmp_path)) == {
        "resolve_cik",
        "_get_data_provider",
        "_now_utc",
    }


def test_patch_ziele_gegen_den_code():
    gepatcht = gepatchte_namen(TESTS)
    # Die Plan-Messung (§1) muss der Waechter selbst sehen, sonst prueft er nichts.
    assert {"resolve_cik", "get_config", "_get_data_provider"} <= set(gepatcht)
    assert list(SPECIALIST.glob("quellen*.py")), "kein Quellen-Modul gefunden"
    meldungen = pruefe(SPECIALIST, gepatcht)
    assert not meldungen, "\n".join(meldungen)


@pytest.mark.parametrize(
    "reihenfolge",
    [
        ("core.stock_specialist", "core.specialist.quellen_edgar"),
        ("core.specialist.quellen_edgar", "core.stock_specialist"),
    ],
    ids=["specialist_zuerst", "mixin_zuerst"],
)
def test_import_kreis_traegt_in_beiden_reihenfolgen(reihenfolge):
    """Frischer Interpreter: der Kreis Specialist ↔ Mixin bricht in keiner Reihenfolge."""
    erstes, zweites = reihenfolge
    probe = (
        f"import importlib\n"
        f"importlib.import_module({erstes!r})\n"
        f"importlib.import_module({zweites!r})\n"
        "from core.stock_specialist import StockSpecialistAgent\n"
        "from core.specialist.quellen_edgar import EdgarQuellenMixin\n"
        "assert issubclass(StockSpecialistAgent, EdgarQuellenMixin)\n"
    )
    ergebnis = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]
