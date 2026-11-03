"""#4264 (H-3b) — Patch-Ziele bleiben am Modulobjekt ``core.risk_manager``.

Plan: ``docs/4264-*/implementation_plan.md`` §2.2. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2 und §3.

Ab H-3c ziehen Methoden von ``RiskManager`` in Themen-Mixins (``core/risk_<thema>.py``). Die
Tests patchen weiter ``core.risk_manager.<name>`` — ``CLOUD_LOGGING_AVAILABLE`` allein in zwölf
Dateien. Benutzt ein Themen-Modul einen solchen Namen frei, läuft der Patch dort **still ins
Leere**: Der Test wird nicht rot, sondern wirkungslos.

Regeln (Entscheidung §2/§3), je ein Wächter:

* Ein Zielmodul liest einen gepatchten Namen nur als ``_rm.<name>`` — ohne Ausnahme.
* Kein Re-Export als Patch-Ziel: Jeder gepatchte Name ist im Kern definiert, zugewiesen oder im
  Cloud-Logging-``try``-Block gesetzt, oder aus einem Modul **außerhalb** der Risikoverwaltung
  importiert. Ein Patch auf einen Namen, den der Kern aus ``core.risk_<thema>`` holt, träfe die
  Aufrufe im Themen-Modul nicht.
* Kein Methodenname in zwei Themen-Mixins, kein Mixin mit ``__init__``.
* Kein Mixin-Modul importiert den Kern oben — ``from core import risk_manager as _rm`` steht nach
  der letzten Klasse. ``risk_skalierer.py`` importiert nichts aus der Risikoverwaltung.
* Der Import-Kreis trägt in beiden Reihenfolgen (frischer Interpreter).

Muster: ``test_specialist_patch_ziele.py`` (G-8a2). Beispielquellen unten setzen den Modulpfad
zusammen, damit die Erhebung in ``test_h3_schnitt_risk_manager.py`` sie nicht als Patch zählt.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _risiko_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = rq.PAKET
CORE = PAKET / "core"
TESTS = PAKET / "tests"
_MODUL = "core." + "risk_manager"


#: Die Module der Risikoverwaltung: Kern plus Zielmodule (Entscheidung §2).
_RISIKO_MODULE = {_MODUL} | {f"core.{Path(n).stem}" for n in rq.ZIELMODULE}
_SKALIERER = "risk_skalierer.py"

# Dieselben Formen wie ``test_h3_schnitt_risk_manager.py::_patch_namen``, dazu der Alias ohne
# ``as`` — ``test_erhebung_gleich_h3_schnitt`` bindet beide Erhebungen aneinander.
_PFAD = re.compile(r"core[.]risk_manager[.]([A-Za-z_]\w*)")
_ALIAS = re.compile(
    r"import\s+core[.]risk_manager\s+as\s+(\w+)"
    r"|from\s+core\s+import\s+risk_manager\b(?:\s+as\s+(\w+))?"
)


def gepatchte_namen(tests: Path) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``core.risk_manager`` patchen."""
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() == Path(__file__).resolve():
            continue  # die Beispiel-Quellen dieses Wächters sind keine Patches
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(_PFAD.findall(text))
        for m in _ALIAS.finditer(text):
            alias = m.group(1) or m.group(2) or "risk_manager"
            namen |= set(
                re.findall(
                    rf"(?:patch[.]object|setattr)\(\s*{re.escape(alias)}\s*,\s*[\"']([A-Za-z_]\w*)[\"']",
                    text,
                )
            )
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def _modulebene(baum: ast.Module):
    """Jede Anweisung auf Modulebene, auch in ``try``/``if``/``with`` — nicht in Rümpfen."""
    offen = list(baum.body)
    while offen:
        knoten = offen.pop(0)
        yield knoten
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for feld in ("body", "orelse", "finalbody", "handlers"):
            offen.extend(getattr(knoten, feld, []))


def _zielmodule(core_dir: Path) -> list[tuple[Path, ast.Module]]:
    return [
        (datei, ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei)))
        for datei in (core_dir / n for n in rq.ZIELMODULE)
        if datei.is_file()
    ]


def pruefe(core_dir: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    """Ein Zielmodul benutzt einen gepatchten Namen frei — der Patch liefe dort ins Leere."""
    meldungen = []
    for datei, baum in _zielmodule(core_dir):
        frei: dict[str, int] = {}
        for x in ast.walk(baum):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                frei.setdefault(x.id, x.lineno)
        for name in sorted(set(frei) & set(gepatcht)):
            meldungen.append(
                f"{datei.name}:{frei[name]}: '{name}' wird frei benutzt, Tests patchen "
                f"aber {_MODUL}.{name} ({', '.join(sorted(gepatcht[name]))}). "
                f"Lies es zur Laufzeit als '_rm.{name}'."
            )
    return meldungen


def _bindungen(baum: ast.Module) -> dict[str, list[str | None]]:
    """Name → Herkunft je Bindung auf Modulebene: ``None`` für ``def``/``class``/Zuweisung,
    sonst das Modul, aus dem importiert wird."""
    gebunden: dict[str, list[str | None]] = {}
    for k in _modulebene(baum):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            gebunden.setdefault(k.name, []).append(None)
        elif isinstance(k, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            ziele = k.targets if isinstance(k, ast.Assign) else [k.target]
            for ziel in ziele:
                for n in ast.walk(ziel):
                    if isinstance(n, ast.Name):
                        gebunden.setdefault(n.id, []).append(None)
        elif isinstance(k, ast.ImportFrom):
            for alias in k.names:
                gebunden.setdefault(alias.asname or alias.name, []).append(
                    k.module or ""
                )
        elif isinstance(k, ast.Import):
            for alias in k.names:
                name = alias.asname or alias.name.partition(".")[0]
                gebunden.setdefault(name, []).append(alias.name)
    return gebunden


def reexporte(core_dir: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    """Jeder gepatchte Name ist im Kern gebunden — nicht aus einem Themen-Modul geholt."""
    kern = core_dir / rq.KERN.name
    gebunden = _bindungen(ast.parse(kern.read_text(encoding="utf-8")))
    meldungen = []
    for name in sorted(gepatcht):
        wer = ", ".join(sorted(gepatcht[name]))
        if name not in gebunden:
            meldungen.append(
                f"'{name}' ist im Kern nicht gebunden, Tests patchen aber "
                f"{_MODUL}.{name} ({wer})."
            )
            continue
        for herkunft in gebunden[name]:
            if herkunft in _RISIKO_MODULE:
                meldungen.append(
                    f"'{name}' bindet der Kern nur per Import aus {herkunft}, Tests patchen "
                    f"aber {_MODUL}.{name} ({wer}). Der Patch träfe die Aufrufe in "
                    f"{herkunft} nicht — definiere den Namen im Kern oder patche ihn dort."
                )
    return meldungen


def doppelte_namen(wurzel: Path) -> list[str]:
    """Kein Methodenname in zwei Themen-Mixins (Entscheidung §2)."""
    meldungen = []
    for name, orte in sorted(rq.themen_methoden(wurzel).items()):
        mixins = [f"{d.name}:{k}" for d, k in orte if k.endswith("Mixin")]
        if len(mixins) > 1:
            meldungen.append(
                f"'{name}' steht in mehreren Themen-Mixins: {', '.join(mixins)} — "
                "die Basisreihenfolge von RiskManager entschiede still."
            )
    return meldungen


def mixin_init(wurzel: Path) -> list[str]:
    """Kein Themen-Mixin definiert ``__init__`` (Entscheidung §2)."""
    return [
        f"{d.name}:{k} definiert __init__ — der Zustand gehört in RiskManager.__init__."
        for d, k in rq.themen_methoden(wurzel).get("__init__", [])
        if k.endswith("Mixin")
    ]


def _importiert(k: ast.AST) -> set[str]:
    """Die Module der Risikoverwaltung, die eine Import-Anweisung lädt."""
    if isinstance(k, ast.Import):
        return {a.name for a in k.names} & _RISIKO_MODULE
    if isinstance(k, ast.ImportFrom) and k.level == 0 and k.module:
        if k.module in _RISIKO_MODULE:
            return {k.module}
        return {f"{k.module}.{a.name}" for a in k.names} & _RISIKO_MODULE
    return set()


def kern_import_oben(core_dir: Path) -> list[str]:
    """Ein Mixin-Modul lädt den Kern erst nach der letzten Klasse (Entscheidung §3);
    ``risk_skalierer.py`` lädt nichts aus der Risikoverwaltung."""
    meldungen = []
    for datei, baum in _zielmodule(core_dir):
        ende = max(
            (k.end_lineno for k in baum.body if isinstance(k, ast.ClassDef)), default=0
        )
        for k in _modulebene(baum):
            module = _importiert(k)
            if not module:
                continue
            if datei.name == _SKALIERER:
                meldungen.append(
                    f"{datei.name}:{k.lineno}: importiert {', '.join(sorted(module))} — "
                    "das Skalierer-Modul bleibt frei von Rückimporten."
                )
            elif _MODUL in module and k.lineno < ende:
                meldungen.append(
                    f"{datei.name}:{k.lineno}: importiert {_MODUL} vor dem Ende der "
                    f"letzten Klasse (Zeile {ende}) — Zirkelimport. Schreibe "
                    "'from core import risk_manager as _rm  # noqa: E402' ans Dateiende."
                )
    return meldungen


def _schreibe(datei: Path, quelle: str) -> Path:
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(quelle), encoding="utf-8")
    return datei


# ── synthetisch ───────────────────────────────────────────────────────────────


def test_freier_name_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path / "risk_konto.py",
        """\
        class KontoHaltMixin:
            def update_account_equity(self):
                if CLOUD_LOGGING_AVAILABLE:
                    return 1
        """,
    )
    (meldung,) = pruefe(tmp_path, {"CLOUD_LOGGING_AVAILABLE": {"test_a.py"}})
    assert "risk_konto.py:3" in meldung
    assert "_rm.CLOUD_LOGGING_AVAILABLE" in meldung


def test_zugriff_ueber_rm_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path / "risk_konto.py",
        """\
        class KontoHaltMixin:
            def update_account_equity(self):
                if _rm.CLOUD_LOGGING_AVAILABLE:
                    _rm.cloud_log_risk_event("x")


        from core import risk_manager as _rm  # noqa: E402
        """,
    )
    gepatcht = {"CLOUD_LOGGING_AVAILABLE": {"t.py"}, "cloud_log_risk_event": {"t.py"}}
    assert pruefe(tmp_path, gepatcht) == []


def test_nur_zielmodule_werden_geprueft(tmp_path):
    _schreibe(tmp_path / "risk_manager.py", "def f():\n    return tracer\n")
    _schreibe(tmp_path / "risk_fremd.py", "def f():\n    return tracer\n")
    assert pruefe(tmp_path, {"tracer": {"t.py"}}) == []


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path / "test_x.py",
        f"""\
        import {_MODUL} as rm
        from core import risk_manager
        from core import risk_manager as kern
        patch("{_MODUL}.CLOUD_LOGGING_AVAILABLE", False)
        patch.object(rm, "tracer")
        monkeypatch.setattr(risk_manager, "effective_max_positions", f)
        patch.object(kern, "AILearnedRules")
        """,
    )
    assert set(gepatchte_namen(tmp_path)) == {
        "CLOUD_LOGGING_AVAILABLE",
        "tracer",
        "effective_max_positions",
        "AILearnedRules",
    }


_KERN = """\
    from core.ai_rules import AILearnedRules
    from core.risk_skalierer import resolve_vix, vol_targeting_scaler
    from core.tracing import get_tracer

    tracer = get_tracer(__name__)

    try:
        from core.cloud_logger import log_risk_event as cloud_log_risk_event

        CLOUD_LOGGING_AVAILABLE = True
    except ImportError:
        CLOUD_LOGGING_AVAILABLE = False

        def cloud_log_risk_event(*args, **kwargs):
            return None


    def effective_max_positions():
        return 5


    class RiskManager:
        pass
    """


def test_reexport_als_patch_ziel_ist_ein_befund(tmp_path):
    _schreibe(tmp_path / "risk_manager.py", _KERN)
    erlaubt = {
        n: {"t.py"}
        for n in (
            "AILearnedRules",
            "tracer",
            "CLOUD_LOGGING_AVAILABLE",
            "cloud_log_risk_event",
            "effective_max_positions",
            "RiskManager",
        )
    }
    assert reexporte(tmp_path, erlaubt) == []

    (meldung,) = reexporte(tmp_path, {"vol_targeting_scaler": {"t.py"}})
    assert "vol_targeting_scaler" in meldung and "core.risk_skalierer" in meldung

    (meldung,) = reexporte(tmp_path, {"gibt_es_nicht": {"t.py"}})
    assert "gibt_es_nicht" in meldung and "nicht gebunden" in meldung


def test_name_in_zwei_themen_mixins_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path / rq.KERN, "class RiskManager:\n    def a(self):\n        pass\n"
    )
    _schreibe(
        tmp_path / "core" / "risk_deckel.py",
        "class DeckelMixin:\n    def _step_cash(self):\n        pass\n",
    )
    assert doppelte_namen(tmp_path) == []
    _schreibe(
        tmp_path / "core" / "risk_bemessung.py",
        "class BemessungMixin:\n    def _step_cash(self):\n        pass\n",
    )
    (meldung,) = doppelte_namen(tmp_path)
    assert "_step_cash" in meldung
    assert "DeckelMixin" in meldung and "BemessungMixin" in meldung


def test_mixin_mit_init_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path / rq.KERN,
        "class RiskManager:\n    def __init__(self):\n        pass\n",
    )
    assert mixin_init(tmp_path) == []
    _schreibe(
        tmp_path / "core" / "risk_konto.py",
        "class KontoHaltMixin:\n    def __init__(self):\n        pass\n",
    )
    (meldung,) = mixin_init(tmp_path)
    assert "risk_konto.py" in meldung and "KontoHaltMixin" in meldung


def test_kern_import_oben_im_mixin_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path / "risk_deckel.py",
        """\
        class DeckelMixin:
            def _step_cash(self):
                return _rm.effective_max_positions()


        from core import risk_manager as _rm  # noqa: E402
        """,
    )
    _schreibe(
        tmp_path / "risk_skalierer.py", "def resolve_vix(daten):\n    return 20.0\n"
    )
    assert kern_import_oben(tmp_path) == []

    for oben in (
        "from core import risk_manager as _rm\n",
        f"import {_MODUL}\n",
        f"from {_MODUL} import effective_max_positions\n",
    ):
        _schreibe(
            tmp_path / "risk_deckel.py",
            oben + "\n\nclass DeckelMixin:\n    def _step_cash(self):\n        pass\n",
        )
        (meldung,) = kern_import_oben(tmp_path)
        assert "risk_deckel.py:1" in meldung, oben

    _schreibe(
        tmp_path / "risk_deckel.py",
        "class DeckelMixin:\n    pass\n\n\nfrom core import risk_manager as _rm\n",
    )
    _schreibe(
        tmp_path / "risk_skalierer.py",
        "from core.risk_deckel import DeckelMixin\n\n\ndef resolve_vix(d):\n    return 1\n",
    )
    (meldung,) = kern_import_oben(tmp_path)
    assert "risk_skalierer.py:1" in meldung


# ── gegen den Code ────────────────────────────────────────────────────────────


def test_erhebung_gleich_h3_schnitt():
    """Beide Erhebungen sehen dieselben Namen — die Zahl aus Plan §1.1 steht an einer Stelle."""
    from tests.unit.test_h3_schnitt_risk_manager import _patch_namen

    assert set(gepatchte_namen(TESTS)) == set(_patch_namen())


def test_patch_ziele_gegen_den_code():
    gepatcht = gepatchte_namen(TESTS)
    # Die Plan-Messung (§1.1) muss der Wächter selbst sehen, sonst prüft er nichts.
    assert {"CLOUD_LOGGING_AVAILABLE", "effective_max_positions", "tracer"} <= set(
        gepatcht
    )
    meldungen = pruefe(CORE, gepatcht)
    assert not meldungen, "\n".join(meldungen)


def test_patch_ziele_sind_im_kern_gebunden():
    meldungen = reexporte(CORE, gepatchte_namen(TESTS))
    assert not meldungen, "\n".join(meldungen)


def test_kein_name_in_zwei_themen_mixins():
    meldungen = doppelte_namen(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_kein_mixin_definiert_init():
    meldungen = mixin_init(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_kein_mixin_modul_importiert_den_kern_oben():
    meldungen = kern_import_oben(CORE)
    assert not meldungen, "\n".join(meldungen)


def _mixins() -> list[tuple[str, str]]:
    """(Modul, Klasse) je ``*Mixin``-Klasse der vorhandenen Zielmodule."""
    return [
        (f"core.{datei.stem}", k.name)
        for datei, baum in _zielmodule(CORE)
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name.endswith("Mixin")
    ]


def test_import_kreis_traegt_in_beiden_reihenfolgen():
    """Frischer Interpreter je Reihenfolge: der Kreis Kern ↔ Mixin bricht in keiner.

    Die Schleife steht im Rumpf, nicht in ``parametrize``: Heute gibt es kein Mixin, und eine
    leere Parametrisierung wäre ein übersprungener Test. Bis H-3c prüft der Test, dass
    ``core.risk_manager`` im frischen Interpreter lädt; danach wächst er ohne Änderung.
    """
    proben = [f"import importlib\nimportlib.import_module({_MODUL!r})\n"]
    for modul, klasse in _mixins():
        pruefung = (
            f"from {_MODUL} import RiskManager\n"
            f"from {modul} import {klasse}\n"
            f"assert issubclass(RiskManager, {klasse})\n"
        )
        for erstes, zweites in ((_MODUL, modul), (modul, _MODUL)):
            proben.append(
                "import importlib\n"
                f"importlib.import_module({erstes!r})\n"
                f"importlib.import_module({zweites!r})\n" + pruefung
            )
    for probe in proben:
        ergebnis = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=PAKET,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert ergebnis.returncode == 0, probe + ergebnis.stderr[-2000:]
