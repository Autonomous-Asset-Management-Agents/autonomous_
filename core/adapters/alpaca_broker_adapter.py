from typing import Any

from core.ports.broker_port import BrokerPort


class AlpacaBrokerAdapter(BrokerPort):
    """
    Adapter for the Alpaca API.
    To be fully implemented when migrating broker logic.
    """

    async def submit_order(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover

    async def replace_order_by_id(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover

    async def cancel_order_by_id(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover

    async def cancel_orders(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover

    async def close_position(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover

    async def close_all_positions(self, *args, **kwargs) -> Any:
        raise NotImplementedError  # pragma: no cover
