"""#4233 (H-1d) — der Mandanten-Zugang wohnt im komponierten ``mandanten_zugang.py``.

Plan: ``docs/4233-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1d.

Vier Methoden von ``OrderExecutorMixin`` ziehen wörtlich nach ``MandantenZugangMixin`` um.
``BotEngine`` erhält sie über ``AusfuehrungMixin`` (``ausfuehrung.py``), das die
komponierten Mixins sammelt, damit ``base.py`` nicht wächst. Der Kern importiert keines der
beiden Module (Zirkelregel, Entscheidung §3).
"""

from __future__ import annotations

import ast
from itertools import combinations
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

KERN = Path(__file__).resolve().parents[2] / "core" / "engine" / "order_executor.py"
VIER = (
    "get_active_tenant_clients",
    "_get_tenant_risk_manager",
    "_get_tenant_portfolio_manager",
    "_broker_zugang_fuer",
)


def test_vier_methoden_wohnen_in_mandanten_zugang():
    from core.engine.mandanten_zugang import MandantenZugangMixin
    from core.engine.order_executor import OrderExecutorMixin

    for name in VIER:
        assert name in MandantenZugangMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_ausfuehrung_sammelt_die_komponierten_mixins():
    from core.engine.absendung_abgang import AbsendungAbgangMixin
    from core.engine.absendung_nachlauf import AbsendungNachlaufMixin
    from core.engine.absendung_vorlauf import AbsendungVorlaufMixin
    from core.engine.ausfuehrung import AusfuehrungMixin
    from core.engine.base import BotEngine
    from core.engine.hitl_freigabe import HitlFreigabeMixin
    from core.engine.mandanten_zugang import MandantenZugangMixin
    from core.engine.signal_desktop_absendung import SignalDesktopAbsendungMixin
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin
    from core.engine.signal_uebergabe import SignalUebergabeMixin
    from core.engine.verdraengung import VerdraengungMixin

    assert AusfuehrungMixin.__bases__ == (
        SignalUebergabeMixin,
        AbsendungNachlaufMixin,
        MandantenZugangMixin,
        HitlFreigabeMixin,
        AbsendungVorlaufMixin,
        AbsendungAbgangMixin,
        SignalDesktopEntscheidMixin,
        SignalDesktopAbsendungMixin,
        VerdraengungMixin,
    )
    assert AusfuehrungMixin in BotEngine.__bases__
    assert SignalUebergabeMixin not in BotEngine.__bases__
    assert AbsendungNachlaufMixin not in BotEngine.__bases__
    for mixin in (
        SignalUebergabeMixin,
        AbsendungNachlaufMixin,
        MandantenZugangMixin,
        HitlFreigabeMixin,
        AbsendungVorlaufMixin,
        AbsendungAbgangMixin,
        SignalDesktopEntscheidMixin,
        SignalDesktopAbsendungMixin,
        VerdraengungMixin,
    ):
        assert mixin in BotEngine.__mro__, mixin.__name__


def test_keine_methode_doppelt():
    from core.engine.absendung_abgang import AbsendungAbgangMixin
    from core.engine.absendung_nachlauf import AbsendungNachlaufMixin
    from core.engine.absendung_vorlauf import AbsendungVorlaufMixin
    from core.engine.hitl_freigabe import HitlFreigabeMixin
    from core.engine.mandanten_zugang import MandantenZugangMixin
    from core.engine.order_executor import OrderExecutorMixin
    from core.engine.signal_desktop_absendung import SignalDesktopAbsendungMixin
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin
    from core.engine.signal_uebergabe import SignalUebergabeMixin
    from core.engine.verdraengung import VerdraengungMixin

    klassen = (
        OrderExecutorMixin,
        SignalUebergabeMixin,
        AbsendungNachlaufMixin,
        MandantenZugangMixin,
        HitlFreigabeMixin,
        AbsendungVorlaufMixin,
        AbsendungAbgangMixin,
        SignalDesktopEntscheidMixin,
        SignalDesktopAbsendungMixin,
        VerdraengungMixin,
    )
    namen = {
        k: {n for n, v in vars(k).items() if callable(v) and not n.startswith("__")}
        for k in klassen
    }
    for a, b in combinations(klassen, 2):
        assert not namen[a] & namen[b], (a.__name__, b.__name__, namen[a] & namen[b])


def test_kern_importiert_komponierte_module_nicht():
    baum = ast.parse(KERN.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            quellen = [knoten.module or ""] + [a.name for a in knoten.names]
        elif isinstance(knoten, ast.Import):
            quellen = [a.name for a in knoten.names]
        else:
            continue
        for quelle in quellen:
            assert not quelle.endswith(
                (
                    "mandanten_zugang",
                    "ausfuehrung",
                    "hitl_freigabe",
                    "absendung_vorlauf",
                    "absendung_abgang",
                    "signal_desktop_entscheid",
                    "signal_desktop_absendung",
                    "verdraengung",
                )
            ), quelle
