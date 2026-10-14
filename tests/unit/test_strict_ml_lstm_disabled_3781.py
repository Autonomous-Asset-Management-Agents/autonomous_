"""#3781 — Strict-ML-Gate: ein vom Operator deaktiviertes LSTM legt nicht mehr jeden Kauf still.

Plan: docs/3781-strict-ml-gate-lstm-disabled/implementation_plan.md (Option A). Das Gate liest den
Verzicht aus dem Vote (``abstain_reason == DISABLED``): bewusst ausgeschaltet ⇒ kein Block, einmalige
WARNING je Sitzung; eingeschaltet, aber ohne gültige Stimme ⇒ Block wie bisher (fail-closed).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.vc1]

from core.contracts.signal_candidate import AbstainReason, SignalCandidate  # noqa: E402
from core.round_table.base_agent import VoteResult  # noqa: E402
from core.round_table.gatekeeper import GatekeeperDecision  # noqa: E402

_MISSING = "Missing core ML votes (LSTM/RL failed or excluded)"


@pytest.fixture(autouse=True)
def _reset_latches():
    import core.round_table.gate_stufen as gate_stufen

    gate_stufen._STRICT_ML_RL_WAIVED_WARNED = False
    gate_stufen._STRICT_ML_LSTM_DISABLED_WARNED = False
    yield
    gate_stufen._STRICT_ML_RL_WAIVED_WARNED = False
    gate_stufen._STRICT_ML_LSTM_DISABLED_WARNED = False


class _Cfg(SimpleNamespace):
    def __getattr__(self, name: str):
        return False


def _cfg(requires_rl: bool) -> _Cfg:
    return _Cfg(
        GATEKEEPER_STRICT_ML_REQUIRES_RL=requires_rl, GATEKEEPER_REQUIRE_CONTEXT=False
    )


class TestGateEinheit:
    def test_lstm_deaktiviert_gate_blockiert_nicht(self, caplog):
        from core.round_table import runner

        with caplog.at_level(logging.WARNING), patch(
            "config.get_config", return_value=_cfg(requires_rl=False)
        ):
            assert (
                runner._strict_ml_gate_blocks(False, False, lstm_disabled=True) is False
            )
        assert any(
            "LSTM" in r.message and "disabled" in r.message for r in caplog.records
        )

    def test_lstm_an_ohne_gewicht_blockiert(self):
        from core.round_table import runner

        with patch("config.get_config", return_value=_cfg(requires_rl=False)):
            assert (
                runner._strict_ml_gate_blocks(False, False, lstm_disabled=False) is True
            )

    def test_lstm_deaktiviert_warning_einmal_je_sitzung(self, caplog):
        from core.round_table import runner

        with caplog.at_level(logging.WARNING), patch(
            "config.get_config", return_value=_cfg(requires_rl=False)
        ):
            for _ in range(5):
                runner._strict_ml_gate_blocks(False, False, lstm_disabled=True)
        hits = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING
            and "LSTM" in r.message
            and "disabled" in r.message
        ]
        assert len(hits) == 1

    def test_requires_rl_true_und_lstm_aus_blockiert(self):
        from core.round_table import runner

        with patch("config.get_config", return_value=_cfg(requires_rl=True)):
            assert (
                runner._strict_ml_gate_blocks(False, False, lstm_disabled=True) is True
            )

    def test_signatur_ohne_neuen_parameter_byte_identisch(self):
        from core.round_table import runner

        with patch("config.get_config", return_value=_cfg(requires_rl=False)):
            assert runner._strict_ml_gate_blocks(True, False) is False
            assert runner._strict_ml_gate_blocks(False, False) is True


# --------------------------------------------------------------------------- #
# Durchgehend über run_round_table (Übergabe Vote → Gate → Entscheidung)
# --------------------------------------------------------------------------- #
def _mk_state(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1e3,
        },
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _wire(monkeypatch, symbol: str, lstm_vote, momentum_score: float = 0.9):
    from core.round_table import runner

    lstm = object.__new__(runner.LSTMSignalAgent)
    lstm.vote = AsyncMock(return_value=lstm_vote)
    rl = object.__new__(runner.RLConfidenceAgent)
    rl.vote = AsyncMock(
        return_value=VoteResult(
            agent_name="RLConfidenceAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,
            reasoning="rl",
        )
    )
    consensus = MagicMock()
    consensus.check_distribution.return_value = (True, "ok")
    consensus.aggregate.return_value = 0.75
    gatekeeper = MagicMock()
    gatekeeper.check = AsyncMock(
        return_value=GatekeeperDecision(
            approved=True, reason="AllChecksPassed", symbol=symbol
        )
    )
    senate = MagicMock()
    senate.log_session = AsyncMock()
    monkeypatch.setattr(runner, "_consensus_engine", consensus)
    monkeypatch.setattr(runner, "_gatekeeper", gatekeeper)
    monkeypatch.setattr(runner, "_senate", senate)
    monkeypatch.setattr(runner, "_active_agents", [lstm, rl])
    cfg = _cfg(requires_rl=False)
    import sys

    import config
    import settings

    monkeypatch.setattr(config, "get_config", lambda: cfg)
    monkeypatch.setattr(settings, "get_config", lambda: cfg)
    for name, mod in list(sys.modules.items()):
        if (
            name in ("config", "settings")
            or name.endswith(".config")
            or name.endswith(".settings")
        ) and mod is not None:
            if not name.startswith("pydantic") and hasattr(mod, "get_config"):
                monkeypatch.setattr(mod, "get_config", lambda: cfg)
    return runner, senate


class TestRoundTableOhneLstm:
    @pytest.mark.anyio
    async def test_lstm_deaktiviert_kauf_genehmigt(self, monkeypatch):
        symbol = "MRVL"
        disabled = SignalCandidate(
            agent_name="LSTMSignalAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.DISABLED,
            reasoning="disabled",
        )
        runner, senate = _wire(monkeypatch, symbol, disabled)
        with patch("core.decision_capture.capture.attach_round_table_capture_fields"):
            out = await runner.run_round_table(_mk_state(symbol))
        assert out.get("error") is None
        session = senate.log_session.call_args.args[0]
        assert session.gatekeeper_approved is True
        assert _MISSING not in session.gatekeeper_reason
        assert "LSTM disabled" in session.gatekeeper_reason

    @pytest.mark.anyio
    async def test_lstm_ausgefallen_bleibt_blockiert(self, monkeypatch):
        symbol = "MRVL"
        failed = VoteResult(
            agent_name="LSTMSignalAgent",
            symbol=symbol,
            score=0.5,
            weight=0.0,
            reasoning="no model",
        )
        runner, senate = _wire(monkeypatch, symbol, failed)
        with patch("core.decision_capture.capture.attach_round_table_capture_fields"):
            out = await runner.run_round_table(_mk_state(symbol))
        assert out.get("error") is None
        session = senate.log_session.call_args.args[0]
        assert session.gatekeeper_approved is False
        assert session.gatekeeper_reason == _MISSING
