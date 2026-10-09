"""#4275 (H-4b) — Eigentests des Quelltext-Helfers ``_round_table_quelle``.

Plan: ``docs/4275-*/implementation_plan.md`` §2.1, §2.3, §6 Schritt 1.

1. Der Helfer importiert kein ``core`` — er liest Kern und Zielmodule als Text.
2. Der Text des Round Table umfasst den Kern **und** jedes vorhandene Zielmodul; fehlt der
   Kern, erhebt der Helfer (leerer Text machte jeden Leser stillschweigend wahr).
3. Die Erhebung der Patch-Ziele sieht alle Formen und am Code genau die 21 Namen aus
   Entscheidung §3.
4. ``ZIELMODULE`` ist die Tabelle aus Entscheidung §2.
5. Sperre gegen Rückfall: kein Quelltextleser liest ``round_table/runner.py`` als Datei.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _round_table_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

_AI_BOT = Path(__file__).resolve().parents[2]
TESTS = _AI_BOT / "tests"


def _schreibe(datei: Path, quelle: str) -> Path:
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(quelle), encoding="utf-8")
    return datei


def _paket(wurzel: Path, **module: str) -> Path:
    """Ein Paket unter ``wurzel`` mit ``core/round_table/<name>.py`` je Eintrag."""
    for name, quelle in module.items():
        _schreibe(wurzel / "core" / "round_table" / f"{name}.py", quelle)
    return wurzel


def test_der_helfer_importiert_kein_core():
    """Eigener Prozess: Im Testprozess ist ``core`` längst geladen."""
    skript = (
        "import sys\n"
        "vorher = set(sys.modules)\n"
        "from tests.unit import _round_table_quelle as rq\n"
        "rq.texte_round_table(); rq.text_round_table(); rq.gepatchte_namen()\n"
        "neu = [m for m in set(sys.modules) - vorher if m == 'core' or m.startswith('core.')]\n"
        "assert not neu, neu\n"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", skript],
        cwd=_AI_BOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert lauf.returncode == 0, lauf.stderr[-1500:]


def test_text_umfasst_kern_und_vorhandene_zielmodule(tmp_path):
    paket = _paket(
        tmp_path,
        runner="KERN_MARKE = 1\n",
        signal_bau="regime_damp_factor=0.5\n",
        fremd="NICHT_IM_TEXT = 1\n",
    )
    texte = rq.texte_round_table(paket)
    assert [p.name for p in texte] == ["runner.py", "signal_bau.py"]
    text = rq.text_round_table(paket)
    assert "KERN_MARKE" in text and "regime_damp_factor=" in text
    assert "NICHT_IM_TEXT" not in text


def test_der_kern_steht_vorn_dann_nur_zielmodule():
    """Heute (vor H-4c) liefert das genau einen Eintrag: den Kern."""
    kern, *ziele = rq.texte_round_table()
    assert kern == rq.KERN
    assert all(p.parent == rq.KERN.parent and p.name in rq.ZIELMODULE for p in ziele)


def test_fehlender_kern_erhebt(tmp_path):
    _paket(tmp_path, signal_bau="x = 1\n")
    with pytest.raises(LookupError):
        rq.texte_round_table(tmp_path)


def test_texte_je_modul_einzeln_parsebar(tmp_path):
    kopf = "from __future__ import annotations\n\nx = 1\n"
    paket = _paket(tmp_path, runner=kopf, gate_stufen=kopf)
    texte = rq.texte_round_table(paket)
    assert len(texte) == 2
    # ``ast.parse`` prüft die Stellung von ``from __future__`` nicht, erst der Compiler.
    for text in texte.values():
        compile(text, "<modul>", "exec")
    with pytest.raises(SyntaxError):
        compile(rq.text_round_table(paket), "<verbunden>", "exec")


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path / "test_a.py",
        """\
        import core.round_table.runner as rt_runner
        patch("core.round_table.runner._senate")
        patch.object(rt_runner, "_gatekeeper")
        rt_runner._LAST_MISSING_CONTEXT_WARN_TS = 0.0
        assert rt_runner._active_agents == []
        """,
    )
    _schreibe(
        tmp_path / "sub" / "test_b.py",
        """\
        from core.round_table import runner
        monkeypatch.setattr(runner, "SIGNAL_BUY_THRESHOLD", 0.65)
        """,
    )
    _schreibe(
        tmp_path / "test_c.py",
        """\
        from core.round_table import runner as _r
        _r._STRICT_ML_RL_WAIVED_WARNED = False
        """,
    )
    _schreibe(
        tmp_path / "test_ausgenommen.py",
        'patch("core.round_table.runner.boot_engine")\n',
    )
    gepatcht = rq.gepatchte_namen(tmp_path, ausser=(tmp_path / "test_ausgenommen.py",))
    assert gepatcht == {
        "_senate": {"test_a.py"},
        "_gatekeeper": {"test_a.py"},
        "_LAST_MISSING_CONTEXT_WARN_TS": {"test_a.py"},
        "SIGNAL_BUY_THRESHOLD": {"test_b.py"},
        "_STRICT_ML_RL_WAIVED_WARNED": {"test_c.py"},
    }


#: Entscheidung §3: die 21 Namen aus Plan §1.1 (06.10.2026) plus ``_record_consensus_outcome``,
#: den die Charakterisierung aus H-4a (#4348) patcht (Weg (a), bleibt im Kern).
#: #4277 (H-4d): ``SIGNAL_BUY_THRESHOLD`` gestrichen — Weg (c), der Patch zielt auf ``signal_bau``.
#: #4279 (H-4f): ``_LAST_MISSING_CONTEXT_WARN_TS``, ``_STRICT_ML_LSTM_DISABLED_WARNED`` und
#: ``_STRICT_ML_RL_WAIVED_WARNED`` gestrichen — Weg (c), die Zuweisungen zielen auf ``gate_stufen``.
_PATCH_ZIELE_HEUTE = {
    "_active_agents",
    "_bump_agent_failure",
    "_bump_run",
    "_consensus_engine",
    "_gatekeeper",
    "_maybe_record_shadow_tft_vote",
    "_resolve_gatekeeper_decision",
    "_senate",
    "boot_engine",
    "get_audit_logger",
    "logger",
    "run_round_table",
    "_global_registry",
    "_ml_watchdog",
    "datetime",
    "get_global_registry",
    "record_round_table_decision",
    "_record_consensus_outcome",
    # #4281 (H-4h): die Gegenproben aus H-4a ersetzen den Phasen-Schritt am Kern.
    "_abstimmen",
}


def test_gepatchte_namen_gegen_den_code():
    assert set(rq.gepatchte_namen()) == _PATCH_ZIELE_HEUTE


def test_zielmodule_wie_in_der_entscheidung():
    from tests.unit.test_h4_schnitt_round_table_runner import _ZIELMODUL, _dokument

    in_der_entscheidung = {
        Path(ziel).name
        for ziel, *_ in _ZIELMODUL.findall(_dokument())
        if ziel != "core/round_table/runner.py"
    }
    assert in_der_entscheidung, "keine Zielmodul-Zeile in Entscheidung §2"
    assert set(rq.ZIELMODULE) == in_der_entscheidung
    assert len(rq.ZIELMODULE) == len(set(rq.ZIELMODULE))


# ── Sperre gegen Rückfall: kein Leser liest round_table/runner.py als Datei ───

#: Datei → warum sie ``round_table/runner.py`` als Datei lesen darf.
_DATEI_LESER_ERLAUBT = {
    "_round_table_quelle.py": "der Helfer selbst",
    "test_round_table_quelle.py": "seine Eigentests",
    "test_round_table_patch_ziele.py": "Patch-Ziel-Wächter: Kern per ast, synthetische Pakete",
    "test_g3d2_audit_log_contract.py": "liest nur Boot-Zeilen, die im Kern bleiben (#4186 §5)",
    "test_h4_schnitt_round_table_runner.py": "Messung der Schnitt-Entscheidung #4186",
    "test_architektur_fitness.py": "synthetische Dateien und Vertragszeilen",
}
_MODUL_ALIAS = re.compile(
    r"import\s+core\.round_table\.runner\s+as\s+(\w+)"
    r"|from\s+core\.round_table\s+import\s+runner\b(?:\s+as\s+(\w+))?"
)


def datei_leser(datei: Path) -> list[str]:
    """Stellen, die ``round_table/runner.py`` als Ganzes lesen statt den Text des Round Table."""
    text = datei.read_text(encoding="utf-8-sig", errors="replace")
    if "runner" not in text:
        return []
    baum = ast.parse(text)
    literale = [
        k
        for k in ast.walk(baum)
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    ]
    mit_round_table = any("round_table" in k.value for k in literale)
    befunde = [
        f"{datei.name}:{k.lineno}: {k.value!r}"
        for k in literale
        if k.value.replace("\\", "/").endswith("round_table/runner.py")
        or (k.value == "runner.py" and mit_round_table)
    ]
    # Der Modul-Alias oder der volle Pfad; ``getsource(<funktion>)`` folgt der Funktion.
    alias = {"core.round_table.runner"} | {
        m.group(1) or m.group(2) or "runner" for m in _MODUL_ALIAS.finditer(text)
    }
    for n in ast.walk(baum):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, (ast.Attribute, ast.Name))
            and (n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id)
            == "getsource"
            and n.args
            and ast.unparse(n.args[0]) in alias
        ):
            befunde.append(
                f"{datei.name}:{n.lineno}: getsource({ast.unparse(n.args[0])})"
            )
    return sorted(befunde, key=lambda b: int(b.split(":")[1]))


def test_datei_leser_erkennt_die_formen(tmp_path):
    datei = _schreibe(
        tmp_path / "test_probe.py",
        """\
        import inspect
        import os
        import core.round_table.runner as rt
        from core.round_table import runner

        A = os.path.join("..", "core", "round_table", "runner.py")
        B = PAKET / "core/round_table/runner.py"
        c = inspect.getsource(rt)
        d = inspect.getsource(runner)
        e = inspect.getsource(runner._score_to_signal)
        f = inspect.getsource(core.round_table.runner)
        """,
    )
    zeilen = [int(b.split(":")[1]) for b in datei_leser(datei)]
    assert zeilen == [6, 7, 8, 9, 11]


def test_anderer_runner_ist_kein_befund(tmp_path):
    datei = _schreibe(
        tmp_path / "test_sim.py",
        'SIM = PAKET / "core" / "sim" / "runner.py"\n',
    )
    assert datei_leser(datei) == []


def test_kein_quelltextleser_liest_runner_als_datei():
    befunde = [
        b
        for datei in sorted(TESTS.rglob("*.py"))
        if datei.name not in _DATEI_LESER_ERLAUBT
        for b in datei_leser(datei)
    ]
    assert not befunde, (
        "Den Text des Round Table über tests.unit._round_table_quelle lesen "
        "(text_round_table / texte_round_table):\n" + "\n".join(befunde)
    )
