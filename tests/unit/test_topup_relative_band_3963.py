"""#3963 — Nachkauf-Sperre relativ zum Zielgewicht, Nachkauf nur bis zum Ziel.

Plan: docs/3963-topup-totband-relativ/implementation_plan.md (plan-approved, PR #3970).

Zwei Defekte, ein Ziel:
  * Die Sperre mass in festen Prozentpunkten (5). Bei 20 Plaetzen liegt das Ziel bei 5 % oder
    darunter -> es wurde nie nachgekauft (gemessen: FIX 2,7 % bei Ziel 5 %, 363x gesperrt).
  * Ein Nachkauf kaufte eine volle Tranche statt der Luecke -> Ueberschiessen (ABNB/MRK).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.vc2  # Stufen-Marker (#3396): Positionsgroesse = VC-2

from core.portfolio_manager import (  # noqa: E402
    OpportunityScore,
    PortfolioManager,
    PositionScore,
)

CAPITAL = 100_000.0


@pytest.fixture(autouse=True)
def _clean_weight_b_20_slots(monkeypatch):
    """Ausgelieferter Zielpfad: clean-weight "b", 20 Plaetze, kein Vol-Faktor (Prognose None)."""
    import config as _c

    monkeypatch.setattr(_c, "CLEAN_WEIGHT_SIZING", "b", raising=False)
    monkeypatch.setattr(_c.get_config(), "CLEAN_WEIGHT_SIZING", "b", raising=False)
    monkeypatch.setattr(
        _c.get_config(), "FULL_UNIVERSE_MAX_POSITIONS", 20, raising=False
    )
    monkeypatch.setattr(_c, "FULL_UNIVERSE_MAX_POSITIONS", 20, raising=False)


@pytest.fixture(autouse=True)
def _no_regime(monkeypatch):
    monkeypatch.setattr(
        "core.engine.regime_signal.active_throttle_factor", lambda *a, **k: 1.0
    )


def _pm(capital=CAPITAL):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm.refresh_positions = lambda: None
    pm._last_refresh_ok = True
    pm._conviction_ewma_enabled = False
    pm._within_order_cooldown = lambda s: False
    pm._can_trade_symbol_when_room = lambda s: True
    return pm


def _hold(pm, symbol, value):
    pm._position_scores[symbol] = PositionScore(
        symbol=symbol,
        qty=value / 100.0,
        avg_entry=100.0,
        current_price=100.0,
        market_value=value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _opp(symbol="X", conf=0.8):
    return OpportunityScore(
        symbol=symbol, current_price=100.0, model_confidence=conf, total_score=80.0
    )


# --------------------------------------------------------------------------- Sperre
class TestRelativeBand:
    def test_default_is_a_quarter_of_the_target(self):
        import config as _c

        assert _c.get_config().POSITION_TOPUP_DEAD_BAND_REL == pytest.approx(0.25)
        assert _pm()._topup_dead_band_rel == pytest.approx(0.25)

    def test_small_position_is_released_for_topup(self):
        pm = _pm()
        _hold(pm, "X", 2_700.0)  # 2,7 % bei Ziel 5,0 %
        assert pm._topup_dead_band_reason(_opp()) is None

    def test_small_deviation_stays_blocked(self):
        pm = _pm()
        _hold(pm, "X", 4_000.0)  # 4,0 % bei Ziel 5,0 % -> Luecke 20 % < 25 %
        reason = pm._topup_dead_band_reason(_opp())
        assert reason is not None and "dead-band" in reason

    def test_exactly_at_the_band_edge_is_blocked(self):
        pm = _pm()
        _hold(pm, "X", 3_750.0)  # genau 75 % des Ziels
        assert pm._topup_dead_band_reason(_opp()) is not None

    def test_same_behaviour_at_ten_slots(self, monkeypatch):
        import config as _c

        monkeypatch.setattr(
            _c.get_config(), "FULL_UNIVERSE_MAX_POSITIONS", 10, raising=False
        )
        monkeypatch.setattr(_c, "FULL_UNIVERSE_MAX_POSITIONS", 10, raising=False)
        pm = _pm()
        _hold(pm, "X", 5_000.0)  # 5 % bei Ziel 10 %
        assert pm._topup_dead_band_reason(_opp()) is None
        _hold(pm, "X", 8_000.0)  # 8 % bei Ziel 10 %
        assert pm._topup_dead_band_reason(_opp()) is not None

    def test_new_name_never_blocked(self):
        assert _pm()._topup_dead_band_reason(_opp()) is None

    @pytest.mark.parametrize("bad", [-0.1, 0.95, float("nan")])
    def test_out_of_range_falls_back_with_warning(self, monkeypatch, caplog, bad):
        import config as _c

        monkeypatch.setattr(_c, "POSITION_TOPUP_DEAD_BAND_REL", bad, raising=False)
        with caplog.at_level("WARNING"):
            pm = _pm()
        assert pm._topup_dead_band_rel == pytest.approx(0.25)
        assert any("POSITION_TOPUP_DEAD_BAND_REL" in r.message for r in caplog.records)


# --------------------------------------------------------------------------- Luecke
class TestTopupGapValue:
    def test_gap_against_the_same_target_as_the_band(self):
        pm = _pm()
        _hold(pm, "X", 2_700.0)
        assert pm.topup_gap_value(_opp()) == pytest.approx(2_300.0)

    def test_not_held_has_no_gap(self):
        assert _pm().topup_gap_value(_opp()) is None

    def test_above_target_gap_is_zero(self):
        pm = _pm()
        _hold(pm, "X", 14_300.0)  # MRK-Lage
        assert pm.topup_gap_value(_opp()) == pytest.approx(0.0)

    def test_unconfirmed_holdings_fail_closed(self):
        pm = _pm()
        _hold(pm, "X", 2_700.0)
        pm._last_refresh_ok = False
        assert pm.topup_gap_value(_opp()) == pytest.approx(0.0)

    def test_regime_factor_shrinks_the_target_once(self, monkeypatch):
        monkeypatch.setattr(
            "core.engine.regime_signal.active_throttle_factor", lambda *a, **k: 0.5
        )
        pm = _pm()
        _hold(pm, "X", 1_000.0)  # Ziel 5 % x 0,5 = 2,5 %
        assert pm.topup_gap_value(_opp()) == pytest.approx(1_500.0)

    def test_conviction_mode_uses_the_conviction_target(self, monkeypatch):
        import config as _c

        monkeypatch.setattr(_c, "CLEAN_WEIGHT_SIZING", "off", raising=False)
        pm = _pm()
        _hold(pm, "X", 2_000.0)
        expected = pm._conviction_target_pct(0.8) / 100.0 * CAPITAL - 2_000.0
        assert pm.topup_gap_value(_opp(conf=0.8)) == pytest.approx(expected)


# --------------------------------------------------------------------------- Orderpfad
def _ctx(conv=0.8):
    return SimpleNamespace(
        conviction_score=conv,
        forecast_vol=None,
        skew_percentile=None,
        vote_coverage=None,
    )


class TestOrderPathGapCap:
    def test_held_name_is_capped_at_the_gap(self):
        from core.engine.order_executor import _topup_gap_capped_qty

        pm = _pm()
        _hold(pm, "X", 2_700.0)
        trace: dict = {}
        qty = _topup_gap_capped_qty(pm, "X", 50.0, 100.0, _ctx(), trace)
        assert qty == pytest.approx(23.0)  # 2.300 $ statt 5.000 $
        assert trace.get("binding_limit") == "topup_gap"

    def test_new_name_is_untouched(self):
        from core.engine.order_executor import _topup_gap_capped_qty

        assert _topup_gap_capped_qty(
            _pm(), "X", 50.0, 100.0, _ctx(), {}
        ) == pytest.approx(50.0)

    def test_order_below_the_gap_is_untouched(self):
        from core.engine.order_executor import _topup_gap_capped_qty

        pm = _pm()
        _hold(pm, "X", 2_700.0)
        trace: dict = {}
        assert _topup_gap_capped_qty(pm, "X", 10.0, 100.0, _ctx(), trace) == 10.0
        assert trace.get("binding_limit") != "topup_gap"

    def test_unconfirmed_holdings_buy_nothing(self):
        from core.engine.order_executor import _topup_gap_capped_qty

        pm = _pm()
        _hold(pm, "X", 2_700.0)
        pm._last_refresh_ok = False
        assert _topup_gap_capped_qty(pm, "X", 50.0, 100.0, _ctx(), {}) == 0.0

    def test_mock_or_missing_manager_is_untouched(self):
        from core.engine.order_executor import _topup_gap_capped_qty

        assert _topup_gap_capped_qty(None, "X", 50.0, 100.0, _ctx(), {}) == 50.0
        assert _topup_gap_capped_qty(MagicMock(), "X", 50.0, 100.0, _ctx(), {}) == 50.0

    def test_broken_trace_sink_logs_a_warning_and_still_caps(self, caplog):
        """Review #3981 POLICY-01: a failing audit sink is never swallowed silently."""
        from core.engine.order_executor import _topup_gap_capped_qty

        class _BrokenSink(dict):
            def __setitem__(self, key, value):
                raise RuntimeError("sink down")

        pm = _pm()
        _hold(pm, "X", 2_700.0)
        with caplog.at_level("WARNING"):
            qty = _topup_gap_capped_qty(pm, "X", 50.0, 100.0, _ctx(), _BrokenSink())
        assert qty == pytest.approx(23.0)
        assert any(
            "topup_gap" in r.getMessage() and r.levelname == "WARNING"
            for r in caplog.records
        )


def _gate():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _geld_gate

    return _geld_gate


class TestIntegrationDefaultPath:
    """Beide Orderpfade bis zur abgesendeten Menge (Geld-Gate-Harness, #3392)."""

    def _szenario(self):
        gate = _gate()

        return next(s for s in gate.SZENARIEN if s.name == "nachkauf_bis_zum_ziel")

    def test_tenant_path_buys_only_the_gap(self):
        gate = _gate()

        ergebnis = gate.fahre(self._szenario())
        assert ergebnis["notional"] == pytest.approx(2_300.0)
        assert ergebnis["deckel"] == "topup_gap"

    def test_desktop_path_buys_the_same(self):
        gate = _gate()

        sz = self._szenario()
        desktop = gate.fahre_desktop(sz, vorgabe=0.0)
        assert desktop["notional"] == pytest.approx(gate.fahre(sz)["notional"])


# --------------------------------------------------------------------------- Broker-Stop
class TestBrokerStopAfterTopup:
    """Plan #3963 §7: deckt der Broker-Stop nach einem Nachkauf die Gesamtmenge?

    Befund beim Bau dieses PRs: nein. Der Planer nimmt ``qty_available``, das die vom
    EIGENEN liegenden Stop gebundenen Stuecke ausschliesst, und pendelt zwischen alter und
    neuer Teilmenge (Issue #3976). Behoben mit #3976: die Stuecke des eigenen Stops zaehlen
    zur Schutzmenge. Der alte Stop traegt deshalb den Schluessel, den die Pflege vergibt.
    """

    def test_stop_covers_the_whole_position_after_a_topup(self):
        from datetime import date

        from core.broker_stops import plan_broker_stops

        alter_stop = SimpleNamespace(
            id="s1",
            symbol="X",
            time_in_force="gtc",
            qty=27.0,
            stop_price=98.5,
            client_order_id="stop-0-stop-X",
        )
        nach_nachkauf = SimpleNamespace(
            symbol="X", qty=50.0, qty_available=23.0, avg_entry_price=104.6
        )
        plan = plan_broker_stops(
            [nach_nachkauf],
            stop_loss_pct=1.5,
            existing_stops=[alter_stop],
            session_date=date(2026, 10, 2),
        )
        gelegt = sum(s.qty for s in plan.to_place)
        bleibt = 0.0 if "s1" in plan.to_cancel else alter_stop.qty
        assert gelegt + bleibt == pytest.approx(50.0)
