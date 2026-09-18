# tests/unit/test_compliance_source_aware.py
# ii-3 (PR-0a-ii, GAP2): source-aware check_trade (EU AI Act Art. 14).
#
# A human-approved HITL order bypasses the AUTONOMOUS daily-trades cap — a human has
# authorised this specific capital decision — but still passes every other Iron-Dome check
# (check_order / max_order_value etc. are separate methods, unaffected). Dormant: the only
# caller that passes source="human_approved" arrives in PR-0a-ii-5 (the drain executor); with
# the default source="ai" all 4 existing callers + their tests are byte-identical.
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from core.compliance import ComplianceGuardian  # noqa: E402

_ORDER = {"symbol": "AAPL", "action": "BUY", "qty": 1, "price": 100.0}


def _guardian(daily_trades: int, max_daily_trades: int = 10) -> ComplianceGuardian:
    with patch("core.compliance.get_cloud_logger", return_value=MagicMock()):
        g = ComplianceGuardian()
    g.max_daily_trades = max_daily_trades
    g.daily_trades = daily_trades
    return g


def test_autonomous_default_is_capped():
    g = _guardian(daily_trades=10)  # at the cap
    assert g.check_trade(_ORDER) is False
    assert g.check_trade(_ORDER, source="ai") is False


def test_human_approved_bypasses_cap():
    g = _guardian(daily_trades=10)  # at the cap
    assert g.check_trade(_ORDER, source="human_approved") is True


def test_under_cap_passes_for_any_source():
    g = _guardian(daily_trades=0)
    assert g.check_trade(_ORDER) is True
    assert g.check_trade(_ORDER, source="human_approved") is True


def test_existing_positional_call_unchanged():
    # the 4 production callers all call check_trade(order) with one positional arg
    g = _guardian(daily_trades=10)
    assert g.check_trade(_ORDER) is False
