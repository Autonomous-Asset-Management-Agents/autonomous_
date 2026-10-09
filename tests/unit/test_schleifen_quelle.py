"""#4006 — Eigentests des Quelltext-Helfers ``_schleifen_quelle``.

Plan: ``docs/4006-vorarbeit-g2-quelltext-waechter-entkoppeln/implementation_plan.md`` §2/§5.

Fünf Wächter lesen den Quelltext von ``live_trading_loop``; künftig über diesen Helfer. Damit
sie nicht stillschweigend wahr werden, muss er beweisen:

1. Der Dirigenten-Teil ist **wortgleich** mit ``inspect.getsource(live_trading_loop)``.
2. Jeder gefundene Schritt ist eine Methode von ``TradingLoopMixin``.
3. Er lädt **kein** ``core`` — die Abnahme #3489 darf die Engine nicht booten.
4. Greift er ins Leere, **erhebt** er ``LookupError``.

#4243 (H-2b): Er folgt den Schritten in die Mixin-Basen von ``TradingLoopMixin`` und liefert
den Text der Handelsschleife über alle Themen-Module — sonst verlören die Wächter mit jedem
Umzug still Prüfumfang. Plan: ``docs/4243-*/implementation_plan.md`` §2.1/§2.2/§6.
"""

from __future__ import annotations

import ast
import inspect
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _schleifen_quelle as sq

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_AI_BOT = Path(__file__).resolve().parents[2]


def test_dirigent_wortgleich_mit_getsource():
    from core.engine.trading_loop import TradingLoopMixin

    dirigent = inspect.getsource(TradingLoopMixin.live_trading_loop)
    assert sq.quelle().startswith(dirigent)


def test_jeder_schritt_ist_eine_methode_von_trading_loop_mixin():
    from core.engine.trading_loop import TradingLoopMixin

    schritte = sq.schritte()
    for name in schritte:
        # #4243: über die MRO — ein umgezogener Schritt liegt in einer Mixin-Basis.
        fn = getattr(TradingLoopMixin, name, None)
        assert fn is not None, f"{name} ist keine Methode von TradingLoopMixin"
    erwartet = inspect.getsource(TradingLoopMixin.live_trading_loop) + "".join(
        inspect.getsource(getattr(TradingLoopMixin, name)) for name in schritte
    )
    assert sq.quelle() == erwartet


def test_ast_quelle_liefert_baum_und_text_des_dirigenten_zuerst():
    import ast

    baum, text = sq.ast_quelle()
    assert text == sq.quelle()
    assert isinstance(baum.body[0], ast.AsyncFunctionDef)
    assert baum.body[0].name == "live_trading_loop"


def test_der_helfer_importiert_kein_core():
    """Eigener Prozess: Im Testprozess ist ``core`` längst geladen."""
    skript = (
        "import sys\n"
        "vorher = set(sys.modules)\n"
        "from tests.unit import _schleifen_quelle as sq\n"
        "sq.quelle(); sq.ast_quelle()\n"
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


_WAECHTER = (
    "tests/unit/test_3449_outbox_verdrahtung.py",
    "tests/unit/test_3453_sperre_verdrahtung.py",
    "tests/unit/test_sim_mark_to_market.py",
    "tests/unit/test_sim_multi_day.py",
    "tests/features/step_defs/test_handelsschleife_start.py",
)
_ABNAHME_3489 = _WAECHTER[-1]


@pytest.mark.parametrize("datei", _WAECHTER)
def test_alle_waechter_lesen_den_helfer(datei):
    text = (_AI_BOT / datei).read_text(encoding="utf-8")
    assert "getsource(TradingLoopMixin.live_trading_loop)" not in text
    assert "_schleifen_quelle" in text


def test_die_abnahme_3489_importiert_core_engine_weiterhin_nicht():
    text = (_AI_BOT / _ABNAHME_3489).read_text(encoding="utf-8")
    assert "core.engine" not in text.replace("``core.engine``", "")


def test_ohne_dirigent_erhebt_der_helfer(tmp_path):
    datei = tmp_path / "trading_loop.py"
    datei.write_text(
        "class TradingLoopMixin:\n    async def andere(self):\n        pass\n",
        encoding="utf-8",
    )
    with pytest.raises(LookupError):
        sq.quelle(pfad=datei)


def test_ohne_klasse_erhebt_der_helfer(tmp_path):
    datei = tmp_path / "trading_loop.py"
    datei.write_text("async def live_trading_loop(self):\n    pass\n", encoding="utf-8")
    with pytest.raises(LookupError):
        sq.quelle(pfad=datei)


def test_ohne_datei_erhebt_der_helfer(tmp_path):
    with pytest.raises(LookupError):
        sq.quelle(pfad=tmp_path / "fehlt.py")


def test_schritte_in_aufrufreihenfolge(tmp_path):
    datei = tmp_path / "trading_loop.py"
    datei.write_text(
        "class TradingLoopMixin:\n"
        "    async def live_trading_loop(self):\n"
        "        await self._b()\n"
        "        self._a()\n"
        "        await self._b()\n"
        "\n"
        "    def _a(self):\n"
        "        return 1\n"
        "\n"
        "    async def _b(self):\n"
        "        return 2\n",
        encoding="utf-8",
    )
    assert sq.schritte(pfad=datei) == ["_b", "_a"]
    text = sq.quelle(pfad=datei)
    assert text.index("async def live_trading_loop") < text.index("async def _b")
    assert text.index("async def _b") < text.index("def _a")


# ── #4243 (H-2b): Schritte in Mixin-Basen, Text über die Themen-Module ─────────


def _schreibe(datei: Path, text: str) -> Path:
    datei.write_text(textwrap.dedent(text), encoding="utf-8")
    return datei


_KERN_MIT_BASIS = """\
    from core.engine.hebel import (
        HebelMixin,
        _X,
    )


    class TradingLoopMixin(HebelMixin):
        async def live_trading_loop(self):
            await self._b()
            self._a()

        def _a(self):
            return 1
    """


def test_schritt_aus_basis_mixin_wird_gefunden(tmp_path):
    """Ein umgezogener Schritt bleibt im Text der Schleife — in Aufrufreihenfolge."""
    kern = _schreibe(tmp_path / "trading_loop.py", _KERN_MIT_BASIS)
    _schreibe(
        tmp_path / "hebel.py",
        """\
        class HebelMixin:
            async def _b(self):
                return "rumpf von b"
        """,
    )
    assert sq.schritte(pfad=kern) == ["_b", "_a"]
    text = sq.quelle(pfad=kern)
    assert 'return "rumpf von b"' in text
    assert text.index("async def _b") < text.index("def _a")


def test_schritt_im_kern_gewinnt_vor_der_basis(tmp_path):
    """Wie die MRO: definiert der Kern denselben Namen, liest der Helfer den Kern."""
    kern = _schreibe(tmp_path / "trading_loop.py", _KERN_MIT_BASIS)
    _schreibe(
        tmp_path / "hebel.py",
        """\
        class HebelMixin:
            async def _b(self):
                return "basis"

            def _a(self):
                return "verdeckt"
        """,
    )
    text = sq.quelle(pfad=kern)
    assert "return 1" in text and "verdeckt" not in text


def test_schritt_ohne_definition_erhebt(tmp_path):
    """Ein Aufruf ``self._x()`` ohne Definition fällt nicht still heraus."""
    datei = _schreibe(
        tmp_path / "trading_loop.py",
        """\
        class TradingLoopMixin:
            async def live_trading_loop(self):
                self._a()
                self._fremd()

            def _a(self):
                return 1
        """,
    )
    with pytest.raises(LookupError, match="_fremd"):
        sq.schritte(pfad=datei)
    with pytest.raises(LookupError, match="_fremd"):
        sq.quelle(pfad=datei)


def test_basis_ohne_import_erhebt(tmp_path):
    kern = _schreibe(
        tmp_path / "trading_loop.py",
        _KERN_MIT_BASIS.replace("from core.engine.hebel", "from anderswo.hebel"),
    )
    _schreibe(
        tmp_path / "hebel.py", "class HebelMixin:\n    def _b(self):\n        pass\n"
    )
    with pytest.raises(LookupError, match="HebelMixin"):
        sq.quelle(pfad=kern)


def test_basis_datei_fehlt_erhebt(tmp_path):
    kern = _schreibe(tmp_path / "trading_loop.py", _KERN_MIT_BASIS)
    with pytest.raises(LookupError, match="hebel.py"):
        sq.quelle(pfad=kern)


def test_basis_klasse_fehlt_erhebt(tmp_path):
    kern = _schreibe(tmp_path / "trading_loop.py", _KERN_MIT_BASIS)
    _schreibe(
        tmp_path / "hebel.py", "class AndererMixin:\n    def _b(self):\n        pass\n"
    )
    with pytest.raises(LookupError, match="HebelMixin"):
        sq.quelle(pfad=kern)


def test_text_handelsschleife_umfasst_kern_und_vorhandene_zielmodule(tmp_path):
    kern = _schreibe(tmp_path / "trading_loop.py", "KERN = 1\n")
    _schreibe(tmp_path / "marktdaten.py", "MARKT = 3\n")
    _schreibe(tmp_path / "ausstieg_hebel.py", "HEBEL = 2\n")
    _schreibe(tmp_path / "fremd.py", "FREMD = 4\n")
    texte = sq.texte_handelsschleife(pfad=kern)
    assert list(texte) == [
        kern,
        tmp_path / "ausstieg_hebel.py",
        tmp_path / "marktdaten.py",
    ]
    text = sq.text_handelsschleife(pfad=kern)
    assert text.index("KERN") < text.index("HEBEL") < text.index("MARKT")
    assert "FREMD" not in text


def test_text_handelsschleife_ohne_kern_erhebt(tmp_path):
    _schreibe(tmp_path / "ausstieg_hebel.py", "HEBEL = 2\n")
    with pytest.raises(LookupError):
        sq.text_handelsschleife(pfad=tmp_path / "trading_loop.py")


def test_zielmodule_umfassen_jede_basis_des_kerns():
    """Ein neues Mixin außerhalb von ``ZIELMODULE`` scheitert laut, statt ignoriert zu werden."""
    basen = sq.basen()
    assert basen, "TradingLoopMixin ohne aufgelöste Basis — der Helfer prüft nichts"
    for datei in basen:
        assert datei.name in sq.ZIELMODULE, f"{datei.name} fehlt in ZIELMODULE"


def test_text_handelsschleife_gegen_den_code():
    texte = sq.texte_handelsschleife()
    # #4244 (H-2c): zyklus.py und symbol_schluessel.py stehen in ZIELMODULE vorn.
    assert list(texte)[:4] == [
        sq.PFAD,
        sq.PFAD.parent / "zyklus.py",
        sq.PFAD.parent / "symbol_schluessel.py",
        sq.PFAD.parent / "ausstieg_hebel.py",
    ]
    assert "class AusstiegsHebelMixin" in sq.text_handelsschleife()


# ── Sperre gegen Rückfall: kein Leser liest trading_loop.py als Datei ─────────

#: Datei → warum sie ``trading_loop.py`` als Datei lesen darf.
_DATEI_LESER_ERLAUBT = {
    "_schleifen_quelle.py": "der Helfer selbst",
    "test_schleifen_quelle.py": "seine Eigentests",
    "test_h2_schnitt_trading_loop.py": "Schnitt-Entscheidung: Symbole der Datei",
    "test_3823_ausstieg_schritte.py": "Datei-Zuordnung je Schritt (H-2d schreibt sie fort)",
    "test_architektur_fitness.py": "Zeilenzahl der Datei gegen den Vertrag",
    "test_zyklus_netz_4242.py": "Methodentabelle Kern + Hebel des Zyklus-Netzes (H-2a)",
}
_KERN_PFAD = "core/engine/trading_loop.py"
_MODUL_ALIAS = re.compile(
    r"import\s+core\.engine\.trading_loop\s+as\s+(\w+)"
    r"|from\s+core\.engine\s+import\s+trading_loop\b(?:\s+as\s+(\w+))?"
)


def _zeigt_auf_kern(knoten: ast.AST) -> bool:
    return any(
        isinstance(k, ast.Constant)
        and isinstance(k.value, str)
        and k.value.endswith("trading_loop.py")
        for k in ast.walk(knoten)
    )


def datei_leser(datei: Path) -> list[str]:
    """Stellen, die ``trading_loop.py`` als Ganzes lesen statt den Text der Handelsschleife."""
    text = datei.read_text(encoding="utf-8", errors="replace")
    if "trading_loop" not in text:
        return []
    baum = ast.parse(text)
    # Nur Modulkonstanten (``TRADING_LOOP = …``): lokale Namen tragen in anderen
    # Funktionen anderes (etwa ``kern`` im Patch-Ziel-Wächter des Order-Executors).
    gebunden = {
        ziel.id
        for n in baum.body
        if isinstance(n, ast.Assign) and _zeigt_auf_kern(n.value)
        for ziel in n.targets
        if isinstance(ziel, ast.Name)
    }
    alias = {
        m.group(1) or m.group(2) or "trading_loop" for m in _MODUL_ALIAS.finditer(text)
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
                befunde.append(
                    f"{datei.name}:{n.lineno}: read_text auf trading_loop.py"
                )
        elif (
            n.func.attr == "getsource"
            and n.args
            and isinstance(n.args[0], ast.Name)
            and n.args[0].id in alias
        ):
            befunde.append(f"{datei.name}:{n.lineno}: getsource({n.args[0].id})")
    if liest:
        # Tabellen reichen den Pfad als Parameter durch — kein Empfänger nennt ihn.
        befunde += [
            f"{datei.name}:{k.lineno}: {_KERN_PFAD!r} in einer Datei mit read_text"
            for k in ast.walk(baum)
            if isinstance(k, ast.Constant) and k.value == _KERN_PFAD
        ]
    return sorted(befunde, key=lambda b: int(b.split(":")[1]))


def test_datei_leser_erkennt_die_formen(tmp_path):
    datei = _schreibe(
        tmp_path / "test_probe.py",
        """\
        import inspect
        import core.engine.trading_loop as tl
        from core.engine import trading_loop

        PFAD = WURZEL / "core" / "engine" / "trading_loop.py"
        a = PFAD.read_text()
        b = (WURZEL / "core" / "engine" / "trading_loop.py").read_text()
        c = inspect.getsource(tl)
        d = inspect.getsource(trading_loop)
        e = inspect.getsource(tl.TradingLoopMixin._x)
        TABELLE = [("core/engine/trading_loop.py", "x")]
        """,
    )
    zeilen = [int(b.split(":")[1]) for b in datei_leser(datei)]
    assert zeilen == [6, 7, 8, 9, 11]


def test_kein_quelltextleser_liest_trading_loop_als_datei():
    befunde = [
        b
        for datei in sorted((_AI_BOT / "tests").rglob("*.py"))
        if datei.name not in _DATEI_LESER_ERLAUBT
        for b in datei_leser(datei)
    ]
    assert not befunde, (
        "Den Text der Handelsschleife über tests.unit._schleifen_quelle lesen "
        "(text_handelsschleife / texte_handelsschleife / quelle):\n"
        + "\n".join(befunde)
    )
