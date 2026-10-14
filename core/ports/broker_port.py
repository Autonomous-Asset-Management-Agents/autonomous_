from abc import ABC, abstractmethod
from typing import Any


class BrokerPort(ABC):
    """
    Abstract port for executing trading operations and getting market state.
    Isolates the core from the underlying Alpaca or paper trading API.
    """

    @abstractmethod
    async def submit_order(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover

    @abstractmethod
    async def replace_order_by_id(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover

    @abstractmethod
    async def cancel_order_by_id(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover

    @abstractmethod
    async def cancel_orders(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover

    @abstractmethod
    async def close_position(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover

    @abstractmethod
    async def close_all_positions(self, *args, **kwargs) -> Any:
        pass  # pragma: no cover
