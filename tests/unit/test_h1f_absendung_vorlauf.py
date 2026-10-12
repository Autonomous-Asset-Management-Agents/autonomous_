"""#4235 (H-1f) — der Vorlauf der Absendung je Mandant wohnt in ``absendung_vorlauf.py``.

Plan: ``docs/4235-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1f.

Sieben Schritte von ``OrderExecutorMixin`` ziehen wörtlich nach ``AbsendungVorlaufMixin``
um. ``BotEngine`` erhält sie über ``AusfuehrungMixin``. Disjunktheit der Methodennamen
prüft ``test_h1d_mandanten_zugang.py`` zentral; hier steht, dass ``BotEngine`` jeden der
sieben auf genau die umgezogene Methode auflöst (der Kern steht in der MRO vorn).
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "absendung_vorlauf.py"
KERN = PAKET / "core" / "engine" / "order_executor.py"
SIEBEN = (
    "_schritt_mandant_vorbereiten",
    "_schritt_mandant_bestand",
    "_schritt_mandant_bemessung",
    "_schritt_mandant_portfolio",
    "_schritt_mandant_verdraengung",
    "_schritt_mandant_sofort_verkaufen",
    "_schritt_mandant_menge",
)


def test_sieben_schritte_wohnen_im_vorlauf():
    from core.engine.absendung_vorlauf import AbsendungVorlaufMixin
    from core.engine.order_executor import OrderExecutorMixin

    for name in SIEBEN:
        assert name in AbsendungVorlaufMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_botengine_loest_den_vorlauf_auf():
    from core.engine.absendung_vorlauf import AbsendungVorlaufMixin
    from core.engine.base import BotEngine

    for name in SIEBEN:
        assert (
            inspect.getattr_static(BotEngine, name) is vars(AbsendungVorlaufMixin)[name]
        ), name


def test_vorlauf_bleibt_unter_der_dateischwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["datei_schwelle"]
    zeilen = len(MODUL.read_text(encoding="utf-8").splitlines())
    assert zeilen <= schwelle


def test_kern_importiert_vorlauf_nicht():
    from core.engine.absendung_vorlauf import (  # noqa: F401 — rot ohne Modul
        AbsendungVorlaufMixin,
    )

    baum = ast.parse(KERN.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            quellen = [knoten.module or ""] + [a.name for a in knoten.names]
        elif isinstance(knoten, ast.Import):
            quellen = [a.name for a in knoten.names]
        else:
            continue
        for quelle in quellen:
            assert not quelle.endswith("absendung_vorlauf"), quelle
