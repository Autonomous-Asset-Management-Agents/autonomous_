"""#4271 (H-3i) — Skalierer und Audit wohnen in ``core/risk_skalierer.py``.

Plan: ``docs/4271-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3, Abschnitt H-3i.

Der VIX-Block, ``resolve_vix`` und die sieben Skalierer- und Audit-Funktionen ziehen in ein
reines Modul ohne Rückimport. Der Kern exportiert alle zehn Namen wieder; Importeure von außen
und die ``_rm.``-Lesestellen in Bemessung und Vorprüfung bleiben unverändert.

Teil „Werte“ ist Charakterisierung: vor dem Umzug geschrieben und dort grün, über
``core.risk_manager`` importiert. Teil „Ort“ hält den Umzug selbst fest.
"""

from __future__ import annotations

import ast
import importlib
import logging
import math
import subprocess
import sys
import tomllib
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.unit import _risiko_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
SKALIERER = PAKET / "core" / "risk_skalierer.py"
VERTRAG = PAKET / "tests" / "architecture" / "vertrag.toml"

#: Die zehn Namen, die mit H-3i umziehen (Entscheidung #4185 §1/§2).
NAMEN = (
    "VIX_FAILCLOSED_SENTINEL",
    "VIX_UNCONFIRMED_MARKER",
    "resolve_vix",
    "_bull_exposure_cfg",
    "vix_ladder_scaler",
    "vol_targeting_scaler",
    "skew_size_tilt",
    "coverage_size_discount",
    "apply_vol_targeting_audit",
    "apply_sizing_mode_audit",
)

_AUS = {
    "VOL_TARGETING_SIZING_ENABLED": False,
    "SKEW_SIZE_TILT_ENABLED": False,
    "COVERAGE_SIZING_STRENGTH": 0.0,
}


def _config(**werte):
    """Patcht ``config.<NAME>`` für die Dauer eines ``with``-Blocks; Default: alle Flags aus."""
    stapel = ExitStack()
    for name, wert in {**_AUS, **werte}.items():
        stapel.enter_context(patch(f"config.{name}", wert, create=True))
    return stapel


# ── Werte (Charakterisierung) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "daten, erwartet",
    [
        (None, (45.0, False, "unconfirmed")),
        ({}, (45.0, False, "unconfirmed")),
        ({"vix": True}, (45.0, False, "invalid")),
        ({"vix": "x"}, (45.0, False, "invalid")),
        ({"vix": -1}, (45.0, False, "invalid")),
        ({"vix": math.inf}, (45.0, False, "invalid")),
        ({"vix": 20}, (20.0, True, "confirmed")),
    ],
)
def test_resolve_vix(daten, erwartet):
    from core.risk_manager import VIX_FAILCLOSED_SENTINEL, resolve_vix

    assert VIX_FAILCLOSED_SENTINEL == 45.0
    assert resolve_vix(daten) == erwartet


def test_vix_marker():
    from core.risk_manager import VIX_UNCONFIRMED_MARKER

    assert VIX_UNCONFIRMED_MARKER == "[VIX UNCONFIRMED] "


@pytest.mark.parametrize(
    "vix, bull, calm, erwartet",
    [
        (41.0, False, 1.0, 0.3),
        (40.0, False, 1.0, 0.4),
        (35.0, False, 1.0, 0.65),
        (25.0, False, 1.0, 0.9),
        (18.0, False, 1.0, 1.0),
        (18.0, True, 1.2, 1.2),
        (19.0, True, 1.2, 0.9),
    ],
)
def test_vix_ladder_scaler(vix, bull, calm, erwartet):
    from core.risk_manager import vix_ladder_scaler

    assert vix_ladder_scaler(vix, bull, calm) == pytest.approx(erwartet)


def test_bull_exposure_cfg():
    from core.risk_manager import _bull_exposure_cfg

    with _config(BULL_EXPOSURE_ENABLED=True, BULL_EXPOSURE_CALM_SCALER=1.15):
        assert _bull_exposure_cfg() == (True, pytest.approx(1.15))
    with _config(BULL_EXPOSURE_ENABLED=False, BULL_EXPOSURE_CALM_SCALER=1.0):
        assert _bull_exposure_cfg() == (False, 1.0)


@pytest.mark.parametrize(
    "flag, einfluss, prognose, erwartet",
    [
        (False, 1.0, 0.03, 1.0),
        (True, 1.0, 0.03, 0.5),
        (True, 1.0, 0.01, 1.5),
        (True, 1.0, None, 1.0),
        (True, 1.0, True, 1.0),
        (True, 0.5, 0.03, 0.75),
    ],
)
def test_vol_targeting_scaler(flag, einfluss, prognose, erwartet):
    from core.risk_manager import vol_targeting_scaler

    with _config(
        VOL_TARGETING_SIZING_ENABLED=flag,
        VOL_TARGET_DAILY_VOL=0.015,
        VOL_SIZE_SCALER_LO=0.5,
        VOL_SIZE_SCALER_HI=1.5,
        VIX_SIZE_INFLUENCE=einfluss,
    ):
        assert vol_targeting_scaler(prognose) == pytest.approx(erwartet)


@pytest.mark.parametrize(
    "flag, deckel, perzentil, erwartet",
    [
        (False, 0.2, 1.0, 1.0),
        (True, 0.0, 1.0, 1.0),
        (True, 0.2, 1.0, 1.2),
        (True, 0.2, 0.0, 0.8),
        (True, 0.2, 0.5, 1.0),
        (True, 0.2, 1.5, 1.0),
        (True, 0.2, None, 1.0),
    ],
)
def test_skew_size_tilt(flag, deckel, perzentil, erwartet):
    from core.risk_manager import skew_size_tilt

    with _config(SKEW_SIZE_TILT_ENABLED=flag, SKEW_SIZE_TILT_CAP=deckel):
        assert skew_size_tilt(perzentil) == pytest.approx(erwartet)


@pytest.mark.parametrize(
    "staerke, abdeckung, erwartet",
    [
        (0.0, 0.25, 1.0),
        (0.5, 0.25, 0.75),
        (0.5, 1.0, 1.0),
        (0.5, 0.0, 1.0),
        (0.5, None, 1.0),
    ],
)
def test_coverage_size_discount(staerke, abdeckung, erwartet):
    from core.risk_manager import coverage_size_discount

    with _config(COVERAGE_SIZING_STRENGTH=staerke):
        assert coverage_size_discount(abdeckung) == pytest.approx(erwartet)


def test_apply_vol_targeting_audit():
    from core.risk_manager import apply_vol_targeting_audit

    ziel = SimpleNamespace()
    with _config():
        assert apply_vol_targeting_audit(ziel, 0.03) == 1.0
    assert not hasattr(ziel, "risk_size_scaler")

    with _config(
        VOL_TARGETING_SIZING_ENABLED=True,
        VOL_TARGET_DAILY_VOL=0.015,
        VOL_SIZE_SCALER_LO=0.5,
        VOL_SIZE_SCALER_HI=1.5,
        VIX_SIZE_INFLUENCE=1.0,
        COVERAGE_SIZING_STRENGTH=0.5,
    ):
        assert apply_vol_targeting_audit(ziel, 0.03, None, 0.25) == pytest.approx(0.375)
        assert apply_vol_targeting_audit(None, 0.03) == pytest.approx(0.5)
    assert ziel.risk_size_scaler == pytest.approx(0.375)


def test_apply_sizing_mode_audit(caplog):
    from core.risk_manager import apply_sizing_mode_audit

    ziel = SimpleNamespace()
    apply_sizing_mode_audit(
        ziel,
        {
            "sizing_mode": "b",
            "sizing_target_weight": "0.1",
            "binding_limit": "cash",
            "binding_cap_value": 250,
        },
    )
    assert vars(ziel) == {
        "sizing_mode": "b",
        "sizing_target_weight": 0.1,
        "sizing_binding_limit": "cash",
        "sizing_cap_value": 250.0,
    }

    leer = SimpleNamespace()
    apply_sizing_mode_audit(leer, None)
    apply_sizing_mode_audit(leer, {})
    apply_sizing_mode_audit(None, {"sizing_mode": "a"})
    assert vars(leer) == {}

    kaputt = SimpleNamespace()
    with caplog.at_level(logging.WARNING):
        apply_sizing_mode_audit(
            kaputt, {"sizing_mode": "a", "sizing_target_weight": "x"}
        )
    assert vars(kaputt) == {"sizing_mode": "a"}
    assert "sizing-mode audit mirror failed" in caplog.text


# ── Ort ───────────────────────────────────────────────────────────────────────


def _definiert(text: str) -> set[str]:
    """Namen, die ein Modultext auf Modulebene per ``def`` oder Zuweisung selbst bindet."""
    baum = ast.parse(text)
    namen: set[str] = set()
    for k in baum.body:
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            namen.add(k.name)
        elif isinstance(k, (ast.Assign, ast.AnnAssign)):
            ziele = k.targets if isinstance(k, ast.Assign) else [k.target]
            namen |= {
                n.id for z in ziele for n in ast.walk(z) if isinstance(n, ast.Name)
            }
    return namen


def test_skalierer_definiert_die_zehn_namen():
    assert SKALIERER.is_file(), f"{SKALIERER} fehlt"
    fehlt = set(NAMEN) - _definiert(SKALIERER.read_text(encoding="utf-8"))
    assert not fehlt, f"risk_skalierer.py definiert nicht: {sorted(fehlt)}"


def test_kern_definiert_keinen_der_zehn():
    kern = rq.texte_risikoverwaltung()[rq.PAKET / rq.KERN]
    doppelt = set(NAMEN) & _definiert(kern)
    assert not doppelt, f"Der Kern definiert noch selbst: {sorted(doppelt)}"


def test_ein_name_ein_funktionsobjekt():
    import core.risk_manager as kern

    skalierer = importlib.import_module("core.risk_skalierer")
    for name in NAMEN:
        assert getattr(kern, name) is getattr(skalierer, name), name


def test_skalierer_importiert_nichts_aus_der_risikoverwaltung():
    baum = ast.parse(SKALIERER.read_text(encoding="utf-8"))
    rueck = [
        f"{k.lineno}: {ast.unparse(k)}"
        for k in ast.walk(baum)
        if isinstance(k, (ast.Import, ast.ImportFrom))
        and any(
            m == "core.risk_manager" or m.startswith("core.risk_")
            for m in (
                [a.name for a in k.names]
                if isinstance(k, ast.Import)
                else [k.module or ""] + [f"{k.module}.{a.name}" for a in k.names]
            )
        )
    ]
    assert not rueck, rueck


def test_skalierer_zuerst_importierbar():
    code = (
        "import sys\n"
        "import core.risk_skalierer as s\n"
        "assert not any(m.startswith('core.risk_') and m != 'core.risk_skalierer'"
        " for m in sys.modules), sorted(sys.modules)\n"
        "import core.risk_manager as k\n"
        "assert k.resolve_vix is s.resolve_vix\n"
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr[-2000:]


def test_skalierer_steht_im_kapitalschutz():
    from tests.unit.test_3392_review_gate import KAPITALSCHUTZ

    assert "ai_trading_bot/core/risk_skalierer.py" in KAPITALSCHUTZ


def test_vertragszeilen_ziehen_mit():
    vertrag = tomllib.loads(VERTRAG.read_text(encoding="utf-8"))
    tabelle = vertrag["getattr_widersprueche"]["ausnahmen"]
    assert tabelle.get("core/risk_skalierer.py") == {
        "BULL_EXPOSURE_CALM_SCALER": 1,
        "VOL_TARGETING_SIZING_ENABLED": 1,
    }
    kern = tabelle.get(rq.KERN.as_posix(), {})
    assert "BULL_EXPOSURE_CALM_SCALER" not in kern
    assert "VOL_TARGETING_SIZING_ENABLED" not in kern
