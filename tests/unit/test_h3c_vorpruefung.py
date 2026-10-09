"""#4265 (H-3c+H-3d) — die Vorprüfung wohnt in ``core/risk_vorpruefung.py``.

Plan: ``docs/4265-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3, Abschnitte H-3c und H-3d.

* H-3c: ``evaluate_new_trade`` zieht als ``VorpruefungMixin`` um. Der Kern-Import steht am
  Dateiende, ``tracer`` bleibt Patch-Ziel am Modulobjekt ``core.risk_manager``.
* H-3d: ``evaluate_new_trade`` zerfällt in benannte Schritte. Die Charakterisierung je
  Regelaktion hält das Ist-Verhalten fest, bevor zerlegt wird — Rückgabe, Span-Attribute in
  ihrer Reihenfolge und die fail-open-WARNING je Regel (#1236).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "risk_vorpruefung.py"
NUTZER = "nutzer-4265"


class _Uhr:
    def now(self, tz=None):
        import datetime

        return datetime.datetime(2026, 10, 8, tzinfo=datetime.timezone.utc)


def _rm():
    """Echter ``RiskManager`` mit lokalem Halt — der globale Kill-Switch bleibt unberührt."""
    from core.kill_switch import LokalerHalt
    from core.risk_manager import RiskManager

    return RiskManager(
        MagicMock(), 10_000.0, user_id=NUTZER, kill_switch=LokalerHalt(), clock=_Uhr()
    )


def test_evaluate_new_trade_wohnt_in_vorpruefung():
    from core.risk_manager import RiskManager
    from core.risk_vorpruefung import VorpruefungMixin

    assert "evaluate_new_trade" in VorpruefungMixin.__dict__
    assert "evaluate_new_trade" not in RiskManager.__dict__
    assert issubclass(RiskManager, VorpruefungMixin)


def test_kern_import_am_ende():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    letzte_klasse = max(
        i for i, k in enumerate(baum.body) if isinstance(k, ast.ClassDef)
    )
    kern = [
        i
        for i, k in enumerate(baum.body)
        if (isinstance(k, ast.ImportFrom) and k.module == "core.risk_manager")
        or (
            isinstance(k, ast.ImportFrom)
            and k.module == "core"
            and any(a.name == "risk_manager" for a in k.names)
        )
        or (
            isinstance(k, ast.Import)
            and any(a.name == "core.risk_manager" for a in k.names)
        )
    ]
    assert len(kern) == 1, f"genau ein Kern-Import erwartet, gefunden: {kern}"
    knoten = baum.body[kern[0]]
    assert isinstance(knoten, ast.ImportFrom) and knoten.module == "core"
    assert [(a.name, a.asname) for a in knoten.names] == [("risk_manager", "_rm")]
    assert kern[0] > letzte_klasse, "Kern-Import steht vor der Klasse (Zirkelimport)"


def test_patch_auf_kern_tracer_trifft_vorpruefung():
    rm = _rm()
    with patch("core.risk_manager.tracer") as tracer, patch.object(
        rm.ai_rules_singleton, "get_rules", return_value=[]
    ):
        ergebnis = rm.evaluate_new_trade(
            "AAPL", "buy", {"vix": 20.0, "indicators": {"features": {}}}, 2.0
        )
    assert ergebnis == (True, "Approved", {"size_scaler": 1.0, "sl_multiplier": 2.0})
    tracer.start_as_current_span.assert_called_once_with("risk.evaluate_trade")


# ── H-3d: Charakterisierung je Regelaktion (vor der Zerlegung grün) ─────────────

_MARKT = {"vix": 20.0, "indicators": {"features": {"rsi": 70.0}}}
_FREI = {"size_scaler": 1.0, "sl_multiplier": 2.0}


def _pruefe(regeln, side="buy", markt=_MARKT):
    """Echter Aufruf mit Regel-Stub; liefert (Ergebnis, Span-Attribute in Reihenfolge)."""
    rm = _rm()
    with patch("core.risk_manager.tracer") as tracer, patch.object(
        rm.ai_rules_singleton, "get_rules", return_value=regeln
    ):
        span = tracer.start_as_current_span.return_value.__enter__.return_value
        ergebnis = rm.evaluate_new_trade("AAPL", side, markt, 2.0)
    attribute = [c.args for c in span.set_attribute.call_args_list]
    return ergebnis, attribute


_SPAN_FREI = [("symbol", "AAPL"), ("trade.side", "buy"), ("risk.approved", True)]


def test_block_trade_grundtext_und_span_attribute():
    ergebnis, attribute = _pruefe(
        [{"trigger": {}, "action": "block_trade", "reason": "Crash"}]
    )
    assert ergebnis == (False, "Blocked by AI Rule: Crash", {})
    assert attribute == _SPAN_FREI + [
        ("risk.approved", False),
        ("risk.reason", "Blocked by AI Rule: Crash"),
    ]


def test_reduce_size_nimmt_das_minimum():
    regeln = [
        {"trigger": {}, "action": "reduce_size", "value": 0.7},
        {"trigger": {}, "action": "reduce_size"},  # Vorgabe 0.5
        {"trigger": {}, "action": "reduce_size", "value": 0.9},
    ]
    ergebnis, attribute = _pruefe(regeln)
    assert ergebnis == (True, "Approved", {**_FREI, "size_scaler": 0.5})
    assert attribute == _SPAN_FREI


def test_increase_size_nimmt_das_maximum():
    regeln = [
        {"trigger": {}, "action": "increase_size", "value": 1.2},
        {"trigger": {}, "action": "increase_size"},  # Vorgabe 1.5
        {"trigger": {}, "action": "increase_size", "value": 1.3},
    ]
    ergebnis, _ = _pruefe(regeln)
    assert ergebnis == (True, "Approved", {**_FREI, "size_scaler": 1.5})


def test_tighten_sl_und_widen_sl():
    enger, _ = _pruefe(
        [
            {"trigger": {}, "action": "tighten_sl"},  # Vorgabe 1.5 < 2.0
            {"trigger": {}, "action": "tighten_sl", "value": 1.8},
        ]
    )
    assert enger == (True, "Approved", {**_FREI, "sl_multiplier": 1.5})
    weiter, _ = _pruefe(
        [
            {"trigger": {}, "action": "widen_sl", "value": 2.5},
            {"trigger": {}, "action": "widen_sl"},  # Vorgabe 3.0
        ]
    )
    assert weiter == (True, "Approved", {**_FREI, "sl_multiplier": 3.0})


def test_probation_wird_nach_dem_treffer_uebersprungen():
    ergebnis, attribute = _pruefe(
        [{"trigger": {}, "action": "block_trade", "status": "probation", "reason": "P"}]
    )
    assert ergebnis == (True, "Approved", _FREI)
    assert attribute == _SPAN_FREI


def test_proactive_signal_wird_vor_dem_match_uebersprungen(caplog):
    # Ein Trigger, der im Match werfen würde: übersprungen, bevor er gelesen wird.
    with caplog.at_level("WARNING"):
        ergebnis, _ = _pruefe(
            [{"trigger": {"vix_gt": "kaputt"}, "action": "proactive_signal"}]
        )
    assert ergebnis == (True, "Approved", _FREI)
    assert "AI rule evaluation failed" not in caplog.text


def test_side_passt_nicht():
    ergebnis, _ = _pruefe(
        [
            {
                "trigger": {"side": "SELL"},
                "action": "block_trade",
                "reason": "nur Verkauf",
            }
        ]
    )
    assert ergebnis == (True, "Approved", _FREI)


def test_vix_gt_als_string_warnt_und_die_naechste_regel_wirkt(caplog):
    # Die Seite passt nicht — trotzdem wertet der Match vix_gt aus und wirft (keine Abkürzung).
    regeln = [
        {
            "id": "r-kaputt",
            "trigger": {"side": "sell", "vix_gt": "kaputt"},
            "action": "block_trade",
        },
        {"trigger": {}, "action": "reduce_size", "value": 0.4},
    ]
    with caplog.at_level("WARNING"):
        ergebnis, _ = _pruefe(regeln)
    assert ergebnis == (True, "Approved", {**_FREI, "size_scaler": 0.4})
    treffer = [
        r for r in caplog.records if "AI rule evaluation failed" in r.getMessage()
    ]
    assert len(treffer) == 1
    assert treffer[0].levelname == "WARNING" and treffer[0].exc_info
    assert "r-kaputt" in treffer[0].getMessage()
    assert treffer[0].funcName == "evaluate_new_trade"


def test_vix_warnung_traegt_den_namen_des_dirigenten(caplog):
    # Das H-3a-Netz schreibt funcName mit (Referenz: "evaluate_new_trade") — ein Schritt darf
    # den Log-Record nicht umbenennen.
    rm = _rm()
    with caplog.at_level("WARNING"):
        ergebnis = rm.evaluate_new_trade("AAPL", "buy", {}, 2.0)
    assert ergebnis[0] is False
    treffer = [r for r in caplog.records if "VIX unconfirmed" in r.getMessage()]
    assert [(r.levelname, r.funcName) for r in treffer] == [
        ("WARNING", "evaluate_new_trade")
    ]


@pytest.mark.parametrize(
    "schluessel, grenze, greift",
    [
        ("indicators.features.rsi.gt", 60, True),
        ("indicators.features.rsi.gt", 80, False),
        ("indicators.features.rsi.lt", 80, True),
        ("indicators.features.rsi.lt", 60, False),
    ],
)
def test_feature_gt_und_lt(schluessel, grenze, greift):
    ergebnis, _ = _pruefe(
        [{"trigger": {schluessel: grenze}, "action": "reduce_size", "value": 0.3}]
    )
    erwartet = {**_FREI, "size_scaler": 0.3} if greift else _FREI
    assert ergebnis == (True, "Approved", erwartet)


# ── H-3d: Struktur ──────────────────────────────────────────────────────────────

_SCHRITTE = ("_vorpruefung_halt", "_vorpruefung_vix", "_regel_passt", "_regel_anwenden")


def _mixin() -> ast.ClassDef:
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    return next(
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "VorpruefungMixin"
    )


def _methoden() -> list[ast.FunctionDef]:
    return [m for m in _mixin().body if isinstance(m, ast.FunctionDef)]


def test_schritte_existieren():
    from core.risk_vorpruefung import VorpruefungMixin

    fehlt = [n for n in _SCHRITTE if n not in VorpruefungMixin.__dict__]
    assert not fehlt, f"Schritte fehlen in VorpruefungMixin: {fehlt}"


def test_kein_schritt_ueber_150():
    zu_lang = {}
    for m in _methoden():
        start = min([m.lineno, *(d.lineno for d in m.decorator_list)])
        zeilen = m.end_lineno - start + 1
        if zeilen > 150:
            zu_lang[m.name] = zeilen
    assert not zu_lang, f"über 150 Zeilen: {zu_lang}"


def test_span_attribute_nur_im_dirigenten():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    alle = [
        k
        for k in ast.walk(baum)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "set_attribute"
    ]
    dirigent = next(m for m in _methoden() if m.name == "evaluate_new_trade")
    im_dirigenten = [
        k
        for k in ast.walk(dirigent)
        if isinstance(k, ast.Call)
        and isinstance(k.func, ast.Attribute)
        and k.func.attr == "set_attribute"
    ]
    assert alle and len(alle) == len(
        im_dirigenten
    ), f"set_attribute ausserhalb des Dirigenten: {len(alle) - len(im_dirigenten)}"
    # Ohne Schritte ist jede Methode der Dirigent — die Prüfung trüge nichts.
    assert len(_methoden()) > 1, "evaluate_new_trade ist noch nicht zerlegt"
