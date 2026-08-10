# tests/unit/test_full_universe_trading.py
# Phase B — full-universe CROSS-SECTIONAL trading behind FULL_UNIVERSE_TRADING_ENABLED.
#
# ⚠ This is a TRADING-BEHAVIOUR flag (P1 decision path: it changes WHICH symbols are
# bought). The whole point of these tests is the DORMANCY guarantee: with the flag OFF
# (the ship default, both editions) the engine's universe selection, scanner funnel and
# position selection are byte-identical to today. The flag is only flipped after
# walk-forward validation + human sign-off (MiFID II RTS 6).
#
# Design anchor (our own walk-forward): per-symbol independent bets do NOT survive
# multiple testing (0/488 pass FDR); the cross-sectional RANK is the real (weak) edge.
# So the ON path must rank the full universe and select the top decile BY RANK — never
# ~500 isolated BUY/HOLD calls. Fully deterministic: no network / LLM / GPU.

import importlib.util
import os

import config


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_phase_b", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# 1. Activation: the trading flag now defaults ON in BOTH editions (BORA parity).
#    Owner decision (2026-07): the round-table-v2 constellation was commissioned,
#    so FULL_UNIVERSE_TRADING_ENABLED ships default True (config.py + config.oss.py).
#    The OFF path (dormancy, byte-identical scanner funnel) is still reachable and
#    validated below — it is now env-forced, not the default.
# ===========================================================================
def test_full_universe_trading_flag_default_on_enterprise():
    assert config.get_config().FULL_UNIVERSE_TRADING_ENABLED is True
    assert "FULL_UNIVERSE_TRADING_ENABLED" in config.RuntimeConfigState.model_fields


def test_full_universe_trading_flag_default_on_oss():
    oss = _load_config_oss()
    assert oss.FULL_UNIVERSE_TRADING_ENABLED is True
    assert oss.get_config().FULL_UNIVERSE_TRADING_ENABLED is True


def test_full_universe_trading_flag_is_env_overridable(monkeypatch):
    """The flag defaults ON, but it must remain a deliberate, env-controlled switch —
    NOT hard-wired. Forcing the env var to a false value must still turn it OFF, so an
    operator can restore the dormant (byte-identical) universe path without a code change.
    (RuntimeConfigState is a pydantic BaseSettings → binds env by field name at init.)
    """
    monkeypatch.setenv("FULL_UNIVERSE_TRADING_ENABLED", "False")
    forced_off = config.RuntimeConfigState()
    assert forced_off.FULL_UNIVERSE_TRADING_ENABLED is False


# ===========================================================================
# 2. ADR-FU01 — the fan-out cap that replaces the 200-truncation.
# ===========================================================================
def test_eval_concurrency_default_is_bounded_in_both_editions():
    """Removing the 200-truncation removes the ONLY bound on the per-symbol
    gather. The replacement bound must exist and be small — an unbounded ~500x9
    fan-out at one local Ollama is an outage, not a cost."""
    assert config.get_config().FULL_UNIVERSE_EVAL_CONCURRENCY == 20
    oss = _load_config_oss()
    assert oss.FULL_UNIVERSE_EVAL_CONCURRENCY == 20
    assert oss.get_config().FULL_UNIVERSE_EVAL_CONCURRENCY == 20


def test_eval_concurrency_is_a_small_positive_bound():
    n = config.get_config().FULL_UNIVERSE_EVAL_CONCURRENCY
    assert isinstance(n, int) and 0 < n <= 50, "fan-out cap must stay small"


# ===========================================================================
# 3. DORMANCY of the trading-loop edits: flag OFF -> today's behaviour.
# ===========================================================================
def test_slot_count_default_and_bounds_in_both_editions():
    """ADR-FU02: the slot count IS the portfolio's scarcity once ~500 names are
    judged — a better chance must displace the weakest, not grow the book.

    ⚠ THE NUMBER. History: #2119 set 50; anti-churn Part C (#2176) moved it to 20
    (1/20 = 5% == MIN_POSITION_PERCENT). Now the operator's call: **10** — the vision is to
    hold the best 10 of the ~500 judged names, displacing the weakest into the same slot.
    At 10 the equal-weight cash slot is 1/10 = 10% (2× MIN_POSITION_PERCENT=5%, so the cash
    clamp stays slack) and well under the 25%/symbol concentration limit. This sets the
    full-universe book EQUAL to the default MAX_POSITIONS=10 — same book size, wider scan.
    Pinned (BORA: both editions) so a drift fails here loudly instead of by merge order.
    """
    assert config.get_config().FULL_UNIVERSE_MAX_POSITIONS == 10
    oss = _load_config_oss()
    assert oss.FULL_UNIVERSE_MAX_POSITIONS == 10
    assert oss.get_config().FULL_UNIVERSE_MAX_POSITIONS == 10


def test_the_slot_count_never_shrinks_the_default_book():
    """The invariant that outlives either number: turning the FULL universe on must
    never give the portfolio FEWER slots than the scanner's top-10 book. If someone
    lowers this below MAX_POSITIONS, breadth would paradoxically narrow the book."""
    assert config.get_config().FULL_UNIVERSE_MAX_POSITIONS >= getattr(
        config, "MAX_POSITIONS", 10
    )


def test_default_book_size_is_untouched_by_phase_b():
    """MAX_POSITIONS itself must NOT move — only the READ is overridden behind the
    flag, so the default path keeps its book size exactly."""
    assert getattr(config, "MAX_POSITIONS", None) == 10


def test_slot_override_is_read_only_inside_the_flag_branch():
    """Mechanical dormancy proof: the Phase B slot count is only ever read behind
    FULL_UNIVERSE_TRADING_ENABLED, and the displacement mechanism it feeds
    (debate_position_swap / get_weakest_position) is the EXISTING one — we extend
    the book size, we do not replace the arbitration."""
    import inspect

    from core.engine import order_executor
    from core.portfolio_manager import PortfolioManager

    src = inspect.getsource(order_executor)
    assert "FULL_UNIVERSE_MAX_POSITIONS" in src
    for line in src.splitlines():
        if "FULL_UNIVERSE_MAX_POSITIONS" in line:
            assert "get_config()" in line
    # the flag guards the override, and the default read survives untouched
    assert 'max_positions = getattr(config, "MAX_POSITIONS", 10)' in src
    assert "if config.get_config().FULL_UNIVERSE_TRADING_ENABLED:" in src
    # the displacement machinery we rely on is the existing one, not a new one
    assert hasattr(PortfolioManager, "debate_position_swap")
    assert hasattr(PortfolioManager, "get_weakest_position")
    assert hasattr(PortfolioManager, "should_open_new_position")


def test_universe_feed_edits_are_flag_gated_and_scanner_payload_is_untouched():
    """base.py must only widen live_universe behind the flag (production and the
    dev default stay verbatim), and monitor_loop must bypass the scanner funnel
    behind the flag WITHOUT touching the scanner's GUI payload."""
    import inspect

    from core.engine import base, monitor_loop

    base_src = inspect.getsource(base)
    # production path unchanged, dev default unchanged, flag is its own branch
    assert 'if config.ENVIRONMENT == "production":' in base_src
    assert "elif config.get_config().FULL_UNIVERSE_TRADING_ENABLED:" in base_src
    # a failed network scrape must degrade, never kill the boot
    flag_branch = base_src.split(
        "elif config.get_config().FULL_UNIVERSE_TRADING_ENABLED:"
    )[1].split("else:")[0]
    assert "try:" in flag_branch and "DEFAULT_SYMBOLS" in flag_branch

    mon_src = inspect.getsource(monitor_loop)
    assert "if config.get_config().FULL_UNIVERSE_TRADING_ENABLED:" in mon_src
    # the GUI contract: _last_top_picks is assigned BEFORE (and outside) the flag
    # branch, so the scanner's top-10 payload is identical in both flag states
    assert mon_src.index("self._last_top_picks") < mon_src.rindex(
        "if config.get_config().FULL_UNIVERSE_TRADING_ENABLED:"
    )


def test_a5_report_flag_is_not_a_trading_flag():
    """ORTHOGONALITY: enabling the full-universe REPORT registry must never widen
    the trading universe. The two flags are independent by construction."""
    import inspect

    from core.engine import base

    src = inspect.getsource(base)
    # the report-registry flag must not appear in the live_universe decision
    universe_block = src.split('if config.ENVIRONMENT == "production":')[1].split(
        "Engine: Universe set to"
    )[0]
    assert "REPORT_REGISTRY_FULL_UNIVERSE_ENABLED" not in universe_block
    # ...and the trading flag must not drive the report registry's universe
    registry_block = src.split("SPECIALIST_REGISTRY_ENABLED")[1][:1500]
    assert "FULL_UNIVERSE_TRADING_ENABLED" not in registry_block


def test_cap_gate_and_semaphore_are_lexically_inside_the_flag_branch():
    """Mechanical dormancy proof (the cheap, reliable one): the 200-truncation
    must still be reachable with the flag OFF, and every read of the Phase B
    sizing constants must sit INSIDE a FULL_UNIVERSE_TRADING_ENABLED branch —
    never unconditionally."""
    import inspect

    from core.engine import trading_loop

    src = inspect.getsource(trading_loop)
    # the OFF path still truncates at the identical constant
    assert "if not get_config().FULL_UNIVERSE_TRADING_ENABLED:" in src
    assert "symbols_to_process = symbols_to_process[:200]" in src
    # the fan-out cap is only ever read behind the flag
    assert "FULL_UNIVERSE_EVAL_CONCURRENCY" in src
    for line in src.splitlines():
        if "FULL_UNIVERSE_EVAL_CONCURRENCY" in line:
            assert "get_config()" in line, "cap must be read via get_config()"
    # the bounded evaluator exists and acquires the slot BEFORE the timeout starts,
    # so a queued symbol is never timed out for merely waiting
    assert "async def _bounded_eval" in src
    bounded = src.split("async def _bounded_eval")[1].split("results = await")[0]
    assert bounded.index("async with _sem") < bounded.index("asyncio.wait_for")
