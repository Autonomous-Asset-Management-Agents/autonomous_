from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrokerClientProtocol(Protocol):
    """
    Protocol defining the required interface for a Broker Client.
    This enables Dependency Injection and makes swapping or mocking the broker
    (e.g. for testing or switching from Alpaca to another provider) seamless.
    """

    def submit_order(self, order_data: Any) -> Any:
        """Submit a trade order to the broker."""
        ...

    def get_account(self) -> Any:
        """Retrieve account details (equity, cash, etc.)."""
        ...

    def get_open_position(self, symbol: str) -> Any:
        """Retrieve the current open position for a specific symbol."""
        ...

    def get_clock(self) -> Any:
        """Retrieve market clock status."""
        ...
