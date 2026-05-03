import sys
import os
from unittest.mock import MagicMock

# Sicherstellen, dass das 'core' Paket gefunden wird
sys.path.insert(0, os.getcwd())

# Manuelles Mocking der Abhängigkeiten um Imports zu vermeiden
sys.modules["core.database.session"] = MagicMock()
sys.modules["core.database.models"] = MagicMock()
sys.modules["core.round_table.agents"] = MagicMock()
sys.modules["sqlalchemy"] = MagicMock()
sys.modules["sqlalchemy.orm"] = MagicMock()
sys.modules["opentelemetry"] = MagicMock()
sys.modules["opentelemetry.sdk.trace"] = MagicMock()

# Nun importieren wir die betroffene Funktion
# Wir nutzen importlib um Caching-Probleme zu vermeiden
import importlib

try:
    from core.round_table.runner import _score_to_signal
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    print("✅ Imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)


def test_logic():
    print("--- Verifying runner.py logic ---")
    state = {
        "symbol": "AAPL",
        "ohlc": {"close": 150.0, "open": 149.0},
        "current_time": "2026-04-01T10:00:00Z",
    }
    votes = [
        MagicMock(
            agent_name="Test", score=0.8, weight=1.0, reasoning="Bullish", vetoed=False
        )
    ]

    signal = _score_to_signal(state, 0.8, votes)

    print(f"Signal Action: {signal.action}")
    print(f"Decision Context Price: {signal.decision_context.current_price}")

    # VERIFICATION
    assert signal.decision_context.current_price == 150.0
    print("🚀 SUCCESS: runner.py logic is CORRECT (Price is populated)")


try:
    test_logic()
except Exception as e:
    print(f"🔥 FAILURE: {e}")
    sys.exit(1)
