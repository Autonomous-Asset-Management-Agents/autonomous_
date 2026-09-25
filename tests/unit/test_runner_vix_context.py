# tests/unit/test_runner_vix_context.py
"""#2958 — the real vix/regime state channels must reach the DecisionContext.

_score_to_signal (core/round_table/runner.py) built the MiFID DecisionContext
without vix_level/market_regime, so every decisions/decision_outcomes row froze
the Pydantic defaults 20.0/"normal" (cloud_logger.py) and the executor fed that
constant into the ADR-R08 sizing ladder (vix_risk_scaler pinned to 0.9). The
producer side already ships the channels (trading_loop._regime_conditioner_state_keys,
REGIME_CONDITIONER_ENABLED default True) — only the context wiring was missing.

Contract pinned here:
- channels present  -> the REAL values land in the context (audit + sizing)
- channels None/absent -> the 20.0/"normal" defaults stay AND a SignalEvent is
  still emitted (fail-open: a logging gap must never cost a trade signal)
"""

from types import SimpleNamespace

from core.round_table.runner import _score_to_signal


def _vote(score: float = 0.5) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name="TestAgent",
        score=score,
        weight=1.0,
        reasoning="unit-test vote",
        vetoed=False,
    )


def _state(**extra) -> dict:
    return {"symbol": "AAPL", "ohlc": {"close": 100.0}, "ml": None, **extra}


class TestVixRegimeReachContext:
    def test_state_vix_regime_reach_decision_context(self):
        signal = _score_to_signal(
            _state(vix=31.5, regime="High Volatility"), 0.5, [_vote()]
        )
        assert signal is not None, "_score_to_signal must not fail on this state"
        assert signal.decision_context.vix_level == 31.5
        assert signal.decision_context.market_regime == "High Volatility"

    def test_low_vol_value_reaches_context(self):
        # The live defect: engine measured 13.19 but the audit rows froze 20.0.
        signal = _score_to_signal(
            _state(vix=13.19, regime="Low Volatility"), 0.5, [_vote()]
        )
        assert signal is not None
        assert signal.decision_context.vix_level == 13.19
        assert signal.decision_context.market_regime == "Low Volatility"


class TestFailOpenDefaults:
    def test_none_channels_keep_defaults_and_still_emit_signal(self):
        # Conditioner OFF / double outage writes None into the channels.
        signal = _score_to_signal(_state(vix=None, regime=None), 0.5, [_vote()])
        assert signal is not None, "None channels must never cost the signal"
        assert signal.decision_context.vix_level == 20.0
        assert signal.decision_context.market_regime == "normal"

    def test_absent_channels_keep_defaults(self):
        signal = _score_to_signal(_state(), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.vix_level == 20.0
        assert signal.decision_context.market_regime == "normal"

    def test_garbage_vix_does_not_kill_the_signal(self):
        # A non-numeric channel value must fall back, not raise out of the
        # builder (the except-arm would swallow the SignalEvent entirely).
        signal = _score_to_signal(_state(vix="n/a", regime=42), 0.5, [_vote()])
        assert signal is not None
        assert signal.decision_context.vix_level == 20.0
