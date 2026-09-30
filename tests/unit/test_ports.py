from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from core.ports.broker_port import BrokerPort
from core.ports.clock_port import ClockPort
from core.ports.state_port import StatePort


class DummyClock(ClockPort):
    def now(self) -> datetime:
        return datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    def time(self) -> float:
        return 1700000000.0


# Removed bad DummyState
import contextlib


class DummyState2(StatePort):
    @contextlib.asynccontextmanager
    async def get_session(self):
        yield AsyncMock(spec=AsyncSession)


@pytest.mark.vc0
@pytest.mark.asyncio
async def test_ports_interfaces():
    c = DummyClock()
    s = DummyState2()
    b = AsyncMock(spec=BrokerPort)
    b.submit_order.return_value = "submitted"
    b.replace_order_by_id.return_value = "replaced"
    b.cancel_order_by_id.return_value = "cancelled_one"
    b.cancel_orders.return_value = "cancelled_all"
    b.close_position.return_value = "closed_pos"
    b.close_all_positions.return_value = "closed_all"

    assert c.now().year == 2026
    assert c.time() == 1700000000.0

    async with s.get_session() as session:
        assert isinstance(session, AsyncMock)

    assert await b.submit_order() == "submitted"
    assert await b.replace_order_by_id() == "replaced"
    assert await b.cancel_order_by_id() == "cancelled_one"
    assert await b.cancel_orders() == "cancelled_all"
    assert await b.close_position() == "closed_pos"
    assert await b.close_all_positions() == "closed_all"
