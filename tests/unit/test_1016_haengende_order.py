"""#1016 (Epic #3367 — ARC-E2) — Die Frist, nach der eine haengende Order storniert wird.

Der Auftrag von #1016 lautet: „Wenn die Order nicht gefuellt ist, sende einen
Cancel-Request an Alpaca. Mit try/except fuer Race Conditions."

**Das gibt es** — im Tenant-Pfad (`order_executor.py::_execute_tenant_order`): Die Order
wird gepollt, und bleibt sie ungefuellt, geht ein Storno an den Broker, in try/except
gekapselt. `test_order_polling_timeout_and_cancel` haelt das fest.

Was fehlte, ist die **Frist selbst**. Sie stand als nackte `120` im Kapitalpfad: nicht
abschaltbar, nicht verkuerzbar, ohne Begruendung und ohne ADR-Kommentar — obwohl sie
entscheidet, wie lange Kapital in einer nicht gefuellten Order gebunden bleibt und wie
lange ein Schutz-Exit auf seinen Fill wartet.

Diese Tests pinnen: die Frist ist ein Feld der Konfiguration, ihr Auslieferungswert ist
der bisherige Wirkwert (120 s / 2 s Takt), und der Order-Pfad benutzt sie wirklich.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc3


def test_die_frist_ist_ein_feld_mit_dem_bisherigen_wirkwert():
    import config

    cfg = config.get_config()
    assert cfg.ORDER_FILL_TIMEOUT_SECONDS == 120.0
    assert cfg.ORDER_FILL_POLL_SECONDS == 2.0


def test_die_frist_steht_nicht_mehr_als_zahl_im_order_pfad():
    """Die nackte 120 im Kapitalpfad war der eigentliche Befund."""
    quelle = (_AI_BOT / "core" / "engine" / "order_executor.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "max_wait_seconds = 120" not in quelle
    assert "ORDER_FILL_TIMEOUT_SECONDS" in quelle


@pytest.mark.anyio
@pytest.mark.mutates_global_state
async def test_eine_kuerzere_frist_storniert_frueher():
    """Der Pfad liest die Frist wirklich — mit 10 s wird nach 5 Runden storniert."""
    from alpaca.trading.enums import OrderStatus

    from tests.unit.test_order_executor import _make_executor, _make_signal

    executor = _make_executor()
    tenant_api = MagicMock()
    tenant_api.submit_order.return_value = MagicMock(id="order-1016")
    tenant_api.get_order_by_id.return_value = MagicMock(status=OrderStatus.NEW)

    tenant = {"user_id": "user-1016", "client": tenant_api, "equity": 10000.0}
    event = _make_signal(action="BUY", symbol="AAPL", qty=1.0)

    rm = MagicMock()
    rm.calculate_position_size.return_value = 1.0
    executor._get_tenant_risk_manager = MagicMock(return_value=rm)
    pm = MagicMock()
    pm.should_open_new_position.return_value = (True, "OK", None)
    executor._get_tenant_portfolio_manager = MagicMock(return_value=pm)

    with (
        patch("core.engine.order_executor.RedisClient") as mock_redis,
        patch("core.engine.order_executor.config.SHADOW_MODE", False, create=True),
        patch(
            "core.engine.order_executor.config.ORDER_FILL_TIMEOUT_SECONDS",
            10.0,
            create=True,
        ),
        patch(
            "core.engine.order_executor.config.ORDER_FILL_POLL_SECONDS",
            2.0,
            create=True,
        ),
        patch("core.engine.order_executor.kill_switch") as ks,
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        mock_redis.get_redis = AsyncMock(
            return_value=MagicMock(
                publish=AsyncMock(),
                set=AsyncMock(),
                lock=MagicMock(
                    return_value=MagicMock(
                        acquire=AsyncMock(return_value=True),
                        release=AsyncMock(return_value=True),
                    )
                ),
            )
        )
        ks.check_halt = MagicMock()
        # #3447: Der Halt-Zustand wird an zwei Stellen gelesen; ein unbesetztes
        # `is_halted` liefert einen wahrheitswertigen MagicMock.
        ks.is_halted = MagicMock(return_value=False)

        await executor._execute_tenant_order(tenant, event)

    tenant_api.submit_order.assert_called_once()
    # 10 s / 2 s Takt = 5 Runden statt 60. Andere Aufgaben schlafen auch, deshalb `<`.
    assert mock_sleep.call_count < 60, mock_sleep.call_count
    tenant_api.cancel_order_by_id.assert_called_once_with("order-1016")
