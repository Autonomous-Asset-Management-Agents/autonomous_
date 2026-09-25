# tests/unit/test_consensus_weight_seam.py
"""#2815 (TRD-8 T1) — every consensus knob is injectable per PROCESS, and unset means
byte-identical to today.

Five of seven knobs were hard-coded class attributes (agents.py) or module constants
(consensus.py); only SPECIALIST_ALPHA_WEIGHT and RL_CONFIDENCE_WEIGHT had the seam. The
sweep (T2) varies knobs via a fresh interpreter per combination, so resolution happens
ONCE at import — deliberately, per the #1346 comment (monkeypatch race between
default_weight and max_weight reading different env).

PLAN DEVIATION, recorded: the approved plan chose raw env reads in agents.py. Grounding
found CODING_POLICY §2.10 and the in-file precedent (*"The os.environ read lives in
config.py/config.oss.py … not in the finance-core"*) — so the knobs are config fields in
BOTH editions, read via get_config() at import, exactly like SPECIALIST_ALPHA_WEIGHT.
Same goal, compliant plumbing.

Override tests run in a SUBPROCESS with a clean interpreter: import-time resolution means
in-process reload games would fight the global-state canary (#2793) — a child process is
both correct and isolation-safe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

# (name, config default, agents/consensus attribute path)
GOLDEN = [
    (
        "DRAWDOWN_GUARD_WEIGHT",
        0.60,
        "core.round_table.agents:DrawdownGuardAgent.default_weight",
    ),
    (
        "REGIME_DETECTION_WEIGHT",
        0.50,
        "core.round_table.agents:RegimeDetectionAgent.default_weight",
    ),
    (
        "MOMENTUM_AGENT_WEIGHT",
        0.45,
        "core.round_table.agents:MomentumAgent.default_weight",
    ),
    (
        "VIX_RISK_WEIGHT",
        0.45,
        "core.round_table.agents:VIXAwareRiskAgent.default_weight",
    ),
    (
        "LSTM_SIGNAL_WEIGHT",
        0.40,
        "core.round_table.agents:LSTMSignalAgent.default_weight",
    ),
    ("SIGNAL_BUY_THRESHOLD", 0.65, "core.round_table.consensus:SIGNAL_BUY_THRESHOLD"),
    ("SIGNAL_SELL_THRESHOLD", 0.35, "core.round_table.consensus:SIGNAL_SELL_THRESHOLD"),
]

_PROBE = r"""
import sys
sys.path.insert(0, {root!r})
mod_name, attr = {path!r}.split(":")
import importlib
mod = importlib.import_module(mod_name)
obj = mod
for part in attr.split("."):
    obj = getattr(obj, part)
print(repr(float(obj)))
"""


def _resolve_in_subprocess(path: str, env_extra: dict) -> float:
    """Import the module in a FRESH interpreter (import-time resolution!) and read attr."""
    import os

    env = dict(os.environ)
    # strip any of the seven from the inherited env so 'unset' really is unset
    for name, _, _ in GOLDEN:
        env.pop(name, None)
    env.update(env_extra)
    code = _PROBE.format(root=str(_AI_BOT), path=path)
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(_AI_BOT),
        timeout=180,
    )
    assert out.returncode == 0, f"probe failed for {path}:\n{out.stderr[-800:]}"
    return float(out.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# 1. The load-bearing invariant: unset ⇒ byte-identical to today
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,default,path", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_unset_env_yields_todays_exact_value(name, default, path):
    got = _resolve_in_subprocess(path, {})
    assert got == default, f"{name}: default drifted — {got} != {default}"


# ---------------------------------------------------------------------------
# 2. The seam: an override reaches the class / constant
# ---------------------------------------------------------------------------


def test_weight_override_reaches_the_agent_class():
    got = _resolve_in_subprocess(
        "core.round_table.agents:LSTMSignalAgent.default_weight",
        {"LSTM_SIGNAL_WEIGHT": "0.30"},
    )
    assert got == 0.30


def test_threshold_override_reaches_the_constant():
    got = _resolve_in_subprocess(
        "core.round_table.consensus:SIGNAL_BUY_THRESHOLD",
        {"SIGNAL_BUY_THRESHOLD": "0.55"},
    )
    assert got == 0.55


def test_threshold_override_changes_classification():
    """The whole point, proven on a REAL consumer: `record_consensus_outcome` buckets a
    0.60 score as no_trade today (buy > 0.65) and as buy once the threshold is 0.55."""
    import os

    code = (
        f"import sys; sys.path.insert(0, {str(_AI_BOT)!r})\n"
        "from core.round_table.consensus import record_consensus_outcome, get_decision_counters\n"
        "record_consensus_outcome(0.60, True)\n"
        "c = get_decision_counters()['consensus_outcomes']\n"
        "print('buy' if c['buy'] == 1 else 'no_trade')\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in [g[0] for g in GOLDEN]}
    base = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(_AI_BOT),
        timeout=180,
    )
    lowered = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env={**env, "SIGNAL_BUY_THRESHOLD": "0.55"},
        cwd=str(_AI_BOT),
        timeout=180,
    )
    assert base.returncode == 0 and lowered.returncode == 0, (
        base.stderr,
        lowered.stderr,
    )
    assert base.stdout.strip().splitlines()[-1] != "buy"
    assert lowered.stdout.strip().splitlines()[-1] == "buy"


# ---------------------------------------------------------------------------
# 3. Safety: garbage and inverted values fall back, never invert the consensus
# ---------------------------------------------------------------------------


def test_garbage_weight_fails_closed_and_names_the_variable():
    """PLAN DEVIATION #2, measured: pydantic BaseSettings validates every numeric env at
    boot — garbage crashes get_config() LOUDLY, for these knobs exactly as for every
    existing numeric field (e.g. RL_CONFIDENCE_WEIGHT=abc). That fail-closed posture is
    deliberate platform behaviour and STRONGER than the plan's silent-fallback promise:
    a live engine must not trade on a half-understood config. The sweep orchestrator
    (#2816) validates spec values before spawning, so a sweep typo dies there, visibly.
    This test pins the failure mode: nonzero exit and the variable named in the error.
    """
    import os

    env = dict(os.environ)
    for name, _, _ in GOLDEN:
        env.pop(name, None)
    env["MOMENTUM_AGENT_WEIGHT"] = "abc"
    code = _PROBE.format(
        root=str(_AI_BOT), path="core.round_table.agents:MomentumAgent.default_weight"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(_AI_BOT),
        timeout=180,
    )
    assert (
        out.returncode != 0
    ), "garbage must fail closed, not silently trade on defaults"
    assert "MOMENTUM_AGENT_WEIGHT" in (out.stderr + out.stdout)


def test_out_of_range_weight_is_clamped():
    got = _resolve_in_subprocess(
        "core.round_table.agents:DrawdownGuardAgent.default_weight",
        {"DRAWDOWN_GUARD_WEIGHT": "7"},
    )
    assert got == 1.0  # clamped to [0, 1], never a 7x mega-vote


def test_inverted_thresholds_are_refused():
    """buy 0.3 / sell 0.6 would invert the consensus — both fall back to defaults."""
    buy = _resolve_in_subprocess(
        "core.round_table.consensus:SIGNAL_BUY_THRESHOLD",
        {"SIGNAL_BUY_THRESHOLD": "0.3", "SIGNAL_SELL_THRESHOLD": "0.6"},
    )
    sell = _resolve_in_subprocess(
        "core.round_table.consensus:SIGNAL_SELL_THRESHOLD",
        {"SIGNAL_BUY_THRESHOLD": "0.3", "SIGNAL_SELL_THRESHOLD": "0.6"},
    )
    assert (buy, sell) == (0.65, 0.35)


# ---------------------------------------------------------------------------
# 4. BORA parity: both editions define all seven with equal defaults
# ---------------------------------------------------------------------------


def test_both_editions_define_all_seven_with_equal_defaults():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "config_oss_seam_check", str(_AI_BOT / "config.oss.py")
    )
    oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oss)
    import config as ent

    cfg = ent.get_config()
    for name, default, _ in GOLDEN:
        assert float(getattr(cfg, name)) == default, f"config.py {name}"
        assert float(getattr(oss, name)) == default, f"config.oss.py {name}"
