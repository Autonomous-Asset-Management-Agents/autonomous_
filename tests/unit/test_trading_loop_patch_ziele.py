"""Ein Name, den ein Test über ``core.engine.trading_loop`` patcht, liest ein Themen-Modul über ``_tl``.

#4243 (H-2b), Teil von ARC-E6 (#3738). Die Umzüge H-2c bis H-2k ziehen Schritte der
Handelsschleife in Themen-Module (Schnitt-Entscheidung #4184 §2, ``ZIELMODULE`` in
``tests/unit/_schleifen_quelle.py``). Die Tests patchen weiter ``core.engine.trading_loop.
get_config``, ``.kill_switch``, ``.build_symbol_eval_graph`` und andere. Benutzt ein Themen-
Modul einen solchen Namen frei (per Import an sich gebunden), greift der Patch dort nicht
mehr: laut, wo ``patch()`` den Namen nicht findet, still, wo ein AUS-Schalter ohnehin der
Default ist.

Regel (Entscheidung §3): Patcht ein Test ``core.engine.trading_loop.<name>`` (als Pfad-String
oder per ``(monkeypatch.)setattr``/``patch.object`` auf das Modul), und ein Modul aus
``ZIELMODULE`` benutzt ``<name>`` als freien Namen, ist das ein Befund. Lies den Namen dort
zur Laufzeit als ``_tl.<name>``. Dazu: kein Methodenname in zwei Themen-Mixins (§2), und der
Import-Kreis Kern ↔ Themen-Modul trägt in beiden Reihenfolgen (§3).

Muster: ``test_specialist_patch_ziele.py`` (G-8a2), ``test_routes_patch_ziele.py`` (G-4).
Plan: ``docs/4243-h-2b-a-wachter-vor-dem-ersten-umzug/implementation_plan.md`` §2.3.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _schleifen_quelle as sq

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ENGINE = PAKET / "core" / "engine"
TESTS = PAKET / "tests"

#: Entscheidung #4184 §3: ``trading_loop.asyncio.sleep`` *ist* ``asyncio.sleep`` — ein Patch
#: auf ``core.engine.trading_loop.asyncio.<x>`` trifft das geteilte Modulobjekt, also auch den
#: freien Namen ``asyncio`` in einem Themen-Modul (``tests/helpers/schlaf.py``).
ERLAUBT = {"asyncio"}


#: Gezählt wird das erste Segment: ``"core.engine.trading_loop.asyncio.gather"`` patcht
#: ``asyncio`` (wie ``test_order_executor_patch_ziele.py::gepatchte_namen``).
_PFAD = re.compile(r"[\"']core\.engine\.trading_loop\.([A-Za-z_]\w*)[\w.]*[\"']")
_ALIAS = re.compile(
    r"import\s+core\.engine\.trading_loop\s+as\s+(\w+)"
    r"|from\s+core\.engine\s+import\s+trading_loop\b(?:\s+as\s+(\w+))?"
)


def gepatchte_namen(tests: Path) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modul ``core.engine.trading_loop`` patchen."""
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.name.endswith("_patch_ziele.py"):
            # Die Beispiel-Quellen dieses und der anderen Patch-Ziel-Wächter sind keine
            # Patches (``test_order_executor_patch_ziele.py`` probt mit ``trading_loop``).
            continue
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(_PFAD.findall(text))
        for m in _ALIAS.finditer(text):
            alias = m.group(1) or m.group(2) or "trading_loop"
            namen |= set(
                re.findall(
                    rf"(?:patch\.object|setattr)\(\s*{re.escape(alias)}\s*,\s*[\"']([A-Za-z_]\w*)[\"']",
                    text,
                )
            )
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def _themen_module(engine: Path) -> list[Path]:
    return [engine / m for m in sq.ZIELMODULE if (engine / m).is_file()]


def pruefe(engine: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    meldungen = []
    for datei in _themen_module(engine):
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        frei: dict[str, int] = {}
        for x in ast.walk(baum):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                frei.setdefault(x.id, x.lineno)
        for name in sorted(set(frei) & set(gepatcht) - ERLAUBT):
            meldungen.append(
                f"{datei.name}:{frei[name]}: '{name}' wird frei benutzt, Tests patchen "
                f"aber core.engine.trading_loop.{name} "
                f"({', '.join(sorted(gepatcht[name]))}). Lies es zur Laufzeit als "
                f"'_tl.{name}'."
            )
    return meldungen


def _mixins(datei: Path) -> list[ast.ClassDef]:
    baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
    return [
        k for k in baum.body if isinstance(k, ast.ClassDef) and k.name.endswith("Mixin")
    ]


def doppelte_mixin_namen(engine: Path) -> list[str]:
    """Entscheidung §2: kein Methodenname in zwei Themen-Mixins — sonst entscheidet die
    Reihenfolge der Basen still, welcher Rumpf läuft."""
    herkunft: dict[str, list[str]] = {}
    for datei in _themen_module(engine):
        for klasse in _mixins(datei):
            for m in klasse.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    herkunft.setdefault(m.name, []).append(
                        f"{datei.name}::{klasse.name}"
                    )
    return [
        f"'{name}' ist in mehreren Themen-Mixins definiert: {', '.join(orte)}"
        for name, orte in sorted(herkunft.items())
        if len(orte) > 1
    ]


def _mixin_module(engine: Path) -> list[tuple[str, tuple[str, ...]]]:
    """(Modulpfad, Mixin-Klassen) je vorhandenem Themen-Modul mit Mixin."""
    return [
        (f"core.engine.{datei.stem}", tuple(k.name for k in mixins))
        for datei in _themen_module(engine)
        if (mixins := _mixins(datei))
    ]


def _schreibe(wurzel: Path, name: str, quelle: str) -> None:
    (wurzel / name).write_text(textwrap.dedent(quelle), encoding="utf-8")


# ── synthetisch ──────────────────────────────────────────────────────────────


def test_freier_name_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "marktdaten.py",
        """\
        from config import get_config

        class MarktdatenMixin:
            def f(self):
                return get_config().SIM_MODE
        """,
    )
    (meldung,) = pruefe(tmp_path, {"get_config": {"test_a.py"}})
    assert "marktdaten.py:5" in meldung and "_tl.get_config" in meldung


def test_zugriff_ueber_tl_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "marktdaten.py",
        """\
        class MarktdatenMixin:
            def f(self):
                return _tl.get_config(), _tl.kill_switch

        from core.engine import trading_loop as _tl
        """,
    )
    assert pruefe(tmp_path, {"get_config": {"t.py"}, "kill_switch": {"t.py"}}) == []


def test_asyncio_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        "ausstieg_hebel.py",
        """\
        import asyncio

        class AusstiegsHebelMixin:
            async def f(self):
                await asyncio.sleep(0)
        """,
    )
    assert pruefe(tmp_path, {"asyncio": {"t.py"}}) == []


def test_nur_zielmodule_werden_geprueft(tmp_path):
    _schreibe(tmp_path, "trading_loop.py", "def f():\n    return get_config()\n")
    _schreibe(tmp_path, "order_executor.py", "def f():\n    return get_config()\n")
    assert pruefe(tmp_path, {"get_config": {"t.py"}}) == []


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path,
        "test_x.py",
        """\
        import core.engine.trading_loop as tl
        from core.engine import trading_loop
        from core.engine import trading_loop as schleife
        patch("core.engine.trading_loop.get_config")
        patch("core.engine.trading_loop.asyncio.gather")
        patch.object(tl, "kill_switch")
        monkeypatch.setattr(trading_loop, "engine_now", f)
        setattr(schleife, "time", t)
        """,
    )
    assert set(gepatchte_namen(tmp_path)) == {
        "get_config",
        "asyncio",
        "kill_switch",
        "engine_now",
        "time",
    }


def test_name_in_zwei_themen_mixins_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "marktdaten.py",
        "class MarktdatenMixin:\n    def _kurs(self):\n        pass\n",
    )
    _schreibe(
        tmp_path,
        "zyklus_kontext.py",
        "class ZyklusKontextMixin:\n    def _kurs(self):\n        pass\n"
        "    def _eigen(self):\n        pass\n",
    )
    (meldung,) = doppelte_mixin_namen(tmp_path)
    assert "_kurs" in meldung
    assert "MarktdatenMixin" in meldung and "ZyklusKontextMixin" in meldung


# ── gegen den Code ───────────────────────────────────────────────────────────


def test_patch_ziele_gegen_den_code():
    gepatcht = gepatchte_namen(TESTS)
    # Die Plan-Messung (§1.2) muss der Wächter selbst sehen, sonst prüft er nichts.
    # #4244 (H-2c): ``config`` fehlt hier bewusst. Der einzige Patch über
    # ``…trading_loop.config.ALPACA_DATA_FEED`` patcht jetzt ``config`` direkt, weil
    # ``symbol_schluessel.py`` ``config`` frei liest; die gepunktete Form deckt
    # ``test_gepatchte_namen_erkennt_alle_formen`` ab.
    assert {"get_config", "kill_switch", "build_symbol_eval_graph"} <= set(gepatcht)
    assert any((ENGINE / m).is_file() for m in sq.ZIELMODULE), "kein Themen-Modul"
    meldungen = pruefe(ENGINE, gepatcht)
    assert not meldungen, "\n".join(meldungen)


def test_kein_name_in_zwei_themen_mixins():
    meldungen = doppelte_mixin_namen(ENGINE)
    assert not meldungen, "\n".join(meldungen)


def test_mixin_module_gegen_den_code():
    """Leerlauf-Wächter: ohne ein einziges Mixin-Modul liefe der Import-Test leer."""
    assert ("core.engine.ausstieg_hebel", ("AusstiegsHebelMixin",)) in _mixin_module(
        ENGINE
    )


@pytest.mark.parametrize("zuerst", ["kern", "thema"])
@pytest.mark.parametrize(
    "modul,mixins", _mixin_module(ENGINE), ids=lambda w: str(w).rpartition(".")[2]
)
def test_import_kreis_traegt_in_beiden_reihenfolgen(modul, mixins, zuerst):
    """Frischer Interpreter: der Kreis Kern ↔ Themen-Modul bricht in keiner Reihenfolge."""
    kern = "core.engine.trading_loop"
    erstes, zweites = (kern, modul) if zuerst == "kern" else (modul, kern)
    probe = (
        "import importlib\n"
        f"importlib.import_module({erstes!r})\n"
        f"importlib.import_module({zweites!r})\n"
        "from core.engine.trading_loop import TradingLoopMixin\n"
        f"thema = importlib.import_module({modul!r})\n"
        f"for name in {list(mixins)!r}:\n"
        "    assert issubclass(TradingLoopMixin, getattr(thema, name)), name\n"
    )
    ergebnis = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]
