"""#4086 (ARC-E6 G-6d) — kein Test patcht einen Namen ueber ``agents``, der dort nur re-exportiert ist.

Zieht ein Helfer nach ``core/round_table/agenten/``, bleibt sein Name in ``agents`` als
Re-Export bestehen. ``patch("core.round_table.agents._read_pit_fundamentals")`` und
``monkeypatch.setattr(ag, "_read_pit_fundamentals", …)`` werfen deshalb keinen Fehler — sie
ersetzen aber nur die Re-Export-Bindung. Der Agent ruft die echte Funktion in seinem Modul,
der Test prueft still etwas anderes.

"Nur re-exportiert" heisst: ``agents.py`` importiert den Namen aus ``core.round_table.agenten.*``
— per ``from … import`` oder ueber den neuladetreuen Re-Export
``_x = _frisch("core.round_table.agenten.<modul>")`` → ``NAME = _x.NAME`` (G-6b).

Erfasst werden:
    * ``patch("core.round_table.agents.<name>")`` (auch ueber eine Modul-Konstante), ebenso
      ``monkeypatch.setattr("core.round_table.agents.<name>", …)``; ``<name>.<attr>`` auf
      einer Klasse ist erlaubt — die Klasse ist dasselbe Objekt wie im Agenten-Modul;
    * ``monkeypatch.setattr(<alias>, "<name>")`` und ``patch.object(<alias>, "<name>")`` fuer
      einen Alias, der an ``core.round_table.agents`` gebunden ist.

Nicht erfasst (Plan §7): Patches ueber ``sys.modules[...]`` oder ``importlib.import_module``.

Plan: ``docs/4086-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
AGENTS_PY = PAKET / "core" / "round_table" / "agents.py"
TESTS = PAKET / "tests"

_AGENTS = "core.round_table.agents"
_AGENTEN = "core.round_table.agenten"


def reexportierte_namen(quelle: str) -> set[str]:
    """Namen, die ``agents.py`` aus ``core.round_table.agenten.*`` zurueckimportiert."""
    baum = ast.parse(quelle)
    namen: set[str] = set()
    agenten_module: set[str] = (
        set()
    )  # Variablen, an die _frisch(...) ein agenten-Modul bindet
    for knoten in baum.body:
        if isinstance(knoten, ast.ImportFrom) and (knoten.module or "").startswith(
            _AGENTEN
        ):
            namen.update(a.asname or a.name for a in knoten.names)
        elif isinstance(knoten, ast.Assign) and len(knoten.targets) == 1:
            ziel, wert = knoten.targets[0], knoten.value
            if not isinstance(ziel, ast.Name):
                continue
            if (
                isinstance(wert, ast.Call)
                and wert.args
                and isinstance(wert.args[0], ast.Constant)
                and str(wert.args[0].value).startswith(_AGENTEN)
            ):
                agenten_module.add(ziel.id)
            elif (
                isinstance(wert, ast.Attribute)
                and isinstance(wert.value, ast.Name)
                and wert.value.id in agenten_module
            ):
                namen.add(ziel.id)
    return namen


def _punktname(knoten: ast.AST) -> str:
    """``a.b.c`` als String, sonst ''."""
    teile = []
    while isinstance(knoten, ast.Attribute):
        teile.append(knoten.attr)
        knoten = knoten.value
    if isinstance(knoten, ast.Name):
        teile.append(knoten.id)
        return ".".join(reversed(teile))
    return ""


def _aliase_und_konstanten(baum: ast.Module) -> tuple[set[str], dict[str, str]]:
    aliase: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            for a in knoten.names:
                if a.name == _AGENTS:
                    # ohne "as" bindet der Import "core" — der Zugriff laeuft dann ueber
                    # den Punktnamen core.round_table.agents (siehe _ist_agents_ausdruck).
                    if a.asname:
                        aliase.add(a.asname)
        elif isinstance(knoten, ast.ImportFrom) and knoten.module == "core.round_table":
            aliase.update(
                a.asname or a.name for a in knoten.names if a.name == "agents"
            )
    konstanten = {
        k.targets[0].id: k.value.value
        for k in baum.body
        if isinstance(k, ast.Assign)
        and len(k.targets) == 1
        and isinstance(k.targets[0], ast.Name)
        and isinstance(k.value, ast.Constant)
        and isinstance(k.value.value, str)
    }
    return aliase, konstanten


def _ist_agents(ausdruck: ast.AST, aliase: set[str]) -> bool:
    if isinstance(ausdruck, ast.Name):
        return ausdruck.id in aliase
    return _punktname(ausdruck) == _AGENTS


def _als_text(ausdruck: ast.AST, konstanten: dict[str, str]) -> str | None:
    if isinstance(ausdruck, ast.Constant) and isinstance(ausdruck.value, str):
        return ausdruck.value
    if isinstance(ausdruck, ast.Name):
        return konstanten.get(ausdruck.id)
    return None


def pruefe_patches(verzeichnis: Path, reexportiert: set[str]) -> list[str]:
    """Meldet jeden Patch auf einen nur re-exportierten ``agents``-Namen mit Datei und Zeile."""
    meldungen = []
    for datei in sorted(verzeichnis.rglob("*.py")):
        # utf-8-sig: einzelne Testdateien tragen ein BOM (test_functional_runner.py).
        baum = ast.parse(datei.read_text(encoding="utf-8-sig"), filename=str(datei))
        aliase, konstanten = _aliase_und_konstanten(baum)
        for knoten in ast.walk(baum):
            if not isinstance(knoten, ast.Call) or not knoten.args:
                continue
            funktion = _punktname(knoten.func)
            letzter = funktion.rsplit(".", 1)[-1]
            name = None
            # patch("core.round_table.agents.X") / monkeypatch.setattr("…agents.X", wert)
            if letzter in {"patch", "setattr"}:
                ziel = _als_text(knoten.args[0], konstanten)
                if ziel and ziel.startswith(_AGENTS + "."):
                    rest = ziel[len(_AGENTS) + 1 :]
                    if "." not in rest:  # X.attr auf einer Klasse ist erlaubt
                        name = rest
            # monkeypatch.setattr(ag, "X", …) / patch.object(ag, "X", …)
            if (
                name is None
                and (letzter == "setattr" or funktion.endswith("patch.object"))
                and len(knoten.args) >= 2
                and _ist_agents(knoten.args[0], aliase)
            ):
                name = _als_text(knoten.args[1], konstanten)
            if name in reexportiert:
                meldungen.append(
                    f"{datei.relative_to(verzeichnis).as_posix()}:{knoten.lineno}: "
                    f"Patch auf '{_AGENTS}.{name}' — der Name ist dort nur re-exportiert, "
                    f"der Patch erreicht den Agenten nicht. Patche ihn in seinem "
                    f"Modul unter {_AGENTEN}."
                )
    return meldungen


# --- Erkennung des Re-Exports ------------------------------------------------


def test_beide_reexport_formen_werden_erkannt():
    quelle = textwrap.dedent(
        """\
        from core.round_table.agenten._basis import _agent_enabled, X as Y
        from core.round_table.base_agent import VotingAgent

        def _frisch(name): ...

        _fd = _frisch("core.round_table.agenten._fundamentaldaten")
        _read_pit_fundamentals = _fd._read_pit_fundamentals
        _eigen = 1
        """
    )
    assert reexportierte_namen(quelle) == {
        "_agent_enabled",
        "Y",
        "_read_pit_fundamentals",
    }


def test_die_verschobenen_helfer_sind_in_agents_reexportiert():
    namen = reexportierte_namen(AGENTS_PY.read_text(encoding="utf-8"))
    assert {
        "_read_pit_fundamentals",
        "_valuation_multimetric_enabled",
        "NewsSentimentAgent",
    } <= namen


# --- Erkennung der Patches ---------------------------------------------------

_REEXPORT = {"_read_pit_fundamentals", "_valuation_multimetric_enabled", "Agent"}


def _schreibe(wurzel: Path, quelle: str) -> None:
    (wurzel / "test_probe.py").write_text(textwrap.dedent(quelle), encoding="utf-8")


def test_ein_toter_patch_per_pfad_meldet_datei_und_zeile(tmp_path):
    _schreibe(
        tmp_path,
        """\
        from unittest.mock import patch

        with patch("core.round_table.agents._read_pit_fundamentals"):
            pass
        """,
    )
    (meldung,) = pruefe_patches(tmp_path, _REEXPORT)
    assert "test_probe.py:3" in meldung
    assert "_read_pit_fundamentals" in meldung


def test_ein_toter_patch_ueber_eine_modulkonstante(tmp_path):
    _schreibe(
        tmp_path,
        """\
        from unittest import mock
        _PATCH = "core.round_table.agents._read_pit_fundamentals"

        def test_x():
            with mock.patch(_PATCH, return_value=None):
                pass
        """,
    )
    (meldung,) = pruefe_patches(tmp_path, _REEXPORT)
    assert "test_probe.py:5" in meldung


@pytest.mark.parametrize(
    "importzeile,alias",
    [
        ("from core.round_table import agents as ag", "ag"),
        ("import core.round_table.agents as agents_mod", "agents_mod"),
        ("from core.round_table import agents", "agents"),
    ],
)
def test_monkeypatch_setattr_und_patch_object_ueber_einen_alias(
    tmp_path, importzeile, alias
):
    _schreibe(
        tmp_path,
        f"""\
        from unittest.mock import patch
        {importzeile}

        def test_x(monkeypatch):
            monkeypatch.setattr({alias}, "_valuation_multimetric_enabled", lambda: True)
            with patch.object({alias}, "_read_pit_fundamentals"):
                pass
        """,
    )
    assert len(pruefe_patches(tmp_path, _REEXPORT)) == 2


def test_punktname_ohne_alias_und_setattr_per_pfad(tmp_path):
    _schreibe(
        tmp_path,
        """\
        import core.round_table.agents

        def test_x(monkeypatch):
            monkeypatch.setattr(core.round_table.agents, "_read_pit_fundamentals", 1)
            monkeypatch.setattr("core.round_table.agents._read_pit_fundamentals", 1)
        """,
    )
    assert len(pruefe_patches(tmp_path, _REEXPORT)) == 2


def test_erlaubt_sind_eigene_namen_klassenattribute_und_das_agenten_modul(tmp_path):
    _schreibe(
        tmp_path,
        """\
        from unittest.mock import patch
        from core.round_table import agents as ag
        from core.round_table.agenten import _fundamentaldaten as fd

        def test_x(monkeypatch):
            monkeypatch.setattr(ag, "get_global_registry", lambda: None)
            monkeypatch.setattr(fd, "_read_pit_fundamentals", lambda *a: None)
            with patch("core.round_table.agents.Agent.vote"):
                pass
            with patch("core.round_table.agents.get_global_registry"):
                pass
            with patch(
                "core.round_table.agenten._fundamentaldaten._read_pit_fundamentals"
            ):
                pass
            ag._read_pit_fundamentals("X")  # Aufruf, kein Patch
        """,
    )
    assert pruefe_patches(tmp_path, _REEXPORT) == []


# --- gegen den Code ------------------------------------------------------------


def test_keine_toten_patches_in_den_tests():
    reexportiert = reexportierte_namen(AGENTS_PY.read_text(encoding="utf-8"))
    meldungen = pruefe_patches(TESTS, reexportiert)
    assert not meldungen, "\n".join(meldungen)
