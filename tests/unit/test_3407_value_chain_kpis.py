import pytest

from core.value_chain_kpis import StageAlert, ValueChainMonitor

pytestmark = [pytest.mark.vc0]


def test_value_chain_kpis_evaluation():
    monitor = ValueChainMonitor()
    metrics = {
        "data_coverage": 0.95,
        "decisions_count": 12,
        "blocked_orders": 1,
        "filled_orders": 3,
        "pnl_pct": 2.4,
        "audit_records": 15,
    }
    annotated, alerts = monitor.evaluate(metrics)

    assert "VC-1_data_coverage" in annotated
    assert annotated["VC-1_data_coverage"] == 0.95
    assert "VC-2_decisions_count" in annotated
    assert "VC-3_filled_orders" in annotated
    assert "VC-4_blocked_orders" in annotated
    assert "VC-5_pnl_pct" in annotated
    assert "VC-6_audit_records" in annotated
    assert len(alerts) == 0


def test_value_chain_kpis_missing_metric_alert():
    monitor = ValueChainMonitor()
    metrics = {
        "decisions_count": 5,
    }
    annotated, alerts = monitor.evaluate(metrics)

    assert annotated["VC-1_data_coverage"] is None
    assert annotated["VC-2_decisions_count"] == 5
    assert len(alerts) >= 1
    assert any(a.stage == "VC-1" and a.is_missing for a in alerts)
