# flake8: noqa
# rc3 live finding R2: on settlement day the warm benchmark cache appended today's live
# equity (6,143 -> 11,929) WITHOUT its cash-flow, so Net deposits showed +5.91k against a
# +11.9k book for hours (next full rebuild only ran on mode/baseline staleness). A live
# append that moves equity by settlement scale must evict the cache so the next poll
# rebuilds with freshly aligned flows (<=60s of staleness instead of hours).

from __future__ import annotations

from core.engine.perf_metrics import is_settlement_scale_jump


def test_deposit_scale_jump_triggers_invalidation():
    assert is_settlement_scale_jump(6143.68, 11929.70) is True  # +94% — the real case


def test_normal_market_moves_do_not():
    assert is_settlement_scale_jump(6143.68, 6180.00) is False  # +0.6%
    assert is_settlement_scale_jump(6143.68, 6700.00) is False  # +9% big rally day


def test_withdrawal_scale_drop_triggers_too():
    assert is_settlement_scale_jump(11929.70, 6100.00) is True  # −49%


def test_degenerate_inputs_fail_soft():
    assert is_settlement_scale_jump(0.0, 5000.0) is False  # no base -> no signal
    assert is_settlement_scale_jump(None, 5000.0) is False
