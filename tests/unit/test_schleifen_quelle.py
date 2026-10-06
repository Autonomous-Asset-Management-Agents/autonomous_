"""#4006 — Eigentests des Quelltext-Helfers ``_schleifen_quelle``.

Plan: ``docs/4006-vorarbeit-g2-quelltext-waechter-entkoppeln/implementation_plan.md`` §2/§5.

Fünf Wächter lesen den Quelltext von ``live_trading_loop``; künftig über diesen Helfer. Damit
sie nicht stillschweigend wahr werden, muss er beweisen:

1. Der Dirigenten-Teil ist **wortgleich** mit ``inspect.getsource(live_trading_loop)``.
2. Jeder gefundene Schritt ist eine Methode von ``TradingLoopMixin``.
3. Er lädt **kein** ``core`` — die Abnahme #3489 darf die Engine nicht booten.
4. Greift er ins Leere, **erhebt** er ``LookupError``.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
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
        fn = TradingLoopMixin.__dict__.get(name)
        assert fn is not None, f"{name} ist keine Methode von TradingLoopMixin"
    erwartet = inspect.getsource(TradingLoopMixin.live_trading_loop) + "".join(
        inspect.getsource(TradingLoopMixin.__dict__[name]) for name in schritte
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


def test_schritte_in_aufrufreihenfolge_nur_aus_derselben_klasse(tmp_path):
    datei = tmp_path / "trading_loop.py"
    datei.write_text(
        "class TradingLoopMixin:\n"
        "    async def live_trading_loop(self):\n"
        "        await self._b()\n"
        "        self._fremd()\n"
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
    assert "_fremd(self)" not in text
