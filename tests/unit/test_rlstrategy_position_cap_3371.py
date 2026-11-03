"""#3371 — the RLStrategy PortfolioManager must honour the operator's full-universe
position cap, not the hard MAX_POSITIONS=10.

Bug: the enforcing PM (RLStrategy.portfolio_manager, which feeds summary.max_positions)
was initialised from the bare `config.MAX_POSITIONS` (=10) and never read
FULL_UNIVERSE_MAX_POSITIONS — so raising the Console position count had no effect
(setup.json saved + injected, just never read on this path). The sibling order_executor
path did it right. Fix: both read the shared `effective_max_positions()` helper.

RLStrategy pulls in torch/RecurrentPPO and is too heavy to instantiate in a unit test,
so this pins (1) the helper's behaviour for exactly this bug, and (2) — anti-drift —
that the PM-init call site uses the helper, not a bare MAX_POSITIONS read.
"""

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from core.risk_manager import effective_max_positions


def _cfg(full_universe, full_max=10, max_pos=10):
    return SimpleNamespace(
        FULL_UNIVERSE_TRADING_ENABLED=full_universe,
        FULL_UNIVERSE_MAX_POSITIONS=full_max,
        MAX_POSITIONS=max_pos,
    )


def test_full_universe_cap_is_the_operator_value_not_ten():
    """The exact #3371 regression: full universe on + operator set 20 → 20, not 10."""
    with patch("config.get_config", return_value=_cfg(True, full_max=20)):
        assert effective_max_positions() == 20


def test_narrow_universe_falls_back_to_max_positions():
    """Full universe off → the fixed MAX_POSITIONS (byte-identical default path)."""
    with patch("config.get_config", return_value=_cfg(False, max_pos=10)):
        assert effective_max_positions() == 10


def test_rlstrategy_pm_init_uses_the_shared_helper_not_bare_max_positions():
    """Anti-drift (wire-the-seams): the RLStrategy PM-init must call
    effective_max_positions() and must NOT re-introduce a bare `from config import
    MAX_POSITIONS` on that path — otherwise the operator's cap is ignored again."""
    import core.strategies.rl_strategy as rl

    src = inspect.getsource(rl)
    assert (
        "effective_max_positions()" in src
    ), "PM-init no longer uses the shared cap helper"
    assert (
        "from config import MAX_POSITIONS" not in src
    ), "bare MAX_POSITIONS import reintroduced on the PM-init path — the #3371 bug"


def test_cap_fallback_is_logged_not_silent():
    """#3372 review (POLICY-01 / §5.6): the cap-read except must WARN before falling
    back, never a silent `pass` — a mispinned cap must stay visible."""
    import core.strategies.rl_strategy as rl

    src = inspect.getsource(rl)
    # The except that guards the effective_max_positions() read logs at WARNING.
    guard = src[src.index("max_positions = effective_max_positions()") :]
    guard = guard[: guard.index("PortfolioManager(")]
    assert "except (ImportError, ValueError, TypeError)" in guard
    assert "logging.warning(" in guard, "cap-read fallback must log at WARNING (§5.6)"
    assert "\n                pass" not in guard, "silent pass in the cap-read except"
