"""#3317: der Exit-Pfad muss unter SIM_MODE die VIRTUELLE Uhr messen, nicht die Wanduhr.

Fehlerklasse wie #3119 (Haltefrist/Churn-Sperren), an zwei Stellen, die #3119 nicht erfasst
hat: ``_ratchet_entry_times`` stempelt die Eintrittszeit mit ``datetime.now`` und
``evaluate_position_stop`` misst dagegen ebenfalls die Wanduhr, weil der Aufruf von
``plan_position_stops`` kein ``now=`` durchreicht.

Wirkung ohne Fix: ein Sim-Lauf dauert real Stunden, also bleibt ``hours_held`` nahe null, der
Zeit-Multiplikator (>=4h/24h/72h -> 1,2/1,4/1,5) greift nie, und die -4-%-Verluststufe
(base_score 70) erreicht die Hartstop-Schwelle 90 nicht. Nur der -8-%-Hartstop feuert, weil er
100 direkt zurueckgibt.

Beide Stellen muessen GEMEINSAM fallen: nur die Messung umzustellen liefert ein negatives
``now - entry`` (geklemmt auf 0, unveraendert kaputt), nur den Stempel umzustellen laesst
``hours_held`` auf die Differenz Sim-Datum <-> heute explodieren (Multiplikator dauerhaft 1,5,
jede -4-%-Position sofort hart gestoppt).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from core.engine.trading_loop import TradingLoopMixin

# Sim-Zeitpunkt, bewusst weit in der Vergangenheit gegenueber der Wanduhr: nur so trennt der
# Test die beiden Uhren ueberhaupt.
_VIRTUAL = datetime(2026, 7, 14, 15, 30, 0, tzinfo=timezone.utc)


def _pos(symbol, qty=10.0, avg=100.0, current=100.0):
    return SimpleNamespace(
        symbol=symbol, qty=qty, avg_entry_price=avg, current_price=current
    )


def _engine(positions, trade_history):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.api = MagicMock()
    eng.api.get_all_positions = MagicMock(return_value=positions)
    eng.active_strategy = SimpleNamespace(
        portfolio_manager=SimpleNamespace(_trade_history=trade_history)
    )
    eng._process_signal_event = AsyncMock()
    return eng


def _cfg(**kw):
    base = {
        "POSITION_EXIT_ENTRY_TIME_RECONCILE_ENABLED": True,
        "POSITION_EXIT_HWM_TRAILING_ENABLED": False,
        "CONSENSUS_RETENTION_THRESHOLD": 0.0,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@allure.feature("VC-3 Trading & Execution")
@allure.story("#3317 Sim-Uhr im Exit-Pfad")
class TestSimClockExitPath:
    def test_ratchet_stamps_virtual_clock_under_sim(self):
        """Die Eintrittszeit einer neu erschienenen Position traegt die VIRTUELLE Zeit."""
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
            "core.engine.trading_loop.engine_now", return_value=_VIRTUAL
        ):
            stamped = eng._ratchet_entry_times([_pos("HOOD")], {})

        assert stamped["HOOD"] == _VIRTUAL, (
            "Eintrittszeit wurde mit der Wanduhr gestempelt statt mit engine_now() — "
            f"erhalten: {stamped['HOOD']}"
        )

    @pytest.mark.anyio
    async def test_plan_position_stops_receives_virtual_now(self):
        """Der Per-Zyklus-Risiko-Exit reicht die virtuelle Zeit als ``now=`` durch."""
        eng = _engine([_pos("HOOD")], {"HOOD": [_VIRTUAL - timedelta(hours=30)]})
        with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
            "core.engine.trading_loop.engine_now", return_value=_VIRTUAL
        ), patch(
            "core.engine.trading_loop.plan_position_stops", return_value=[]
        ) as planner:
            await eng._run_position_stop_checks()

        planner.assert_called_once()
        assert planner.call_args.kwargs.get("now") == _VIRTUAL, (
            "plan_position_stops bekam kein now= (faellt auf die Wanduhr zurueck) — "
            f"kwargs: {sorted(planner.call_args.kwargs)}"
        )

    @pytest.mark.anyio
    async def test_loss_tier_2_escalates_after_24_virtual_hours(self):
        """-4,5 % nach 25 virtuellen Stunden: Zeit-Multiplikator 1,4 -> Score 98, tier=risk.

        Ohne Fix misst die Wanduhr rund 1.400 h seit dem Sim-Einstand, der Multiplikator
        springt auf 1,5 und der Score wird 105 -> geklemmt auf 100. Der exakte Wert 98 ist
        also genau die Stelle, an der sich beide Uhren unterscheiden.
        """
        entry = _VIRTUAL - timedelta(hours=25)
        eng = _engine([_pos("HOOD", avg=100.0, current=95.5)], {"HOOD": [entry]})
        with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
            "core.engine.trading_loop.engine_now", return_value=_VIRTUAL
        ):
            stopped = await eng._run_position_stop_checks()

        assert stopped == {"HOOD"}
        event = eng._process_signal_event.call_args[0][0]
        assert "LOSS CUT" in event.decision_context.stop_type
        assert "(Score: 98)" in event.decision_context.stop_type, (
            "Zeit-Multiplikator wurde nicht aus der virtuellen Zeit gebildet — "
            f"erhalten: {event.decision_context.stop_type}"
        )

    @pytest.mark.anyio
    async def test_fresh_loser_is_not_hard_cut_in_sim(self):
        """-4,5 % nach 5 virtuellen Stunden: Multiplikator 1,2 -> Score 84 < 90, kein Hartstop.

        Das ist die inhaltlich wichtige Haelfte: ohne Fix wird eine frisch eroeffnete
        Verlustposition im Sim sofort hart gestoppt, weil die Wanduhr >72 h meldet.
        """
        entry = _VIRTUAL - timedelta(hours=5)
        eng = _engine([_pos("HOOD", avg=100.0, current=95.5)], {"HOOD": [entry]})
        with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
            "core.engine.trading_loop.engine_now", return_value=_VIRTUAL
        ):
            await eng._run_position_stop_checks()

        dispatched = eng._process_signal_event.call_args_list
        hard_cuts = [
            c[0][0].decision_context.stop_type
            for c in dispatched
            if "LOSS CUT" in (c[0][0].decision_context.stop_type or "")
        ]
        assert not hard_cuts, (
            "frische Verlustposition wurde hart gestoppt — die Haltedauer kam von der "
            f"Wanduhr: {hard_cuts}"
        )

    def test_wall_clock_unchanged_without_sim_mode(self, monkeypatch):
        """Byte-Identitaets-Wache: ohne SIM_MODE bleibt es die Wanduhr (muss immer gruen sein)."""
        from core.sim import clock

        monkeypatch.setattr(
            clock, "get_config", lambda: SimpleNamespace(SIM_MODE=False)
        )
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        with patch("core.engine.trading_loop.get_config", return_value=_cfg()):
            stamped = eng._ratchet_entry_times([_pos("HOOD")], {})

        drift = abs((datetime.now(timezone.utc) - stamped["HOOD"]).total_seconds())
        assert drift < 5, f"Nicht-Sim-Pfad weicht von der Wanduhr ab: {drift}s"
