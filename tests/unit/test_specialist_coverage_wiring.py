# tests/unit/test_specialist_coverage_wiring.py — TradingLoopMixin._reconcile_specialist_coverage
# (Epic #1998, sub #2630). The method is async and reads flags via config.get_config(); broker I/O
# runs through asyncio.to_thread, so get_all_positions stays a SYNC mock. Driven with asyncio.run.
import asyncio
import types
from unittest.mock import MagicMock


class FakeReg:
    def __init__(self):
        self.added = []
        self.priority = None
        self._symbols = ["AAPL"]

    def add_symbol(self, s):
        self.added.append(s.upper())

    def update_priority(self, syms):
        self.priority = list(syms)


def _engine_with(reg, positions, scores):
    from core.engine.trading_loop import TradingLoopMixin

    eng = MagicMock()
    eng.specialist_registry = reg
    eng._last_round_table_state = scores
    # sync callable — the method invokes it via asyncio.to_thread(...), NOT awaited directly
    eng.api.get_all_positions = MagicMock(
        return_value=[MagicMock(symbol=s) for s in positions]
    )
    eng._reconcile_specialist_coverage = (
        TradingLoopMixin._reconcile_specialist_coverage.__get__(eng)
    )
    return eng


def _cfg(**kw):
    base = dict(
        SPECIALIST_COVERAGE_DYNAMIC=True,
        SPECIALIST_TOP_N_CONVICTION=2,
        SPECIALIST_COVER_POSITIONS=True,
    )
    base.update(kw)
    return lambda: types.SimpleNamespace(**base)


def test_wiring_adds_positions_and_topn(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_config", _cfg())
    reg = FakeReg()
    eng = _engine_with(reg, ["BNY"], [{"symbol": "PCG", "consensus_score": 0.9}])
    asyncio.run(eng._reconcile_specialist_coverage(base_watchlist=["AAPL"]))
    assert "BNY" in reg.priority and "PCG" in reg.priority and "AAPL" in reg.priority
    assert (
        "BNY" in reg.added and "PCG" in reg.added
    )  # new symbols added to the universe


def test_wiring_flag_off_is_noop(monkeypatch):
    import config

    monkeypatch.setattr(config, "get_config", _cfg(SPECIALIST_COVERAGE_DYNAMIC=False))
    reg = FakeReg()
    eng = _engine_with(reg, ["BNY"], [{"symbol": "PCG", "consensus_score": 0.9}])
    asyncio.run(eng._reconcile_specialist_coverage(base_watchlist=["AAPL"]))
    assert reg.priority is None and reg.added == []
