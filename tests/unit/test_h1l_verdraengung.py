"""#4241 (H-1l) — die Verdrängung wohnt in ``verdraengung.py``.

Plan: ``docs/4241-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1l.

Drei freie Funktionen (``register_displacement_leg``, ``displacement_available_bp``,
``submit_deferred_displacement_sell``) und die Methode ``_recover_displacement`` ziehen aus
dem Kern ``order_executor.py`` in das komponierte Modul ``verdraengung.py``. ``BotEngine``
erhält die Methode über ``AusfuehrungMixin``. Was der Rückkauf *tut*, halten das Netz H-1a
(``test_h1a_verdraengung_charakterisierung.py``) und ``test_displacement_recovery.py`` fest;
hier stehen Heimat, Zugriffsregel und die zwei Halt-Stellen am Kern.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "verdraengung.py"
KERN = PAKET / "core" / "engine" / "order_executor.py"
FREIE = (
    "register_displacement_leg",
    "displacement_available_bp",
    "submit_deferred_displacement_sell",
)
VIER = FREIE + ("_recover_displacement",)
#: Epic §8.2: je Datei höchstens 800, je Funktion höchstens 150 Zeilen.
DATEI_GRENZE = 800
FUNKTION_GRENZE = 150
#: Plan §2.1 (Weg b): im Kern gebunden, im Modul nur als ``order_executor.<name>`` gelesen.
KERN_NAMEN = (
    "asyncio",
    "json",
    "logging",
    "kill_switch",
    "gateway_for",
    "_derived_coid",
    "_safe_publish",
    "RedisClient",
    "persist_pm_state_to_redis",
    "CompositionRoot",
    "MarketOrderRequest",
    "OrderSide",
    "TimeInForce",
    "OrderExecutorMixin",
)


def _baum(pfad: Path) -> ast.Module:
    return ast.parse(pfad.read_text(encoding="utf-8"))


# ── Heimat und Aufbau ─────────────────────────────────────────────────────────


def test_die_vier_symbole_liegen_in_verdraengung():
    baum = _baum(MODUL)
    frei = {
        k.name
        for k in baum.body
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert set(FREIE) <= frei
    (klasse,) = [
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "VerdraengungMixin"
    ]
    methoden = {
        k.name: type(k) for k in klasse.body if isinstance(k, ast.AsyncFunctionDef)
    }
    assert methoden == {"_recover_displacement": ast.AsyncFunctionDef}


def test_der_kern_bindet_keinen_der_vier_namen():
    gebunden = []
    for k in ast.walk(_baum(KERN)):
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            gebunden.append(k.name)
        elif isinstance(k, (ast.Import, ast.ImportFrom)):
            gebunden += [(a.asname or a.name).rpartition(".")[2] for a in k.names]
        elif isinstance(k, ast.Name) and isinstance(k.ctx, ast.Store):
            gebunden.append(k.id)
    assert not set(VIER) & set(gebunden)


def test_der_kern_importiert_verdraengung_nicht():
    """Entscheidung §3, „Zirkelimport“: das Modul importiert den Kern, nie umgekehrt."""
    importiert = set()
    for k in ast.walk(_baum(KERN)):
        if isinstance(k, ast.ImportFrom):
            importiert.add(k.module or "")
            importiert.update(a.name for a in k.names)
        elif isinstance(k, ast.Import):
            importiert.update(a.name for a in k.names)
    assert not any("verdraengung" in name for name in importiert)


def test_jeder_kern_name_existiert():
    """Plan §3: ``submit_deferred_displacement_sell`` fängt jede Ausnahme selbst (fail-open).
    Ein falsch geschriebener Kern-Name endete dort still als CRITICAL-Log — deshalb per ``ast``.
    """
    from core.engine import order_executor

    namen = {
        n.attr
        for n in ast.walk(_baum(MODUL))
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "order_executor"
    }
    assert set(KERN_NAMEN) <= namen
    fehlend = sorted(n for n in namen if n not in vars(order_executor))
    assert not fehlend, fehlend


def test_kein_kern_name_wird_frei_gelesen():
    """Weg b: ein frei gelesener Kern-Name ließe einen Patch am Kern ins Leere laufen."""
    frei = {
        (n.id, n.lineno)
        for n in ast.walk(_baum(MODUL))
        if isinstance(n, ast.Name) and n.id in KERN_NAMEN
    }
    assert not frei, sorted(frei)


def test_botengine_loest_recover_displacement_ueber_verdraengung_auf():
    from core.engine.ausfuehrung import AusfuehrungMixin
    from core.engine.base import BotEngine
    from core.engine.order_executor import OrderExecutorMixin
    from core.engine.verdraengung import VerdraengungMixin

    assert AusfuehrungMixin.__bases__[-1] is VerdraengungMixin
    assert "_recover_displacement" not in OrderExecutorMixin.__dict__
    assert (
        inspect.getattr_static(BotEngine, "_recover_displacement")
        is vars(VerdraengungMixin)["_recover_displacement"]
    )


def test_jede_funktion_liegt_unter_der_funktionsschwelle():
    gemessen = {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(PAKET, "core/engine")
        if b.datei == "core/engine/verdraengung.py"
    }
    assert set(gemessen) >= set(FREIE) | {"VerdraengungMixin._recover_displacement"}
    for name, n in gemessen.items():
        assert n <= FUNKTION_GRENZE, f"{name}: {n} Zeilen"
        assert n <= regeln.lade_vertrag()["groessen"]["funktion_schwelle"]


def test_modul_liegt_unter_der_dateischwelle():
    zeilen = len(MODUL.read_text(encoding="utf-8").splitlines())
    assert zeilen <= DATEI_GRENZE
    assert zeilen <= regeln.lade_vertrag()["groessen"]["datei_schwelle"]


def test_der_storno_der_rueckabwicklung_steht_im_vertrag_am_neuen_ort():
    ausnahmen = regeln.lade_vertrag()["broker_aufrufer"]["ausnahmen"]
    assert ausnahmen["core/engine/verdraengung.py"] == {"cancel_order_by_id": 1}
    assert "core/engine/order_executor.py" not in ausnahmen
    stornos = sum(
        e.get("cancel_order_by_id", 0)
        for d, e in ausnahmen.items()
        if d
        in (
            "core/engine/verdraengung.py",
            "core/engine/absendung_abgang.py",
            "core/engine/order_executor.py",
        )
    )
    assert stornos == 2


# ── Halt am Kern ──────────────────────────────────────────────────────────────


def test_halt_verhindert_den_rueckabwicklungs_buy(caplog):
    """#2467: Ein Halt am Kern-``kill_switch`` hält den Rückkauf auf, fail-closed.

    Charakterisierung: schon vor dem Umzug grün, danach unverändert.
    """
    from core.engine import order_executor as oe
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.compliance_guardian = MagicMock()
    schalter = MagicMock()
    schalter.check_halt.side_effect = RuntimeError("halt")
    kanal = MagicMock(publish=AsyncMock())
    api = MagicMock()
    api.get_order_by_id.return_value = SimpleNamespace(filled_qty=3.0)
    tor = AsyncMock()
    with (
        patch("core.engine.order_executor.kill_switch", schalter),
        patch.object(oe.OrderExecutorMixin, "_sende_durchs_tor", tor),
        patch(
            "core.engine.order_executor.RedisClient.get_redis",
            AsyncMock(return_value=kanal),
        ),
        caplog.at_level(logging.CRITICAL),
    ):
        asyncio.run(
            engine._recover_displacement(
                "u-4241", api, None, MagicMock(), "GATE", "verkauf-1", 3.0
            )
        )
    schalter.check_halt.assert_called_once_with("u-4241")
    tor.assert_not_awaited()
    engine.compliance_guardian.check_order.assert_not_called()
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    (aufruf,) = kanal.publish.await_args_list
    assert aufruf.args[0] == "explainability:u-4241"
    assert '"state_inconsistency"' in aufruf.args[1]


def test_verdraengungs_sell_liest_den_halt_am_kern():
    """Ein Patch auf ``core.engine.order_executor.kill_switch`` erreicht den SELL-Intent."""
    from core.engine.verdraengung import submit_deferred_displacement_sell

    schalter = MagicMock()
    schalter.is_halted.return_value = True
    tor = MagicMock()
    with (
        patch("core.engine.order_executor.kill_switch", schalter),
        patch("core.engine.order_executor.gateway_for", return_value=tor),
    ):
        ok = submit_deferred_displacement_sell(
            client=MagicMock(),
            pm=None,
            guardian=None,
            user_id="u-4241",
            symbol="GATE",
            qty=2.0,
            decision_id="dec-4241",
        )
    assert ok is True
    (aufruf,) = tor.submit.call_args_list
    assert aufruf.args[0].halted is True
    assert aufruf.kwargs["decision"].halted is True
    schalter.is_halted.assert_called_with("u-4241")
