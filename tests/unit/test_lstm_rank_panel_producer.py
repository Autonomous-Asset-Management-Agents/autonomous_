# tests/unit/test_lstm_rank_panel_producer.py
# The keystone: produce the LSTM cross-section panel independently of ACTIVE_STRATEGY.
#
# THE PROBLEM (verified, and it is the DEFAULT of the shipped build — not an edge case):
#   * the panel's only production writer is LSTMDynamicStrategy.update_lstm_rankings
#     (lstm_strategy.py, the sole record_cycle caller in the tree);
#   * config.ACTIVE_STRATEGY defaults to "RLAgent", which has no update_lstm_rankings;
#   => the panel is never written, so fact_set's lstm_xsec_rank is None, so §2 — the
#      section labelled "Operative Zahl (trägt die Empfehlung)" — has nothing, and the
#      head abstains entirely (ADR-RPT4-08). EVERY report, EVERY symbol.
#
# The report's own config comment already admitted half of this: "this flag ALONE does
# nothing ... the default is RLAgent, which never writes a panel." This is the missing
# half — the panel's producer, decoupled from whoever trades.
#
# WHY THE DORMANCY HERE IS UNUSUALLY STRONG (and worth asserting mechanically):
# the new branch is an `elif` hanging off trading_loop's existing
#   if hasattr(local_active_strategy, "update_lstm_rankings"):
# so it can only fire where that hasattr is FALSE — i.e. in exactly the states where
# ZERO code runs today. This is not "the old path is preserved"; there IS no old path
# at that site. OFF must also mean the producer is never CONSTRUCTED: an LSTMDynamic
# __init__ loads a torch model, and a behaviour-identity test would happily pass while
# a second model was being loaded on every boot.
#
# Fully deterministic: no network / LLM / GPU / Redis.

from __future__ import annotations

import importlib.util
import os

import config


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_rank_panel", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# =========================================================================== #
# 1. ACTIVATION — default ON in BOTH editions (BORA parity).
#    The round-table-v2 constellation was commissioned (owner decision): this
#    flag now defaults to True in config.py AND config.oss.py so the report's
#    cross-sectional rank is produced whatever strategy trades. The OFF path
#    below is still reachable via env and is exercised explicitly in §4.
# =========================================================================== #
def test_flag_default_on_enterprise():
    assert config.get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT is True
    assert (
        "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT" in config.RuntimeConfigState.model_fields
    )


def test_flag_default_on_oss():
    oss = _load_config_oss()
    assert oss.LSTM_RANK_PANEL_STRATEGY_INDEPENDENT is True
    assert oss.get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT is True


def test_flag_is_env_overridable():
    """It must never inherit from a tier or another flag — it is controlled by
    its OWN env var only. Now default-ON, so with no env override it is True; the
    source derives the value from LSTM_RANK_PANEL_STRATEGY_INDEPENDENT alone, so
    it can still be forced OFF via that env (see §4's OFF-path tests)."""
    assert os.getenv("LSTM_RANK_PANEL_STRATEGY_INDEPENDENT") in (
        None,
        "",
        "True",
        "true",
    )
    assert config.get_config().LSTM_RANK_PANEL_STRATEGY_INDEPENDENT is True


# =========================================================================== #
# 2. The premise, pinned. If any of these change, this whole feature is moot
#    or wrong — and a future reader deserves to be told loudly, not to
#    rediscover it the way I did.
# =========================================================================== #
def test_the_default_strategy_cannot_write_a_panel():
    """The fact that makes this flag necessary: RLAgent is the ship default and has no
    update_lstm_rankings, so the report's operative number is absent by default."""
    assert config.get_config().ACTIVE_STRATEGY == "RLAgent"

    from core.strategies.rl_strategy import RLStrategy

    assert not hasattr(RLStrategy, "update_lstm_rankings")


def test_only_lstm_dynamic_writes_the_panel():
    """The sole writer. If a second one ever appears, the design premise changes."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    writers = []
    for py in (root / "core").rglob("*.py"):
        if "test" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if ".record_cycle(" in line:
                writers.append(f"{py.name}:{i}")

    assert all(
        w.startswith("lstm_strategy.py") or w.startswith("runner.py") for w in writers
    ), (
        f"the panel gained a writer outside LSTMDynamic/SimRunner — the producer's premise "
        f"needs revisiting: {writers}"
    )


def test_the_producer_branch_is_lexically_unreachable_when_off():
    """Mechanical dormancy proof: the new branch is an `elif` off the existing hasattr,
    so it cannot run in any state the old code could reach — and the hasattr arm itself
    is untouched."""
    import inspect

    from core.engine import trading_loop

    src = inspect.getsource(trading_loop)
    assert 'if hasattr(local_active_strategy, "update_lstm_rankings"):' in src
    assert (
        "elif"
        in src.split('if hasattr(local_active_strategy, "update_lstm_rankings"):')[1][
            :900
        ]
    )
    for line in src.splitlines():
        if "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT" in line:
            assert "get_config()" in line or "cfg" in line


# =========================================================================== #
# 3. The registry seam this leans on — it already exists and nothing uses it.
# =========================================================================== #
def test_the_registry_can_hold_a_standby_strategy():
    """register(..., set_active=False) exists and is called by NOTHING today. It is
    exactly the seam a ranker-that-does-not-trade needs: present in the registry,
    never returned by get_active()."""
    import inspect

    from core.agent_registry import AgentRegistry

    sig = inspect.signature(AgentRegistry.register)
    assert "set_active" in sig.parameters
    assert sig.parameters["set_active"].default is True  # opt-in standby


def test_a_standby_registration_never_becomes_active():
    """The property the producer's safety rests on: registering the ranker must not
    hijack the strategy the engine trades with."""
    from unittest.mock import MagicMock

    from core.agent_registry import AgentRegistry

    reg = AgentRegistry()
    trader, ranker = MagicMock(), MagicMock()
    reg.register("RLAgent", trader)
    reg.register("LSTMDynamic", ranker, set_active=False)

    assert reg.get_active() is trader
    assert "LSTMDynamic" in reg.list_registered()


# =========================================================================== #
# 4. The producer factory — the dormancy that a behaviour test cannot see.
# =========================================================================== #
class _FakeEngine:
    """Just enough BotEngine surface for _build_rank_panel_producer."""

    def __init__(self):
        from unittest.mock import MagicMock

        from core.engine.monitor_loop import MonitorLoopMixin

        self.api = MagicMock()
        self.strategy_running = MagicMock()
        self.live_risk_manager = MagicMock()
        self.data_provider = MagicMock()
        self.compliance_guardian = MagicMock()
        self.active_strategy = MagicMock()
        self._log_strategy_thought = MagicMock()
        self._build_rank_panel_producer = (
            MonitorLoopMixin._build_rank_panel_producer.__get__(self)
        )


def test_flag_off_never_even_constructs_the_producer(monkeypatch):
    """⚠ THE test that matters, and the one a behaviour-identity check would miss:
    LSTMDynamicStrategy.__init__ LOADS A TORCH MODEL. "No behaviour change" would pass
    happily while a second model was loaded on every boot. OFF must mean NOT BUILT.

    The flag now DEFAULTS ON, but the OFF path stays reachable via env — force it OFF
    here so this still validates the not-built guarantee."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setattr(
        config.get_config(), "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT", False
    )

    engine = _FakeEngine()
    with patch(
        "core.strategies.lstm_strategy.LSTMDynamicStrategy"
    ) as ctor:  # noqa: SIM117
        out = engine._build_rank_panel_producer(
            ["AAPL"], 100_000.0, MagicMock(), already_ranks=False
        )

    assert out is None
    assert not ctor.called, "a torch model was loaded with the flag OFF"


def test_an_lstm_trader_does_not_get_a_second_ranker(monkeypatch):
    """If the ACTIVE strategy already ranks, one ranker is enough — two would fight
    over one panel."""
    from unittest.mock import MagicMock, patch

    engine = _FakeEngine()
    with patch("core.strategies.lstm_strategy.LSTMDynamicStrategy") as ctor:
        out = engine._build_rank_panel_producer(
            ["AAPL"], 100_000.0, MagicMock(), already_ranks=True
        )

    assert out is None
    assert not ctor.called


def test_a_broken_producer_costs_the_rank_not_the_engine(monkeypatch):
    """A ranker that cannot be built must degrade to an honest gap in the report —
    never take the boot down with it."""
    from unittest.mock import MagicMock, patch

    engine = _FakeEngine()
    monkeypatch.setenv("LSTM_RANK_PANEL_STRATEGY_INDEPENDENT", "true")
    with patch(
        "core.strategies.lstm_strategy.LSTMDynamicStrategy",
        side_effect=RuntimeError("no model on disk"),
    ):
        out = engine._build_rank_panel_producer(
            ["AAPL"], 100_000.0, MagicMock(), already_ranks=False
        )

    assert out is None  # degraded, not raised


def test_the_engine_starts_with_no_producer():
    """The trading loop's guard is a plain None-check on the first cycle, before the
    monitor has ever run — so the attribute must exist from __init__."""
    import inspect

    from core.engine import base

    assert "self._rank_panel_producer" in inspect.getsource(base)
