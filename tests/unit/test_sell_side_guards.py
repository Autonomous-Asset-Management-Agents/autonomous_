# tests/unit/test_sell_side_guards.py
"""#2713 (Epic #1957, P1) — exit_kind precision: only genuine risk exits stay exempt.

Guard audit 09.08: COMPLIANCE_ALLOW_RISK_REDUCING_EXITS waved EVERY held-qty-bounded SELL
(incl. rotation/trim churn) past the wash window and the daily cap. Fix: rotation/trim
sells lose the exemption (counted + wash-checked); stop-outs keep it — a protective exit
must NEVER queue behind a guard. Unknown kind keeps legacy exemption (fail-SAFE for
not-yet-plumbed risk paths).
"""

from types import SimpleNamespace

from core.compliance import ComplianceGuardian
from core.engine.order_executor import classify_exit_kind


def _order(kind=None, qty=1.0, held=2.0):
    o = {
        "symbol": "X",
        "side": "sell",
        "quantity": qty,
        "price": 10.0,
        "strategy_id": "t",
        "timestamp": 0.0,
        "user_id": "global",
        "held_qty": held,
    }
    if kind is not None:
        o["exit_kind"] = kind
    return o


def test_classify_stop_is_risk():
    assert (
        classify_exit_kind(SimpleNamespace(triggered_by_stop=True, portfolio_reason=""))
        == "risk"
    )


def test_classify_rotation_and_trim_from_reason():
    assert (
        classify_exit_kind(
            SimpleNamespace(
                triggered_by_stop=False,
                portfolio_reason="rotation: dropped to rank 44/300",
            )
        )
        == "rotation"
    )
    assert (
        classify_exit_kind(
            SimpleNamespace(
                triggered_by_stop=False,
                portfolio_reason="SECTOR_CAP Technology: 28.0% > approved cap",
            )
        )
        == "trim"
    )
    assert (
        classify_exit_kind(
            SimpleNamespace(
                triggered_by_stop=False,
                portfolio_reason="REDUCE X: drift +4.2% from target",
            )
        )
        == "trim"
    )


def test_classify_unknown_is_none():
    assert (
        classify_exit_kind(
            SimpleNamespace(triggered_by_stop=False, portfolio_reason="")
        )
        is None
    )
    assert classify_exit_kind(None) is None


def test_rotation_and_trim_lose_the_exemption():
    g = ComplianceGuardian.__new__(ComplianceGuardian)
    assert g._is_risk_reducing_exit(_order(kind="rotation")) is False
    assert g._is_risk_reducing_exit(_order(kind="trim")) is False


def test_risk_and_unknown_keep_the_exemption_bounded_by_held_qty():
    g = ComplianceGuardian.__new__(ComplianceGuardian)
    assert g._is_risk_reducing_exit(_order(kind="risk")) is True
    assert g._is_risk_reducing_exit(_order()) is True  # legacy paths stay stop-safe
    assert (
        g._is_risk_reducing_exit(_order(qty=3.0)) is False
    )  # over held -> never exempt
