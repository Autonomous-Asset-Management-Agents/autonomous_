import pytest
from scripts.live_monitor_cli import fetch_data, generate_table


def test_fetch_data_success(requests_mock):
    # Mock health endpoint
    requests_mock.get(
        "http://localhost:8001/system-health",
        json={"status": "healthy", "alpaca_connection": True, "memory_usage_mb": 150},
    )

    # Mock compliance endpoint
    requests_mock.get(
        "http://localhost:8001/compliance-status",
        json={
            "status": "active",
            "iron_dome_active": True,
            "kill_switch_triggered": False,
        },
    )

    health, compliance = fetch_data()
    assert health["status"] == "healthy"
    assert compliance["kill_switch_triggered"] is False


def test_generate_table_healthy():
    health = {"status": "healthy", "alpaca_connection": True, "memory_usage_mb": 100}
    compliance = {
        "status": "active",
        "iron_dome_active": True,
        "kill_switch_triggered": False,
    }

    table = generate_table(health, compliance)

    # Simple assertion to ensure rendering does not crash and returns a Table
    assert table.title == "Live-Ops Dashboard (AAA Platform)"
    assert len(table.rows) == 5


def test_generate_table_triggered_kill_switch():
    health = {"status": "healthy", "alpaca_connection": True, "memory_usage_mb": 100}
    compliance = {
        "status": "disabled",
        "iron_dome_active": False,
        "kill_switch_triggered": True,
    }

    table = generate_table(health, compliance)

    # Since we can't easily assert on rich rendered output, we just test it constructs successfully
    assert table is not None
