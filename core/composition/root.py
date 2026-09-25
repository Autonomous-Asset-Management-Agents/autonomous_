import os
import threading
from typing import Optional

from core.ports.broker_port import BrokerPort
from core.ports.clock_port import ClockPort
from core.ports.state_port import StatePort


class CompositionRoot:
    """
    The Composition Root is the ONLY place where Editionsweichen
    (K_SERVICE, DEPLOYMENT_MODE, is_local_mode) are evaluated to
    wire up the correct adapters for the chosen edition.
    """

    _instance: Optional["CompositionRoot"] = None
    _lock = threading.Lock()

    def __init__(self):
        if CompositionRoot._instance is not None:
            raise RuntimeError(
                "CompositionRoot is a Singleton. Use get_instance()"
            )  # pragma: no cover

        self.state_port: StatePort = self._build_state_port()
        self.broker_port: BrokerPort = self._build_broker_port()
        self.clock_port: ClockPort = self._build_clock_port()
        self.trade_intelligence = self._build_trade_intelligence()

    @classmethod
    def get_instance(cls) -> "CompositionRoot":
        if cls._instance is None:  # pragma: no branch
            with cls._lock:
                if cls._instance is None:  # pragma: no branch
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """For testing purposes"""
        cls._instance = None

    def _build_state_port(self) -> StatePort:
        from core.adapters.sql_state_adapter import SqlStateAdapter

        return SqlStateAdapter()

    def _build_broker_port(self) -> BrokerPort:
        from core.adapters.alpaca_broker_adapter import AlpacaBrokerAdapter

        return AlpacaBrokerAdapter()

    def _build_clock_port(self) -> ClockPort:
        from core.adapters.system_clock import SystemClock

        return SystemClock()

    def _build_trade_intelligence(self):
        from core.trade_intelligence import TradeIntelligence

        return TradeIntelligence(clock=self.clock_port)

    @property
    def is_cloud(self) -> bool:
        """Returns True if running in the cloud (Enterprise)."""
        return os.environ.get("K_SERVICE") is not None

    @property
    def is_local_desktop(self) -> bool:
        """Returns True if running in Desktop/Local mode."""
        return (
            os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL"
            or os.environ.get("IS_LOCAL_DEV", "").upper() == "TRUE"
        )
