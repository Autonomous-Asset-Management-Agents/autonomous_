"""#4283 (H-5b) — Wächter vor dem ersten Umzug aus dem Portfolio-Kern.

Plan: ``docs/4283-*/implementation_plan.md`` §2.1. Schnitt-Entscheidung #4187:
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` §2 und §3.

Ab H-5c ziehen Methoden von ``PortfolioManager`` in Themen-Mixins (``core/portfolio_<thema>``).
Anders als H-2 und H-3 liest hier kein Zielmodul über einen Rückimport des Kerns
(Entscheidung §3): Wer am Modulobjekt patcht, patcht dort, wo der Name gelesen wird. Liest
der Kern einen Namen nicht mehr, führt er ihn nicht mehr. Je Regel ein Wächter:

* **Patch ins Leere:** Jeder Name, den ein Test über das Modulobjekt des Kerns patcht, wird im
  Kern frei gelesen.
* **Kein Re-Export als Patch-Ziel:** Der Kern holt keinen gepatchten Namen aus einem
  Zielmodul. Der Patch träfe nur den Kern, nicht den Leser im Zielmodul.
* **Kein Zielmodul importiert den Kern**, auch nicht spät im Funktionsrumpf.
  ``portfolio_typen`` importiert nichts aus dem Schnitt.
* Kein Name in zwei Themen-Mixins, kein Mixin mit ``__init__``, jede Basis des Kerns aus
  einem Zielmodul der Liste (Entscheidung §2).
* Der Import-Kreis trägt in beiden Reihenfolgen (frischer Interpreter).

Kern, Zielmodule und Pfade sind Parameter; die Beispielquellen unten nutzen ein Probe-Modul.
Den Modulnamen des Kerns setzt die Datei erst zur Laufzeit zusammen, damit die Erhebung in
``test_h5_schnitt_portfolio_manager.py`` sie weder als Patch noch als Pfad-Leser zählt (Plan §1.3).
Muster: ``test_order_executor_patch_ziele.py`` (H-1a), ``test_risk_manager_patch_ziele.py`` (H-3b).
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
TESTS = PAKET / "tests"

KERNMODUL = ("core", "portfolio_manager")
_MODUL = ".".join(KERNMODUL)
KLASSE = "PortfolioManager"
#: Entscheidung §2, ohne den Kern. Feste Liste, kein Präfix: ``core/portfolio_shape``
#: (#3132) ist kein Zielmodul.
ZIELMODULE = (
    "core.portfolio_typen",
    "core.portfolio_bericht",
    "core.portfolio_handelsbuch",
    "core.portfolio_zulassung",
    "core.portfolio_verdraengung",
    "core.portfolio_bestand",
)
TYPEN = "core.portfolio_typen"

# Dieselben Formen wie ``test_h5_schnitt_portfolio_manager.py::_patch_namen`` —
# ``test_erhebung_gleich_h5_schnitt`` bindet beide Erhebungen aneinander.
_ALIAS_ZUWEISUNG = r"\b{a}[.](\w+)\s*=(?!=)"


def _pfad(modul: str, wurzel: Path = PAKET) -> Path:
    return wurzel.joinpath(*modul.split(".")).with_suffix(".py")


def _baum(datei: Path) -> ast.Module:
    return ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))


def _vorhanden(wurzel: Path, zielmodule) -> list[tuple[str, Path, ast.Module]]:
    return [
        (modul, datei, _baum(datei))
        for modul in zielmodule
        if (datei := _pfad(modul, wurzel)).is_file()
    ]


def gepatchte_namen(tests: Path, modul: str = _MODUL) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``modul`` patchen oder zuweisen."""
    paket, _, kurz = modul.rpartition(".")
    pfad = re.compile(rf"{re.escape(modul)}[.]([A-Za-z_]\w*)")
    alias = re.compile(
        rf"^\s*(?:import\s+{re.escape(modul)}\s+as\s+(\w+)"
        rf"|from\s+{re.escape(paket)}\s+import\s+{re.escape(kurz)}\b(?:\s+as\s+(\w+))?)",
        re.M,
    )
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() == Path(__file__).resolve():
            continue  # die Beispiel-Quellen dieses Wächters sind keine Patches
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(pfad.findall(text))
        for a in {x or y or kurz for x, y in alias.findall(text)}:
            a = re.escape(a)
            namen |= set(
                re.findall(
                    rf"(?:patch[.]object|setattr)\(\s*{a}\s*,\s*[\"'](\w+)", text
                )
            )
            namen |= set(re.findall(_ALIAS_ZUWEISUNG.format(a=a), text))
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def _absolut(k: ast.ImportFrom, modul: str) -> str:
    """Das Modul, aus dem ``k`` importiert — relative Importe gegen ``modul`` aufgelöst."""
    if not k.level:
        return k.module or ""
    basis = modul.split(".")[: -k.level]
    return ".".join(basis + ([k.module] if k.module else []))


def _geladen(k: ast.AST, modul: str) -> set[str]:
    """Alle Modulnamen, die eine Import-Anweisung in ``modul`` laden könnte."""
    if isinstance(k, ast.Import):
        return {a.name for a in k.names}
    if isinstance(k, ast.ImportFrom):
        quelle = _absolut(k, modul)
        return {quelle} | {f"{quelle}.{a.name}" for a in k.names}
    return set()


def pruefe(
    wurzel: Path,
    gepatcht: dict[str, set[str]],
    kern: str = _MODUL,
    zielmodule=ZIELMODULE,
) -> list[str]:
    """Patch ins Leere und Re-Export als Patch-Ziel (Entscheidung §3)."""
    baum = _baum(_pfad(kern, wurzel))
    gelesen = {
        x.id
        for x in ast.walk(baum)
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
    }
    leser: dict[str, list[str]] = {}
    for modul, _, ziel in _vorhanden(wurzel, zielmodule):
        for x in ast.walk(ziel):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                if modul not in leser.setdefault(x.id, []):
                    leser[x.id].append(modul)
    meldungen = []
    for name in sorted(gepatcht):
        wer = ", ".join(sorted(gepatcht[name]))
        if name not in gelesen:
            orte = ", ".join(f"{m}.{name}" for m in leser.get(name, []))
            meldungen.append(
                f"'{name}' wird gepatcht ({wer}), der Kern {kern} liest ihn aber nicht — "
                "der Patch läuft ins Leere. "
                + (
                    f"Patche dort, wo gelesen wird: {orte}."
                    if orte
                    else "Auch kein Zielmodul liest ihn."
                )
            )
    for k in ast.walk(baum):
        if not isinstance(k, ast.ImportFrom):
            continue
        quelle = _absolut(k, kern)
        if quelle not in zielmodule:
            continue
        for a in k.names:
            name = a.asname or a.name
            if name in gepatcht:
                meldungen.append(
                    f"'{name}' bindet der Kern per Re-Export aus {quelle} (Zeile "
                    f"{k.lineno}), Tests patchen aber {kern}.{name} "
                    f"({', '.join(sorted(gepatcht[name]))}). Der Patch träfe den Leser "
                    f"im Zielmodul nicht — patche {quelle}.{name}."
                )
    return meldungen


def kern_importe(
    wurzel: Path, kern: str = _MODUL, zielmodule=ZIELMODULE, typen: str = TYPEN
) -> list[str]:
    """Kein Zielmodul importiert den Kern, auch nicht spät (Entscheidung §3, Zirkelimport);
    das Typen-Modul importiert nichts aus dem Schnitt."""
    schnitt = {kern, *zielmodule}
    meldungen = []
    for modul, datei, baum in _vorhanden(wurzel, zielmodule):
        verboten = (schnitt - {modul}) if modul == typen else {kern}
        for k in ast.walk(baum):
            treffer = _geladen(k, modul) & verboten
            if treffer:
                meldungen.append(
                    f"{datei.name}:{k.lineno}: importiert {', '.join(sorted(treffer))} — "
                    + (
                        "das Typen-Modul importiert nichts aus dem Schnitt."
                        if modul == typen
                        else "kein Zielmodul importiert den Kern (Zirkelimport)."
                    )
                )
    return meldungen


def _mixins(wurzel: Path, zielmodule) -> list[tuple[str, Path, ast.ClassDef]]:
    return [
        (modul, datei, k)
        for modul, datei, baum in _vorhanden(wurzel, zielmodule)
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name.endswith("Mixin")
    ]


def _klassen_namen(klasse: ast.ClassDef) -> set[str]:
    namen = set()
    for k in klasse.body:
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            namen.add(k.name)
        elif isinstance(k, (ast.Assign, ast.AnnAssign)):
            ziele = k.targets if isinstance(k, ast.Assign) else [k.target]
            namen |= {z.id for z in ziele if isinstance(z, ast.Name)}
    return namen


def doppelte_namen(wurzel: Path, zielmodule=ZIELMODULE) -> list[str]:
    """Kein Methoden- oder Attributname in zwei Themen-Mixins (Entscheidung §2)."""
    orte: dict[str, list[str]] = {}
    for _, datei, klasse in _mixins(wurzel, zielmodule):
        for name in _klassen_namen(klasse):
            orte.setdefault(name, []).append(f"{datei.name}:{klasse.name}")
    return [
        f"'{name}' steht in mehreren Themen-Mixins: {', '.join(wo)} — die "
        f"Basisreihenfolge von {KLASSE} entschiede still."
        for name, wo in sorted(orte.items())
        if len(wo) > 1
    ]


def mixin_init(wurzel: Path, zielmodule=ZIELMODULE) -> list[str]:
    """Kein Themen-Mixin definiert ``__init__`` (Entscheidung §2)."""
    return [
        f"{datei.name}:{klasse.name} definiert __init__ — der Zustand gehört in "
        f"{KLASSE}.__init__."
        for _, datei, klasse in _mixins(wurzel, zielmodule)
        if "__init__" in _klassen_namen(klasse)
    ]


def basen_ausserhalb(
    wurzel: Path, kern: str = _MODUL, zielmodule=ZIELMODULE, klasse: str = KLASSE
) -> list[str]:
    """Jede Basis von ``klasse``, die der Kern aus ``core`` holt, stammt aus einem Zielmodul
    der Liste — sonst prüfte der Wächter ein neues Mixin nicht mit."""
    baum = _baum(_pfad(kern, wurzel))
    # gebundener Name → voller Punktname des Objekts, an das er gebunden ist
    herkunft: dict[str, str] = {}
    for k in ast.walk(baum):
        if isinstance(k, ast.Import):
            for a in k.names:
                if a.asname:
                    herkunft[a.asname] = a.name
                else:
                    herkunft[a.name.partition(".")[0]] = a.name.partition(".")[0]
        elif isinstance(k, ast.ImportFrom):
            for a in k.names:
                herkunft[a.asname or a.name] = f"{_absolut(k, kern)}.{a.name}"
    paket = kern.partition(".")[0]
    meldungen = []
    for k in baum.body:
        if not (isinstance(k, ast.ClassDef) and k.name == klasse):
            continue
        for basis in k.bases:
            kette = ast.unparse(basis).split(".")
            if kette[0] not in herkunft:
                continue  # im Kern selbst definiert oder kein Name
            voll = ".".join([herkunft[kette[0]], *kette[1:]])
            modul = voll.rpartition(".")[0]
            if modul.partition(".")[0] == paket and modul not in zielmodule:
                meldungen.append(
                    f"{klasse} erbt von {ast.unparse(basis)} aus {modul}, das nicht in "
                    "ZIELMODULE steht — der Wächter prüfte dieses Mixin nicht mit."
                )
    return meldungen


_PROBE = "core.probe_kern"
_PROBE_ZIELE = ("core.probe_typen", "core.probe_bericht", "core.probe_zulassung")


def _schreibe(wurzel: Path, modul: str, quelle: str) -> Path:
    datei = wurzel.joinpath(*modul.split(".")).with_suffix(".py")
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(quelle), encoding="utf-8")
    return datei


def _pruefe_probe(wurzel: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    return pruefe(wurzel, gepatcht, kern=_PROBE, zielmodule=_PROBE_ZIELE)


# ── Schritt 1: synthetisch ────────────────────────────────────────────────────


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path,
        "test_x",
        f"""\
        import {_PROBE} as pk
        from core import probe_kern
        from core import probe_kern as kern
        patch("{_PROBE}.load_approved_constraints")
        patch("{_PROBE}.config.get_config")
        patch.object(pk, "engine_now")
        monkeypatch.setattr(probe_kern, "_now_utc", f)
        setattr(kern, "tracer", g)
        pk.zugewiesen = 1
        assert pk.verglichen == 1
        patch("{_PROBE}_nachbar.nicht_gemeint")
        """,
    )
    assert set(gepatchte_namen(tmp_path, modul=_PROBE)) == {
        "load_approved_constraints",
        "config",
        "engine_now",
        "_now_utc",
        "tracer",
        "zugewiesen",
    }


def test_patch_ins_leere_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        _PROBE,
        """\
        from core.governance.portfolio_constraints import load_approved_constraints


        class PortfolioManager:
            pass
        """,
    )
    _schreibe(
        tmp_path,
        "core.probe_bericht",
        """\
        from core.governance.portfolio_constraints import load_approved_constraints


        class BerichtMixin:
            def get_rebalance_recommendations(self):
                return load_approved_constraints()
        """,
    )
    (meldung,) = _pruefe_probe(tmp_path, {"load_approved_constraints": {"test_a.py"}})
    assert "'load_approved_constraints'" in meldung and "test_a.py" in meldung
    assert "core.probe_bericht.load_approved_constraints" in meldung

    (meldung,) = _pruefe_probe(tmp_path, {"verschwunden": {"test_b.py"}})
    assert "'verschwunden'" in meldung and "kein Zielmodul" in meldung


def test_gelesener_name_ist_erlaubt(tmp_path):
    _schreibe(
        tmp_path,
        _PROBE,
        """\
        from core.governance.portfolio_constraints import load_approved_constraints


        class PortfolioManager:
            def get_rebalance_recommendations(self):
                return load_approved_constraints()
        """,
    )
    assert _pruefe_probe(tmp_path, {"load_approved_constraints": {"t.py"}}) == []


def test_reexport_als_patch_ziel_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        _PROBE,
        """\
        from core.probe_typen import PositionScore


        class PortfolioManager:
            def f(self) -> PositionScore:
                return PositionScore()

            def g(self):
                from .probe_typen import _now_utc

                return _now_utc()
        """,
    )
    _schreibe(tmp_path, "core.probe_typen", "class PositionScore:\n    pass\n")
    (meldung,) = _pruefe_probe(tmp_path, {"PositionScore": {"test_a.py"}})
    assert "'PositionScore'" in meldung and "core.probe_typen" in meldung
    assert "Re-Export" in meldung

    (meldung,) = _pruefe_probe(tmp_path, {"_now_utc": {"test_b.py"}})
    assert "'_now_utc'" in meldung and "core.probe_typen" in meldung


def test_import_von_ausserhalb_ist_kein_reexport(tmp_path):
    _schreibe(
        tmp_path,
        _PROBE,
        """\
        from core.governance.portfolio_constraints import load_approved_constraints
        from core.portfolio_shape import shape_portfolio


        class PortfolioManager:
            def f(self):
                return load_approved_constraints(), shape_portfolio()
        """,
    )
    gepatcht = {"load_approved_constraints": {"t.py"}, "shape_portfolio": {"t.py"}}
    assert _pruefe_probe(tmp_path, gepatcht) == []


def test_zielmodul_importiert_kern_ist_ein_befund(tmp_path):
    _schreibe(tmp_path, "core.probe_bericht", "class BerichtMixin:\n    pass\n")
    assert kern_importe(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE) == []

    for zeile, form in (
        (1, f"import {_PROBE}\n"),
        (1, f"from {_PROBE} import PortfolioManager\n"),
        (1, "from core import probe_kern\n"),
        (1, "from . import probe_kern\n"),
        (1, "from .probe_kern import PortfolioManager\n"),
    ):
        _schreibe(
            tmp_path, "core.probe_bericht", form + "\n\nclass BerichtMixin:\n    pass\n"
        )
        (meldung,) = kern_importe(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE)
        assert f"probe_bericht.py:{zeile}" in meldung and _PROBE in meldung, form

    _schreibe(
        tmp_path,
        "core.probe_bericht",
        """\
        class BerichtMixin:
            def f(self):
                from core import probe_kern

                return probe_kern


        import core.probe_kern  # noqa: E402
        """,
    )
    meldungen = kern_importe(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE)
    assert len(meldungen) == 2
    assert any("probe_bericht.py:3" in m for m in meldungen)
    assert any("probe_bericht.py:8" in m for m in meldungen)


def test_typen_importieren_nichts_aus_dem_schnitt(tmp_path):
    _schreibe(
        tmp_path,
        "core.probe_typen",
        "from dataclasses import dataclass\nfrom core.sim.clock import engine_now\n",
    )
    assert (
        kern_importe(
            tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE, typen="core.probe_typen"
        )
        == []
    )
    _schreibe(
        tmp_path,
        "core.probe_typen",
        "def f():\n    from core.probe_zulassung import ZulassungMixin\n",
    )
    (meldung,) = kern_importe(
        tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE, typen="core.probe_typen"
    )
    assert "probe_typen.py:2" in meldung and "core.probe_zulassung" in meldung


def test_name_in_zwei_themen_mixins_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "core.probe_bericht",
        "class BerichtMixin:\n    grenze = 1\n\n    def summe(self):\n        pass\n",
    )
    assert doppelte_namen(tmp_path, zielmodule=_PROBE_ZIELE) == []
    _schreibe(
        tmp_path,
        "core.probe_zulassung",
        "class ZulassungMixin:\n    def summe(self):\n        pass\n\n    grenze = 2\n",
    )
    meldungen = doppelte_namen(tmp_path, zielmodule=_PROBE_ZIELE)
    assert len(meldungen) == 2
    assert all("BerichtMixin" in m and "ZulassungMixin" in m for m in meldungen)
    assert any("'summe'" in m for m in meldungen)
    assert any("'grenze'" in m for m in meldungen)


def test_mixin_mit_init_ist_ein_befund(tmp_path):
    _schreibe(
        tmp_path,
        "core.probe_bericht",
        "class BerichtMixin:\n    def summe(self):\n        pass\n",
    )
    assert mixin_init(tmp_path, zielmodule=_PROBE_ZIELE) == []
    _schreibe(
        tmp_path,
        "core.probe_zulassung",
        "class ZulassungMixin:\n    def __init__(self):\n        pass\n",
    )
    (meldung,) = mixin_init(tmp_path, zielmodule=_PROBE_ZIELE)
    assert "probe_zulassung.py" in meldung and "ZulassungMixin" in meldung


def test_basis_ausserhalb_der_liste_ist_ein_befund(tmp_path):
    kopf = (
        "import core.probe_bericht as pb\n"
        "from typing import Generic\n"
        "from core.probe_zulassung import ZulassungMixin\n"
    )
    _schreibe(
        tmp_path,
        _PROBE,
        kopf + "\n\nclass PortfolioManager(ZulassungMixin, pb.BerichtMixin, Generic):\n"
        "    pass\n",
    )
    assert basen_ausserhalb(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE) == []
    _schreibe(
        tmp_path,
        _PROBE,
        kopf + "from core.probe_neu import NeuMixin\n\n\n"
        "class PortfolioManager(ZulassungMixin, NeuMixin):\n    pass\n",
    )
    (meldung,) = basen_ausserhalb(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE)
    assert "NeuMixin" in meldung and "core.probe_neu" in meldung


def test_nur_zielmodule_werden_geprueft(tmp_path):
    """Ein Nachbar mit demselben Präfix (wie ``portfolio_shape``) bleibt außen vor."""
    for modul in ("core.probe_shape", "core.probe_bericht_alt"):
        _schreibe(
            tmp_path,
            modul,
            f"from {_PROBE} import X\n\n\nclass ShapeMixin:\n"
            "    def __init__(self):\n        pass\n",
        )
    _schreibe(
        tmp_path,
        "core.probe_bericht",
        "class BerichtMixin:\n    def __init__(self):\n        pass\n",
    )
    assert kern_importe(tmp_path, kern=_PROBE, zielmodule=_PROBE_ZIELE) == []
    assert doppelte_namen(tmp_path, zielmodule=_PROBE_ZIELE) == []
    (meldung,) = mixin_init(tmp_path, zielmodule=_PROBE_ZIELE)
    assert "probe_bericht.py" in meldung


def test_modul_und_pfade_sind_parameter(tmp_path):
    """Andere Wurzel, anderer Kern, andere Zielmodule — derselbe Prüfer."""
    _schreibe(
        tmp_path,
        "tests.test_t",
        """\
        from pkg import kern as k
        patch("pkg.kern.leser")
        patch.object(k, "weg", f)
        """,
    )
    gepatcht = gepatchte_namen(tmp_path / "tests", modul="pkg.kern")
    assert set(gepatcht) == {"leser", "weg"}
    _schreibe(tmp_path, "pkg.kern", "def f():\n    return leser\n")
    _schreibe(tmp_path, "pkg.thema", "def g():\n    return weg\n")
    (meldung,) = pruefe(tmp_path, gepatcht, kern="pkg.kern", zielmodule=("pkg.thema",))
    assert "pkg.thema.weg" in meldung


# ── Schritt 3: gegen den Code ─────────────────────────────────────────────────


def test_erhebung_gleich_h5_schnitt():
    """Beide Erhebungen sehen dieselben Namen — die Zahl der Entscheidung steht an einer Stelle."""
    from tests.unit.test_h5_schnitt_portfolio_manager import _patch_namen

    assert set(gepatchte_namen(TESTS)) == set(_patch_namen())


def test_patch_ziele_gegen_den_code():
    gepatcht = gepatchte_namen(TESTS)
    # Die Messung der Entscheidung (§3) muss der Wächter selbst sehen, sonst prüft er nichts.
    # #4285 (H-5d): der eine Patch zog mit seinem Leser nach core.portfolio_bericht; seither
    # patcht kein Test mehr am Kern. Dieselbe Erhebung muss ihn dort finden.
    assert "load_approved_constraints" not in gepatcht
    assert "load_approved_constraints" in gepatchte_namen(
        TESTS, "core.portfolio_bericht"
    )
    meldungen = [m for m in pruefe(PAKET, gepatcht) if "Re-Export" not in m]
    assert not meldungen, "\n".join(meldungen)


def test_kein_reexport_als_patch_ziel():
    meldungen = [m for m in pruefe(PAKET, gepatchte_namen(TESTS)) if "Re-Export" in m]
    assert not meldungen, "\n".join(meldungen)


def test_kein_zielmodul_importiert_den_kern():
    meldungen = kern_importe(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_kein_name_in_zwei_themen_mixins():
    meldungen = doppelte_namen(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_kein_mixin_definiert_init():
    meldungen = mixin_init(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_jede_basis_steht_in_der_liste():
    meldungen = basen_ausserhalb(PAKET)
    assert not meldungen, "\n".join(meldungen)


def test_import_kreis_traegt_in_beiden_reihenfolgen():
    """Frischer Interpreter je Reihenfolge: der Kreis Kern ↔ Mixin bricht in keiner.

    Die Schleife steht im Rumpf, nicht in ``parametrize``: Heute gibt es kein Zielmodul, und
    eine leere Parametrisierung wäre ein übersprungener Test. Bis H-5c prüft der Test, dass
    der Kern im frischen Interpreter lädt; danach wächst er ohne Änderung.
    """
    proben = [f"import importlib\nimportlib.import_module({_MODUL!r})\n"]
    for modul, _, klasse in _mixins(PAKET, ZIELMODULE):
        pruefung = (
            f"from {_MODUL} import {KLASSE}\n"
            f"from {modul} import {klasse.name}\n"
            f"assert issubclass({KLASSE}, {klasse.name})\n"
        )
        for erstes, zweites in ((modul, _MODUL), (_MODUL, modul)):
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


def test_der_waechter_ist_kein_patch_ziel_und_kein_pfad_leser():
    """Plan §1.3: diese Datei verfälscht die Erhebung der Schnitt-Entscheidung nicht."""
    from tests.unit.test_h5_schnitt_portfolio_manager import _patch_namen, _pfad_leser

    selbst = Path(__file__).name
    assert not {n for n, dateien in _patch_namen().items() if selbst in dateien}
    assert selbst not in _pfad_leser()
