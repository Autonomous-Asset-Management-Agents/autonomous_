"""#4275 (H-4b) — Patch-Ziele auf ``core.round_table.runner`` treffen den Leser.

Plan: ``docs/4275-*/implementation_plan.md`` §2.2. Entscheidung:
``docs/3738-arc-e6-gestalt/H4_SCHNITT_round_table_runner.md`` §2 und §3.

Die Umzüge H-4c bis H-4g ziehen Themen aus ``runner.py`` in vier Zielmodule. Der Kern führt
die Namen wieder aus, damit Importe weiter tragen. Ein **Patch** trägt die Wiederausfuhr
nicht: er ersetzt nur die Bindung im Kern, nicht die im Zielmodul. Der Test liefe still ins
Leere. Vier Regeln, Muster ``test_specialist_patch_ziele.py``:

1. Jeder Name, den ein Test über ``core.round_table.runner`` patcht oder zuweist, ist im
   Kern auf oberster Ebene definiert oder wird von einer Funktion im Kern frei gelesen. Nur
   importiert (Wiederausfuhr) ist ein Befund: Weg (c), den Test auf das Zielmodul umschreiben.
2. Kein Zielmodul liest einen solchen Namen frei — auch dann nicht, wenn der Kern ihn noch
   liest (``SIGNAL_BUY_THRESHOLD``: ``_score_to_signal`` zieht mit H-4d, ``_serialize_votes``
   erst mit H-4g). Ausnahme ``logger`` (Entscheidung §2: dasselbe Logger-Objekt).
3. Rückimport-frei: kein Modul unter ``core/round_table/`` außer ``__init__.py`` importiert
   den Kern; ein frischer Interpreter lädt jedes Zielmodul zuerst.
4. Jedes Zielmodul bindet ``logger = logging.getLogger("core.round_table.runner")``.

Gelesen wird ohne Import (``ast``); nur Regel 3 lädt im eigenen Subprozess.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _round_table_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = rq.PAKET
ROUND_TABLE = PAKET / "core" / "round_table"
KERN_MODUL = "core.round_table.runner"

#: Name → warum ein Zielmodul ihn frei lesen darf, obwohl Tests ihn über den Kern patchen.
_FREI_ERLAUBT = {
    "logger": (
        "Entscheidung §2: Zielmodule binden dasselbe Logger-Objekt; der eine "
        "patch('…runner.logger') in test_round_table_stabilization.py prüft das VOTE-Log "
        "aus Phase 2, und Phase 2 bleibt im Kern"
    ),
}


def _baum(datei: Path) -> ast.Module:
    return ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))


def _oberste_ebene(baum: ast.Module) -> set[str]:
    """Auf oberster Ebene definierte Namen. Importe und Ausweich-Bindungen in ``try``/``if``
    zählen nicht (wie ``_spannen`` der H-4-Messung): ein so gebundener Name muss gelesen sein.
    """
    namen: set[str] = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            namen.add(knoten.name)
        elif isinstance(knoten, (ast.Assign, ast.AnnAssign)):
            ziele = (
                knoten.targets if isinstance(knoten, ast.Assign) else [knoten.target]
            )
            namen |= {
                n.id for z in ziele for n in ast.walk(z) if isinstance(n, ast.Name)
            }
    return namen


def _frei_gelesen(baum: ast.AST) -> dict[str, int]:
    """Freie Namen (``ast.Name``, Load) → erste Zeile. ``x.name`` ist ein Attribut, kein Name."""
    frei: dict[str, int] = {}
    for x in ast.walk(baum):
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
            frei.setdefault(x.id, x.lineno)
    return frei


def _vorhandene_ziele(round_table: Path) -> list[Path]:
    return [round_table / n for n in rq.ZIELMODULE if (round_table / n).is_file()]


def pruefe_kern(kern: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    """Regel 1: Ein Patch auf den Kern trifft nur, wenn der Kern den Namen selbst hält."""
    baum = _baum(kern)
    definiert = _oberste_ebene(baum)
    gelesen: set[str] = set()
    for fn in ast.walk(baum):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            gelesen |= set(_frei_gelesen(fn))
    meldungen = []
    for name in sorted(set(gepatcht) - definiert - gelesen):
        halter = [
            z.stem
            for z in _vorhandene_ziele(kern.parent)
            if name in _oberste_ebene(_baum(z)) or name in _frei_gelesen(_baum(z))
        ] or ["<zielmodul>"]
        ziele = " oder ".join(f"core.round_table.{h}.{name}" for h in halter)
        meldungen.append(
            f"{kern.name}: '{name}' ist im Kern weder definiert noch von einer Funktion "
            f"gelesen, Tests patchen aber {KERN_MODUL}.{name} "
            f"({', '.join(sorted(gepatcht[name]))}). Patch ins Leere — patche {ziele} "
            "(Weg (c), Entscheidung #4186 §3)."
        )
    return meldungen


def pruefe_ziele(round_table: Path, gepatcht: dict[str, set[str]]) -> list[str]:
    """Regel 2: Ein Zielmodul liest keinen Namen frei, den Tests über den Kern patchen."""
    meldungen = []
    for datei in _vorhandene_ziele(round_table):
        frei = _frei_gelesen(_baum(datei))
        for name in sorted((set(frei) & set(gepatcht)) - set(_FREI_ERLAUBT)):
            meldungen.append(
                f"{datei.name}:{frei[name]}: '{name}' wird frei gelesen, Tests patchen "
                f"aber {KERN_MODUL}.{name} ({', '.join(sorted(gepatcht[name]))}). "
                f"Patch ins Leere — patche core.round_table.{datei.stem}.{name} "
                "(Weg (c), Entscheidung #4186 §3)."
            )
    return meldungen


def _paket_von(datei: Path, round_table: Path) -> list[str]:
    """``core.round_table[.unterpaket]`` als Teile — die Basis relativer Importe."""
    return ["core", "round_table", *datei.parent.relative_to(round_table).parts]


def _absolut(knoten: ast.ImportFrom, paket: list[str]) -> str:
    if not knoten.level:
        return knoten.module or ""
    basis = paket[: len(paket) - (knoten.level - 1)]
    return ".".join([*basis, *([knoten.module] if knoten.module else [])])


def rueckimporte(round_table: Path) -> list[str]:
    """Regel 3, statisch: Kein Modul außer ``__init__.py`` und dem Kern importiert den Kern."""
    meldungen = []
    for datei in sorted(round_table.rglob("*.py")):
        if datei.parent == round_table and datei.name in {"__init__.py", "runner.py"}:
            continue
        paket = _paket_von(datei, round_table)
        for n in ast.walk(_baum(datei)):
            if isinstance(n, ast.Import):
                treffer = any(
                    a.name == KERN_MODUL or a.name.startswith(KERN_MODUL + ".")
                    for a in n.names
                )
            elif isinstance(n, ast.ImportFrom):
                modul = _absolut(n, paket)
                treffer = modul == KERN_MODUL or (
                    modul == "core.round_table"
                    and any(a.name == "runner" for a in n.names)
                )
            else:
                continue
            if treffer:
                meldungen.append(
                    f"{datei.relative_to(round_table).as_posix()}:{n.lineno}: "
                    f"importiert {KERN_MODUL} — Zielmodule sind rückimport-frei "
                    "(Entscheidung #4186 §3)."
                )
    return meldungen


def lade_zuerst(paket: Path, ziel: str) -> subprocess.CompletedProcess:
    """Regel 3, Laufzeit: frischer Interpreter, erst das Zielmodul, dann der Kern."""
    probe = (
        "import importlib\n"
        f"importlib.import_module('core.round_table.{Path(ziel).stem}')\n"
        f"importlib.import_module({KERN_MODUL!r})\n"
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=paket,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _ist_runner_logger(knoten: ast.stmt) -> bool:
    wert = getattr(knoten, "value", None)
    return (
        isinstance(wert, ast.Call)
        and ast.unparse(wert.func) == "logging.getLogger"
        and len(wert.args) == 1
        and not wert.keywords
        and isinstance(wert.args[0], ast.Constant)
        and wert.args[0].value == KERN_MODUL
    )


def pruefe_logger(round_table: Path) -> list[str]:
    """Regel 4: genau eine Bindung ``logger = logging.getLogger("core.round_table.runner")``."""
    meldungen = []
    for datei in _vorhandene_ziele(round_table):
        bindungen = [
            k
            for k in _baum(datei).body
            if isinstance(k, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(z, ast.Name) and z.id == "logger"
                for z in (k.targets if isinstance(k, ast.Assign) else [k.target])
            )
        ]
        if len(bindungen) != 1 or not _ist_runner_logger(bindungen[0]):
            meldungen.append(
                f"{datei.name}: bindet nicht genau einmal "
                f"logger = logging.getLogger({KERN_MODUL!r}) (Entscheidung #4186 §2: "
                f"dasselbe Logger-Objekt wie der Kern), gefunden: "
                f"{[ast.unparse(b) for b in bindungen]}"
            )
    return meldungen


def _modul(wurzel: Path, name: str, quelle: str) -> Path:
    datei = wurzel / name
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(quelle), encoding="utf-8")
    return datei


# ── Regel 1: Patch-Ziel im Kern ───────────────────────────────────────────────


def test_wiederausfuhr_ohne_leser_ist_ein_befund(tmp_path):
    _modul(
        tmp_path,
        "runner.py",
        """\
        from core.round_table.gate_stufen import (  # noqa: F401
            _LAST_MISSING_CONTEXT_WARN_TS,
            _warn_gatekeeper_missing_context,
        )

        def run_round_table():
            return _warn_gatekeeper_missing_context()
        """,
    )
    _modul(tmp_path, "gate_stufen.py", "_LAST_MISSING_CONTEXT_WARN_TS = 0.0\n")
    (meldung,) = pruefe_kern(
        tmp_path / "runner.py", {"_LAST_MISSING_CONTEXT_WARN_TS": {"test_p.py"}}
    )
    assert "_LAST_MISSING_CONTEXT_WARN_TS" in meldung and "test_p.py" in meldung
    assert "core.round_table.gate_stufen._LAST_MISSING_CONTEXT_WARN_TS" in meldung
    assert "Weg (c)" in meldung


def test_im_kern_definiert_ist_erlaubt(tmp_path):
    _modul(
        tmp_path,
        "runner.py",
        """\
        import logging

        logger = logging.getLogger(__name__)
        _senate = None

        def boot_engine():
            pass

        class Gast:
            pass
        """,
    )
    gepatcht = {n: {"t.py"} for n in ("logger", "_senate", "boot_engine", "Gast")}
    assert pruefe_kern(tmp_path / "runner.py", gepatcht) == []


def test_im_kern_importiert_und_gelesen_ist_erlaubt(tmp_path):
    """Weg (a) mit Rückimport: der Kern ruft den ausgezogenen Namen selbst."""
    _modul(
        tmp_path,
        "runner.py",
        """\
        from core.round_table.entscheidungs_zaehler import _bump_run

        def run_round_table():
            _bump_run("x")
        """,
    )
    assert pruefe_kern(tmp_path / "runner.py", {"_bump_run": {"t.py"}}) == []


def test_fehlender_name_ist_ein_befund(tmp_path):
    """Eine Zuweisung ``runner.X = …`` legt X still an, wenn es nicht (mehr) existiert."""
    _modul(tmp_path, "runner.py", "def run_round_table():\n    pass\n")
    (meldung,) = pruefe_kern(tmp_path / "runner.py", {"_weg": {"t.py"}})
    assert "_weg" in meldung


# ── Regel 2: kein gepatchter Name frei im Zielmodul ───────────────────────────


def test_freier_name_im_zielmodul_ist_ein_befund_auch_wenn_der_kern_ihn_liest(
    tmp_path,
):
    _modul(
        tmp_path,
        "runner.py",
        """\
        from core.round_table.consensus import SIGNAL_BUY_THRESHOLD

        def _serialize_votes():
            return SIGNAL_BUY_THRESHOLD
        """,
    )
    _modul(
        tmp_path,
        "signal_bau.py",
        """\
        from core.round_table.consensus import SIGNAL_BUY_THRESHOLD

        def _score_to_signal(score):
            return score >= SIGNAL_BUY_THRESHOLD
        """,
    )
    gepatcht = {"SIGNAL_BUY_THRESHOLD": {"test_drawdown_guard_conditioner.py"}}
    assert pruefe_kern(tmp_path / "runner.py", gepatcht) == []
    (meldung,) = pruefe_ziele(tmp_path, gepatcht)
    assert "signal_bau.py:4" in meldung and "SIGNAL_BUY_THRESHOLD" in meldung
    assert "core.round_table.signal_bau.SIGNAL_BUY_THRESHOLD" in meldung


def test_logger_im_zielmodul_ist_erlaubt(tmp_path):
    _modul(
        tmp_path,
        "gate_stufen.py",
        """\
        import logging

        logger = logging.getLogger("core.round_table.runner")

        def _warn_strict_ml_rl_not_required():
            logger.warning("x")
        """,
    )
    assert (
        pruefe_ziele(tmp_path, {"logger": {"test_round_table_stabilization.py"}}) == []
    )


def test_nur_zielmodule_werden_geprueft(tmp_path):
    _modul(tmp_path, "consensus.py", "def f():\n    return _senate\n")
    assert pruefe_ziele(tmp_path, {"_senate": {"t.py"}}) == []


# ── Regel 3: rückimport-frei ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "datei,quelle",
    [
        ("signal_bau.py", "import core.round_table.runner\n"),
        ("signal_bau.py", "import core.round_table.runner as _rt\n"),
        ("signal_bau.py", "from core.round_table.runner import _senate\n"),
        ("signal_bau.py", "from core.round_table import runner\n"),
        ("signal_bau.py", "from .runner import _senate\n"),
        ("signal_bau.py", "from . import runner\n"),
        ("signal_bau.py", "def f():\n    from core.round_table import runner as _rt\n"),
        ("agenten/probe.py", "from ..runner import _senate\n"),
    ],
    ids=[
        "import",
        "import_as",
        "from_runner",
        "from_paket",
        "relativ_from_runner",
        "relativ_from_paket",
        "spaet",
        "unterpaket_relativ",
    ],
)
def test_rueckimport_ist_ein_befund(tmp_path, datei, quelle):
    _modul(
        tmp_path, "__init__.py", "from core.round_table.runner import run_round_table\n"
    )
    _modul(tmp_path, "runner.py", "def run_round_table():\n    pass\n")
    _modul(tmp_path, datei, quelle)
    (meldung,) = rueckimporte(tmp_path)
    assert Path(datei).name in meldung


def test_kein_rueckimport_ohne_kern_bezug(tmp_path):
    _modul(
        tmp_path, "__init__.py", "from core.round_table.runner import run_round_table\n"
    )
    _modul(
        tmp_path,
        "signal_bau.py",
        "from core.round_table.consensus import SIGNAL_BUY_THRESHOLD\n"
        "from . import consensus\n"
        "from .runner_hilfe import x\n",
    )
    assert rueckimporte(tmp_path) == []


def test_subprozess_meldet_kreisimport(tmp_path):
    """Gegenprobe gegen Vakuität: der Subprozess sieht einen Kreis, und nur ihn."""
    paket = tmp_path / "kreis"
    rt = paket / "core" / "round_table"
    _modul(paket / "core", "__init__.py", "")
    _modul(rt, "__init__.py", "")
    _modul(rt, "runner.py", "from core.round_table.signal_bau import f\nG = 1\n")
    _modul(
        rt,
        "signal_bau.py",
        "from core.round_table.runner import G\n\ndef f():\n    return G\n",
    )
    assert lade_zuerst(paket, "signal_bau.py").returncode != 0

    sauber = tmp_path / "sauber"
    rt = sauber / "core" / "round_table"
    _modul(sauber / "core", "__init__.py", "")
    _modul(rt, "__init__.py", "")
    _modul(rt, "runner.py", "from core.round_table.signal_bau import f\nG = 1\n")
    _modul(rt, "signal_bau.py", "def f():\n    return 1\n")
    lauf = lade_zuerst(sauber, "signal_bau.py")
    assert lauf.returncode == 0, lauf.stderr[-1500:]


# ── Regel 4: Logger-Name ──────────────────────────────────────────────────────


def test_logger_mit_name_ist_ein_befund(tmp_path):
    _modul(
        tmp_path,
        "aufzeichnung.py",
        "import logging\n\nlogger = logging.getLogger(__name__)\n",
    )
    (meldung,) = pruefe_logger(tmp_path)
    assert "aufzeichnung.py" in meldung and "core.round_table.runner" in meldung


def test_fehlender_logger_ist_ein_befund(tmp_path):
    _modul(tmp_path, "entscheidungs_zaehler.py", "_DECISION_COUNTERS = {}\n")
    (meldung,) = pruefe_logger(tmp_path)
    assert "entscheidungs_zaehler.py" in meldung


def test_runner_logger_ist_erlaubt(tmp_path):
    _modul(
        tmp_path,
        "signal_bau.py",
        'import logging\n\nlogger = logging.getLogger("core.round_table.runner")\n',
    )
    assert pruefe_logger(tmp_path) == []


# ── Gegen den Code ────────────────────────────────────────────────────────────


def test_patch_ziele_im_kern():
    gepatcht = rq.gepatchte_namen()
    # Die Messung aus Plan §1.1 muss der Wächter selbst sehen, sonst prüft er nichts.
    # #4277 (H-4d, Weg (c)): SIGNAL_BUY_THRESHOLD wird seither an signal_bau gepatcht.
    assert {"_senate", "_active_agents"} <= set(gepatcht)
    assert "SIGNAL_BUY_THRESHOLD" not in gepatcht
    meldungen = pruefe_kern(rq.KERN, gepatcht)
    assert not meldungen, "\n".join(meldungen)


def test_kein_gepatchter_name_frei_im_zielmodul():
    meldungen = pruefe_ziele(ROUND_TABLE, rq.gepatchte_namen())
    assert not meldungen, "\n".join(meldungen)


def test_kein_rueckimport():
    meldungen = rueckimporte(ROUND_TABLE)
    assert not meldungen, "\n".join(meldungen)


def test_zielmodule_laden_zuerst():
    """Schleife statt ``parametrize``: ohne Zielmodul liefe ein leerer Parameter als
    übersprungen. Gegen Vakuität steht ``test_subprozess_meldet_kreisimport``."""
    for name in rq.ZIELMODULE:
        if (ROUND_TABLE / name).is_file():
            lauf = lade_zuerst(PAKET, name)
            assert lauf.returncode == 0, f"{name} zuerst: {lauf.stderr[-2000:]}"


def test_zielmodule_binden_den_runner_logger():
    meldungen = pruefe_logger(ROUND_TABLE)
    assert not meldungen, "\n".join(meldungen)
