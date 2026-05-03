#!/usr/bin/env python3
"""
Live-Ops Terminal Dashboard
Polls the Trading Engine API on localhost:8001 and displays a live rich table.
"""

import time
import requests
from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich import box


def fetch_data():
    health_data = {
        "status": "UNKNOWN",
        "alpaca_connection": False,
        "memory_usage_mb": 0,
    }
    compliance_data = {
        "status": "UNKNOWN",
        "iron_dome_active": False,
        "kill_switch_triggered": False,
    }

    try:
        r_health = requests.get("http://localhost:8001/system-health", timeout=2)
        if r_health.status_code == 200:
            health_data = r_health.json()
    except requests.RequestException:
        pass

    try:
        r_comp = requests.get("http://localhost:8001/compliance-status", timeout=2)
        if r_comp.status_code == 200:
            compliance_data = r_comp.json()
    except requests.RequestException:
        pass

    return health_data, compliance_data


def generate_table(health, compliance) -> Table:
    table = Table(title="Live-Ops Dashboard (AAA Platform)", box=box.ROUNDED)

    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Status", style="magenta")

    # API Health
    engine_status = (
        "[green]ONLINE[/green]"
        if health.get("status") == "healthy"
        else "[red]OFFLINE[/red]"
    )
    table.add_row("Engine API Status", engine_status)

    # Alpaca
    alpaca = (
        "[green]🟢 Connected[/green]"
        if health.get("alpaca_connection")
        else "[red]🔴 Disconnected[/red]"
    )
    table.add_row("Alpaca Connection", alpaca)

    # Memory
    mem = f"{health.get('memory_usage_mb', 0)} MB"
    table.add_row("Memory Usage", mem)

    # Kill Switch
    ks_triggered = compliance.get("kill_switch_triggered", False)
    ks_status = (
        "[red]🔴 TRIGGERED[/red]" if ks_triggered else "[green]🟢 Inactive[/green]"
    )
    table.add_row("Kill-Switch Status", ks_status)

    # Iron Dome
    iron_dome = (
        "[green]Active[/green]"
        if compliance.get("iron_dome_active")
        else "[yellow]Inactive[/yellow]"
    )
    table.add_row("Iron Dome (MiFID II)", iron_dome)

    return table


def render_loop():
    console = Console()
    with Live(generate_table({}, {}), console=console, refresh_per_second=1) as live:
        while True:
            health, comp = fetch_data()
            live.update(generate_table(health, comp))
            time.sleep(5)


if __name__ == "__main__":
    try:
        render_loop()
    except KeyboardInterrupt:
        print("\nExiting Live-Ops Dashboard.")
