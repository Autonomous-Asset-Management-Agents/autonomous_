"""#4238 (H-1i) — die Entscheid-Schritte des Desktop-Zweigs wohnen in ``signal_desktop_entscheid.py``.

Plan: ``docs/4238-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1i.

Vier Blöcke ohne ``self._schritt_*``-Aufruf ziehen aus ``_process_signal_event`` als Schritte
nach ``SignalDesktopEntscheidMixin``: PM-Zustand laden, Earnings-Guard, Verdrängungs-Entscheid
und Verkaufstor. Sie tragen ihre Werte im Zustand ``_Uebergabe``; die beiden mit frühem
``return`` liefern ``bool`` (Abbruch-Ergebnis, Muster G-1b). Was sie *im Ablauf* bewirken,
halten Geld-Gate und ``test_3819_uebergabe_charakterisierung.py`` fest; hier steht je Schritt
beides Ergebnis und der Abbruch durch den Dirigenten.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "signal_desktop_entscheid.py"
KERN = PAKET / "core" / "engine" / "order_executor.py"
VIER = (
    "_schritt_desktop_pm_laden",
    "_schritt_desktop_earnings",
    "_schritt_desktop_verdraengung",
    "_schritt_desktop_verkaufstor",
)


# ── Heimat und Aufbau ─────────────────────────────────────────────────────────


def test_vier_schritte_wohnen_im_entscheid_modul():
    from core.engine.order_executor import OrderExecutorMixin
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin

    for name in VIER:
        assert name in SignalDesktopEntscheidMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_botengine_loest_die_schritte_auf():
    from core.engine.base import BotEngine
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin

    for name in VIER:
        assert (
            inspect.getattr_static(BotEngine, name)
            is vars(SignalDesktopEntscheidMixin)[name]
        ), name


def test_modul_unter_den_schwellen():
    groessen = regeln.lade_vertrag()["groessen"]
    zeilen = len(MODUL.read_text(encoding="utf-8").splitlines())
    assert zeilen <= groessen["datei_schwelle"]
    gemessen = {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(PAKET, "core/engine")
        if b.datei == "core/engine/signal_desktop_entscheid.py"
    }
    for name in VIER:
        n = gemessen[f"SignalDesktopEntscheidMixin.{name}"]
        assert n <= groessen["funktion_schwelle"], f"{name}: {n} Zeilen"


def test_kern_importiert_das_modul_nicht():
    baum = ast.parse(KERN.read_text(encoding="utf-8"))
    importiert = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            importiert.add(knoten.module or "")
            importiert.update(a.name for a in knoten.names)
        elif isinstance(knoten, ast.Import):
            importiert.update(a.name for a in knoten.names)
    for verboten in ("signal_desktop_entscheid", "ausfuehrung"):
        assert not any(verboten in name for name in importiert), verboten


def test_kein_schritt_ruft_einen_schritt():
    """Option A: der Dirigent ruft jeden Schritt direkt (``_uebergabe_quelle`` liest flach)."""
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin

    for name in VIER:
        baum = ast.parse(
            textwrap.dedent(inspect.getsource(vars(SignalDesktopEntscheidMixin)[name]))
        )
        gerufen = [
            n.func.attr
            for n in ast.walk(baum)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("_schritt_")
        ]
        assert not gerufen, f"{name} ruft {gerufen}"


# ── Je Schritt ────────────────────────────────────────────────────────────────


def _engine(**felder):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.cloud_logger = MagicMock()
    for k, v in felder.items():
        setattr(engine, k, v)
    return engine


def _kontext(**felder):
    werte = {
        "current_price": 150.0,
        "decision_id": "dec-4238",
        "lstm_prediction": 0.7,
        "forecast_vol": None,
        "skew_percentile": None,
        "vote_coverage": None,
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def _st(action="BUY", qty=2.0, **felder):
    from core.engine.order_executor import _Uebergabe

    werte = {
        "event": SimpleNamespace(is_simulation=False, triggered_by_stop=False),
        "symbol": "AAPL",
        "action": action,
        "context": _kontext(),
        "should_log": True,
        "qty": qty,
    }
    werte.update(felder)
    return _Uebergabe(**werte)


def _lauf(coro):
    return asyncio.run(coro)


def test_pm_laden_stellt_einmal_je_prozess_her():
    pm = object()
    redis = object()
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    with (
        patch("core.engine.order_executor.RedisClient") as redis_client,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ) as restore,
    ):
        redis_client.get_redis = AsyncMock(return_value=redis)
        st1, st2 = _st(), _st()
        _lauf(engine._schritt_desktop_pm_laden(st1))
        _lauf(engine._schritt_desktop_pm_laden(st2))

    assert restore.await_count == 2
    erste, zweite = (c.args for c in restore.await_args_list)
    assert erste[0] is pm and erste[1] is redis
    assert erste[2] is zweite[2] is engine._pm_restored
    assert (st1.fb_pm, st1.fb_redis) == (pm, redis)


def test_pm_laden_ohne_pm_laesst_redis_leer():
    engine = _engine(active_strategy=None)
    st = _st()
    with patch("core.engine.order_executor.RedisClient") as redis_client:
        redis_client.get_redis = AsyncMock()
        _lauf(engine._schritt_desktop_pm_laden(st))
    assert (st.fb_pm, st.fb_redis) == (None, None)
    redis_client.get_redis.assert_not_awaited()


def test_earnings_veto_setzt_menge_null_und_auditiert():
    st = _st(qty=5.0)
    with (
        patch(
            "core.engine.order_executor._earnings_guard_veto",
            return_value=("blocked:earnings", "earnings in 1d"),
        ),
        patch("core.engine.order_executor._rec_outcome") as rec,
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        _lauf(_engine()._schritt_desktop_earnings(st))

    assert st.qty == 0.0
    rec.assert_called_once_with(
        "AAPL", "blocked:earnings", "earnings in 1d", decision_id="dec-4238"
    )
    audit.assert_awaited_once_with("AAPL", "BUY", "blocked:earnings", "earnings in 1d")


def test_ohne_veto_bleibt_die_menge():
    st = _st(qty=5.0)
    with patch(
        "core.engine.order_executor._earnings_guard_veto", return_value=None
    ) as veto:
        _lauf(_engine()._schritt_desktop_earnings(st))
    assert st.qty == 5.0
    veto.assert_called_once_with("AAPL", "BUY")


def test_earnings_fragt_bei_menge_null_nicht():
    st = _st(qty=0.0)
    with patch("core.engine.order_executor._earnings_guard_veto") as veto:
        _lauf(_engine()._schritt_desktop_earnings(st))
    veto.assert_not_called()
    assert st.qty == 0.0


def _pm(offen=True, grund="ok", ziel=None):
    pm = MagicMock()
    pm.should_open_new_position.return_value = (offen, grund, ziel)
    pm.can_sell_position.return_value = (True, "ok")
    return pm


def test_verdraengung_ablehnung_liefert_false_mit_einem_log_decision_und_audit():
    pm = _pm(offen=False, grund="cooldown active")
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st()
    with (
        patch("core.engine.order_executor._rec_outcome") as rec,
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
        patch("core.engine.order_executor._capture_outcome") as capture,
    ):
        assert _lauf(engine._schritt_desktop_verdraengung(st)) is False

    engine.cloud_logger.log_decision.assert_called_once_with(st.context)
    capture.assert_called_once_with(st.context)
    rec.assert_called_once_with("AAPL", "blocked:churn", "cooldown active")
    audit.assert_awaited_once_with(
        "AAPL", "BUY", "other", "portfolio declined: cooldown active"
    )


def test_verdraengung_zustimmung_liefert_true_und_schreibt_das_ziel():
    pm = _pm(ziel="MSFT")
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st()
    assert _lauf(engine._schritt_desktop_verdraengung(st)) is True
    assert (st.pc_symbol_to_close, st.pc_pm) == ("MSFT", pm)
    assert st.context.symbol_to_close == "MSFT"
    engine.cloud_logger.log_decision.assert_not_called()


def test_verdraengung_scoring_fehler_ist_fail_open():
    pm = _pm(ziel="MSFT")
    pm.score_opportunity.side_effect = RuntimeError("scoring kaputt")
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st()
    assert _lauf(engine._schritt_desktop_verdraengung(st)) is True
    assert st.pc_symbol_to_close is None
    assert st.pc_pm is pm


def test_verdraengung_ohne_pm_fragt_nicht():
    st = _st()
    assert _lauf(_engine(active_strategy=None)._schritt_desktop_verdraengung(st))
    assert (st.pc_symbol_to_close, st.pc_pm) == (None, None)


def _verkauf(stop=False):
    return SimpleNamespace(
        is_simulation=False, triggered_by_stop=stop, portfolio_reason=""
    )


def test_verkaufstor_haelt_bei_mindesthaltedauer():
    pm = _pm()
    pm.can_sell_position.return_value = (False, "min hold 2h")
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st(action="SELL", qty=3.0, event=_verkauf())
    with patch(
        "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
    ) as audit:
        assert _lauf(engine._schritt_desktop_verkaufstor(st)) is False
    audit.assert_awaited_once_with(
        "AAPL",
        "SELL",
        "other",
        "min-hold gate (#2713): min hold 2h",
        order_value=450.0,
    )


def test_verkaufstor_fail_closed_bei_fehler():
    pm = _pm()
    pm.can_sell_position.side_effect = RuntimeError("pm weg")
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st(action="SELL", qty=3.0, event=_verkauf())
    with patch(
        "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
    ) as audit:
        assert _lauf(engine._schritt_desktop_verkaufstor(st)) is False
    assert "gate error - fail-closed: pm weg" in audit.await_args.args[3]


def test_verkaufstor_laesst_einen_freien_verkauf_durch():
    pm = _pm()
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st(action="SELL", qty=3.0, event=_verkauf())
    assert _lauf(engine._schritt_desktop_verkaufstor(st)) is True
    pm.can_sell_position.assert_called_once_with("AAPL")


def test_risiko_ausstieg_passiert_ohne_frage():
    pm = _pm()
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    st = _st(action="SELL", qty=3.0, event=_verkauf(stop=True))
    assert _lauf(engine._schritt_desktop_verkaufstor(st)) is True
    pm.can_sell_position.assert_not_called()


def test_verkaufstor_laesst_einen_kauf_unberuehrt():
    pm = _pm()
    engine = _engine(active_strategy=SimpleNamespace(portfolio_manager=pm))
    assert _lauf(engine._schritt_desktop_verkaufstor(_st()))
    pm.can_sell_position.assert_not_called()


# ── Abbruch durch den Dirigenten ──────────────────────────────────────────────


def test_abbruch_ueberspringt_compliance_und_abschluss():
    """Ein abgelehnter Desktop-Kauf verlässt die ganze Übergabe: keine Compliance, und der
    Abschluss ``log_decision`` läuft nicht ein zweites Mal (nur der des Schritts)."""
    pm = _pm(offen=False, grund="book full")
    api = MagicMock()
    engine = _engine(
        api=api,
        active_uid=None,
        active_strategy=SimpleNamespace(portfolio_manager=pm),
        compliance_guardian=MagicMock(),
        get_active_tenant_clients=AsyncMock(return_value=[]),
        _market_closed_blocks_order=AsyncMock(return_value=False),
        _hitl_holds_order=AsyncMock(return_value=False),
        _schritt_compliance=AsyncMock(return_value=(True, 150.0)),
    )
    event = SimpleNamespace(
        symbol="AAPL",
        action="BUY",
        suggested_quantity=2.0,
        decision_context=_kontext(
            rl_stabilized_action=0,
            risk_approved=True,
            alpaca_order_id="",
        ),
        is_simulation=False,
        triggered_by_stop=False,
        portfolio_reason="",
    )
    with (
        patch("core.engine.order_executor.RedisClient") as redis_client,
        patch(
            "core.engine.order_executor.restore_pm_state_from_redis", new=AsyncMock()
        ),
        patch("core.engine.order_executor._earnings_guard_veto", return_value=None),
        patch("core.engine.order_executor._rec_outcome"),
        patch("core.engine.order_executor._capture_outcome"),
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
    ):
        redis_client.get_redis = AsyncMock(return_value=None)
        _lauf(engine._process_signal_event(event))

    engine._schritt_compliance.assert_not_awaited()
    engine._hitl_holds_order.assert_not_awaited()
    engine.cloud_logger.log_decision.assert_called_once()
    audit.assert_awaited_once_with(
        "AAPL", "BUY", "other", "portfolio declined: book full"
    )
    api.submit_order.assert_not_called()


def test_verkaufstor_fehler_haelt_und_loggt_mit_stacktrace(caplog):
    """Review #4342 (P1): Ein Fehler in ``can_sell_position`` haelt den SELL (fail-closed)
    und wird als WARNING mit Stack-Trace geloggt - nicht nur als INFO-Begruendung.

    CLAUDE.md 5.6: Fallbacks auf WARNING. Vorher sah ein Code-Fehler im PortfolioManager
    im Log aus wie eine regulaere Ablehnung der Mindesthaltedauer.
    """
    from core.engine.order_executor import _Uebergabe
    from core.engine.signal_desktop_entscheid import SignalDesktopEntscheidMixin

    def kaputt(_symbol):
        raise RuntimeError("pm kaputt")

    engine = SimpleNamespace(
        active_strategy=SimpleNamespace(
            portfolio_manager=SimpleNamespace(can_sell_position=kaputt)
        )
    )
    st = _Uebergabe(
        symbol="AAPL",
        action="SELL",
        context=SimpleNamespace(current_price=10.0),
        event=SimpleNamespace(),
        qty=1,
    )
    # Ein leeres Ereignis ist kein Risiko-Ausstieg (classify_exit_kind -> None),
    # also fragt das Tor den PortfolioManager.
    with (
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
        caplog.at_level("INFO"),
    ):
        weiter = _lauf(
            SignalDesktopEntscheidMixin._schritt_desktop_verkaufstor(engine, st)
        )

    assert weiter is False
    audit.assert_awaited_once()
    warnungen = [r for r in caplog.records if r.levelname == "WARNING" and r.exc_info]
    assert warnungen, "kein WARNING mit Stack-Trace fuer den Gate-Fehler"
    assert "AAPL" in warnungen[0].getMessage()
