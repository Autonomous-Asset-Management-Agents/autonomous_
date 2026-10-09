"""#4264 (H-3b) — Eigentests des Quelltext-Helfers ``_risiko_quelle``.

Plan: ``docs/4264-*/implementation_plan.md`` §2.1/§2.3/§6 Schritt 1.

Vier Quelltextleser lesen ``core/risk_manager.py`` als Ganzes. Ab H-3c wandern Methoden in die
Themen-Module der Schnitt-Entscheidung #4185 §2; zwei der Leser würden dann laut rot, zwei
blieben still grün und prüften die falsche Datei. Der Helfer liefert deshalb den **Text der
Risikoverwaltung** (Kern plus vorhandene Zielmodule) und eine Methodentabelle über
``RiskManager`` und die ``*Mixin``-Klassen. Damit die Leser nicht stillschweigend wahr werden,
muss er beweisen:

1. Er umfasst den Kern und jedes vorhandene Zielmodul, in fester Reihenfolge.
2. Fehlt der Kern, **erhebt** er ``LookupError``.
3. Er lädt **kein** ``core``.
4. Jede Mixin-Basis des Kerns steht in ``ZIELMODULE``.
5. Kein Leser liest ``risk_manager.py`` noch als Ganzes (Sperre gegen Rückfall).
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

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_AI_BOT = Path(__file__).resolve().parents[2]


def _schreibe(datei: Path, text: str) -> Path:
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(text), encoding="utf-8")
    return datei


def _kern(wurzel: Path, text: str = "KERN = 1\n") -> Path:
    return _schreibe(wurzel / rq.KERN, text)


def test_text_umfasst_kern_und_vorhandene_zielmodule(tmp_path):
    kern = _kern(tmp_path)
    core = kern.parent
    _schreibe(core / "risk_skalierer.py", "SKALIERER = 3\n")
    _schreibe(core / "risk_deckel.py", "DECKEL = 2\n")
    _schreibe(core / "risk_fremd.py", "FREMD = 4\n")
    texte = rq.texte_risikoverwaltung(tmp_path)
    assert list(texte) == [kern, core / "risk_deckel.py", core / "risk_skalierer.py"]
    text = rq.text_risikoverwaltung(tmp_path)
    assert text.index("KERN") < text.index("DECKEL") < text.index("SKALIERER")
    assert "FREMD" not in text


def test_kern_fehlt_erhebt(tmp_path):
    _schreibe(tmp_path / "core" / "risk_deckel.py", "DECKEL = 2\n")
    with pytest.raises(LookupError):
        rq.texte_risikoverwaltung(tmp_path)
    with pytest.raises(LookupError):
        rq.text_risikoverwaltung(tmp_path)
    with pytest.raises(LookupError):
        rq.methoden_tabelle(tmp_path)


_KERN_MIT_DECKEL = """\
    from core.risk_deckel import DeckelMixin


    class RiskManager(DeckelMixin):
        def __init__(self):
            self.x = 1

        def calculate_position_size(self):
            return "kern"
    """

_DECKEL = """\
    class DeckelMixin:
        def _step_cash(self, menge, note):
            note("binding_limit", "cash")
            return menge

        def calculate_position_size(self):
            return "verdeckt"


    class Hilfe:
        def nicht_gefuehrt(self):
            pass
    """


def test_methodentabelle_fuehrt_mixin_methoden_mit_datei(tmp_path):
    kern = _kern(tmp_path, _KERN_MIT_DECKEL)
    deckel = _schreibe(kern.parent / "risk_deckel.py", _DECKEL)
    tabelle = rq.methoden_tabelle(tmp_path)
    datei, klasse, knoten = tabelle["_step_cash"]
    assert (datei, klasse, knoten.name) == (deckel, "DeckelMixin", "_step_cash")
    assert tabelle["__init__"][:2] == (kern, "RiskManager")
    assert "nicht_gefuehrt" not in tabelle


def test_kern_gewinnt_bei_doppeltem_namen(tmp_path):
    kern = _kern(tmp_path, _KERN_MIT_DECKEL)
    deckel = _schreibe(kern.parent / "risk_deckel.py", _DECKEL)
    assert rq.methoden_tabelle(tmp_path)["calculate_position_size"][:2] == (
        kern,
        "RiskManager",
    )
    assert rq.themen_methoden(tmp_path)["calculate_position_size"] == [
        (kern, "RiskManager"),
        (deckel, "DeckelMixin"),
    ]


def test_der_helfer_importiert_kein_core():
    """Eigener Prozess: Im Testprozess ist ``core`` längst geladen."""
    skript = (
        "import sys\n"
        "vorher = set(sys.modules)\n"
        "from tests.unit import _risiko_quelle as rq\n"
        "rq.text_risikoverwaltung(); rq.methoden_tabelle(); rq.themen_methoden()\n"
        "rq.basis_module()\n"
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


def test_zielmodule_umfassen_jede_basis_des_kerns(tmp_path):
    """Ein neues Mixin außerhalb von ``ZIELMODULE`` scheitert laut, statt ignoriert zu werden."""
    kern = _kern(
        tmp_path,
        """\
        from core.risk_deckel import DeckelMixin
        from core.risk_neu import NeuMixin


        class RiskManager(DeckelMixin, NeuMixin):
            pass
        """,
    )
    basen = rq.basis_module(tmp_path)
    assert basen == ["risk_deckel.py", "risk_neu.py"]
    assert [b for b in basen if b not in rq.ZIELMODULE] == ["risk_neu.py"]

    _schreibe(kern, "class RiskManager(Fremd):\n    pass\n")
    with pytest.raises(LookupError, match="Fremd"):
        rq.basis_module(tmp_path)

    # Gegen den Code: jede Basis von RiskManager steht in der Liste.
    for datei in rq.basis_module():
        assert datei in rq.ZIELMODULE, f"{datei} fehlt in ZIELMODULE"


def test_heute_ist_der_text_kern_und_alle_zielmodule():
    # #4265 (H-3c): der erste Umzug — der Text ist Kern plus risk_vorpruefung.py.
    # #4267 (H-3e): der zweite — dazu risk_deckel.py, in der Reihenfolge von ZIELMODULE.
    # #4268 (H-3f): der dritte — dazu risk_bemessung.py.
    # #4269 (H-3g): der vierte — dazu risk_konto.py.
    # #4271 (H-3i): der letzte — dazu risk_skalierer.py; reines Modul, keine Basis des Kerns.
    kern = _AI_BOT / rq.KERN
    vorpruefung = kern.parent / "risk_vorpruefung.py"
    deckel = kern.parent / "risk_deckel.py"
    bemessung = kern.parent / "risk_bemessung.py"
    konto = kern.parent / "risk_konto.py"
    skalierer = kern.parent / "risk_skalierer.py"
    assert rq.text_risikoverwaltung() == "\n".join(
        (
            kern.read_text(encoding="utf-8"),
            vorpruefung.read_text(encoding="utf-8"),
            deckel.read_text(encoding="utf-8"),
            bemessung.read_text(encoding="utf-8"),
            konto.read_text(encoding="utf-8"),
            skalierer.read_text(encoding="utf-8"),
        )
    )
    tabelle = rq.methoden_tabelle()
    datei, klasse, _ = tabelle["evaluate_new_trade"]
    assert (datei.name, klasse) == ("risk_vorpruefung.py", "VorpruefungMixin")
    datei, klasse, _ = tabelle["_step_cash"]
    assert (datei.name, klasse) == ("risk_deckel.py", "DeckelMixin")
    datei, klasse, _ = tabelle["calculate_position_size"]
    assert (datei.name, klasse) == ("risk_bemessung.py", "BemessungMixin")
    datei, klasse, _ = tabelle["update_account_equity"]
    assert (datei.name, klasse) == ("risk_konto.py", "KontoHaltMixin")
    assert sorted(rq.basis_module()) == [
        "risk_bemessung.py",
        "risk_deckel.py",
        "risk_konto.py",
        "risk_vorpruefung.py",
    ]


# ── Sperre gegen Rückfall: kein Leser liest risk_manager.py als Ganzes ────────

#: Datei → warum sie ``risk_manager.py`` als Ganzes lesen darf.
_GANZ_LESER_ERLAUBT = {
    "_risiko_quelle.py": "der Helfer selbst",
    "test_risiko_quelle.py": "seine Eigentests",
    "test_h3_schnitt_risk_manager.py": "Schnitt-Entscheidung: Symbole der Datei",
    "test_invariants_matrix.py": "prüft nur, dass die Datei existiert; der Kern bleibt",
    "test_pfad_regeln.py": "prüft den Pfad im Regeltext",
    "test_3392_review_gate.py": "prüft den Pfad in CODEOWNERS",
}
_ENDUNG = "risk_manager.py"
_MODUL_ALIAS = re.compile(
    r"import\s+core\.risk_manager\s+as\s+(\w+)"
    r"|from\s+core\s+import\s+risk_manager\b(?:\s+as\s+(\w+))?"
)


def _zeigt_auf_kern(knoten: ast.AST) -> bool:
    return any(
        isinstance(k, ast.Constant)
        and isinstance(k.value, str)
        and k.value.endswith(_ENDUNG)
        for k in ast.walk(knoten)
    )


def ganz_leser(datei: Path) -> list[str]:
    """Stellen, die ``risk_manager.py`` als Ganzes lesen statt den Text der Risikoverwaltung."""
    text = datei.read_text(encoding="utf-8-sig", errors="replace")
    if "risk_manager" not in text:
        return []
    baum = ast.parse(text)
    gebunden = {
        ziel.id
        for n in baum.body
        if isinstance(n, ast.Assign) and _zeigt_auf_kern(n.value)
        for ziel in n.targets
        if isinstance(ziel, ast.Name)
    }
    alias = {
        m.group(1) or m.group(2) or "risk_manager" for m in _MODUL_ALIAS.finditer(text)
    }
    befunde, liest = [], False
    for n in ast.walk(baum):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr == "read_text":
            liest = True
            if _zeigt_auf_kern(n.func.value) or any(
                isinstance(k, ast.Name) and k.id in gebunden
                for k in ast.walk(n.func.value)
            ):
                befunde.append(f"{datei.name}:{n.lineno}: read_text auf {_ENDUNG}")
        elif (
            n.func.attr == "getsource"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in alias
        ):
            befunde.append(f"{datei.name}:{n.lineno}: getsource({n.args[0].id})")
    if liest and "_risiko_quelle" not in text:
        # Tabellen reichen den Pfad als Parameter durch — kein Empfänger nennt ihn. Wer den
        # Kern über den Helfer liest, darf ihn in der Tabelle weiter nennen.
        befunde += [
            f"{datei.name}:{k.lineno}: {k.value!r} in einer Datei mit read_text"
            for k in ast.walk(baum)
            if isinstance(k, ast.Constant)
            and isinstance(k.value, str)
            and k.value.endswith(_ENDUNG)
        ]
    return sorted(befunde, key=lambda b: int(b.split(":")[1]))


def test_ganz_leser_erkennt_die_formen(tmp_path):
    modul = "core." + "risk_manager"
    datei = _schreibe(
        tmp_path / "test_probe.py",
        f"""\
        import inspect
        import {modul} as rm
        from core import risk_manager

        PFAD = WURZEL / "core" / "risk_manager.py"
        a = PFAD.read_text()
        b = (WURZEL / "core" / "risk_manager.py").read_text()
        c = inspect.getsource(rm)
        d = inspect.getsource(risk_manager)
        e = inspect.getsource(rm.RiskManager._step_cash)
        TABELLE = [("risk_manager.py", "x")]
        """,
    )
    zeilen = [int(b.split(":")[1]) for b in ganz_leser(datei)]
    assert zeilen == [5, 6, 7, 7, 8, 9, 11]


def test_ganz_leser_laesst_den_helfer_durch(tmp_path):
    datei = _schreibe(
        tmp_path / "test_probe.py",
        """\
        from tests.unit import _risiko_quelle

        TABELLE = [("core/risk_manager.py", "x"), ("core/anders.py", "y")]

        def lies(rel):
            if rel == "core/risk_manager.py":
                return _risiko_quelle.text_risikoverwaltung()
            return (WURZEL / rel).read_text()
        """,
    )
    assert ganz_leser(datei) == []


def test_kein_leser_liest_risk_manager_als_ganzes():
    befunde = [
        b
        for datei in sorted((_AI_BOT / "tests").rglob("*.py"))
        if datei.name not in _GANZ_LESER_ERLAUBT
        for b in ganz_leser(datei)
    ]
    assert not befunde, (
        "Den Text der Risikoverwaltung über tests.unit._risiko_quelle lesen "
        "(text_risikoverwaltung / texte_risikoverwaltung / methoden_tabelle):\n"
        + "\n".join(befunde)
    )
