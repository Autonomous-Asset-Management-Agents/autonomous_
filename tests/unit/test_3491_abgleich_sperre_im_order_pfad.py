"""#3491 (ARC-E2.15) — die Abgleich-Sperre haelt Einstiege im Order-Pfad zurueck.

Befund vom 18.09.2026: ``ReconciliationService.blocks()`` hatte keinen Aufrufer im Order-Pfad —
die Sperre aus #3389 war sichtbar und aufhebbar (#3430), hielt aber keine Order zurueck. Der Plan
zu #3389 verlangte die Pruefung vor dem Absenden und den Gegentest „Einstiege ja, Schutz-Exits
nein". Owner-Entscheid 18.09.: die strenge Variante bleibt (auch vor dem Start-Abgleich gesperrt).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.exceptions import TradingHaltedError

pytestmark = [pytest.mark.unit, pytest.mark.vc5, pytest.mark.vc3]


@pytest.fixture(autouse=True)
def tor_offen(monkeypatch):
    from core import reconciliation
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)  # keine Sperre/Outbox
    monkeypatch.setattr(reconciliation, "_aktiver", None)


def _dienst(*, sperrt: bool, abgeglichen: bool = True, block_on_break: bool = True):
    from core.contracts import ReconciliationBreak, ReconciliationRecord
    from core.reconciliation import ReconciliationService

    dienst = ReconciliationService(
        api=None, redis_client=None, block_on_break=block_on_break
    )
    dienst.reconciled_once = abgeglichen
    if sperrt:
        jetzt = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
        befund = ReconciliationRecord(
            run_id="lauf-1",
            started_at=jetzt,
            finished_at=jetzt,
            breaks=(ReconciliationBreak(kind="unknown_position", symbol="MSFT"),),
        )
        dienst._report(befund)
    return dienst


async def _sende(client, *, seite="buy", schutz=False):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.engine.order_executor import OrderExecutorMixin

    side = OrderSide.BUY if seite == "buy" else OrderSide.SELL
    anfrage = MarketOrderRequest(
        symbol="AAPL",
        qty=1,
        side=side,
        time_in_force=TimeInForce.DAY,
        client_order_id="entry-0-entscheidung-3491",
    )
    return await OrderExecutorMixin._sende_durchs_tor(
        client=client,
        request=anfrage,
        symbol="AAPL",
        side_enum=side,
        qty=1.0,
        user_id="global",
        decision_id="entscheidung-3491",
        is_protective_exit=schutz,
    )


def _broker():
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")
    return client


async def test_eine_gesetzte_sperre_haelt_einen_einstieg_zurueck(caplog) -> None:
    import logging

    from core import reconciliation

    reconciliation.setze_aktiven(_dienst(sperrt=True))
    client = _broker()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(TradingHaltedError, match="Abgleich-Sperre"):
            await _sende(client)

    client.submit_order.assert_not_called()
    assert any("Abgleich-Sperre" in r.getMessage() for r in caplog.records)


async def test_ein_schutz_exit_passiert_die_sperre() -> None:
    from core import reconciliation

    reconciliation.setze_aktiven(_dienst(sperrt=True))
    client = _broker()

    await _sende(client, seite="sell", schutz=True)

    client.submit_order.assert_called_once()


async def test_ein_trim_passiert_die_sperre() -> None:
    """Risikomindernd, nicht Risikoaufnahme — NEVER_BLOCKED_ON_BREAK."""
    from core import reconciliation

    reconciliation.setze_aktiven(_dienst(sperrt=True))
    client = _broker()

    await _sende(client, seite="sell")

    client.submit_order.assert_called_once()


async def test_mit_ausgeschalteter_sperrwirkung_geht_der_einstieg() -> None:
    """RECONCILIATION_BLOCK_ON_BREAK aus (Default): Abweichungen werden gemeldet, nicht
    gesperrt — dann aendert sich im Order-Pfad nichts."""
    from core import reconciliation

    reconciliation.setze_aktiven(_dienst(sperrt=True, block_on_break=False))
    client = _broker()

    await _sende(client)

    client.submit_order.assert_called_once()


async def test_nach_der_aufhebung_geht_der_einstieg() -> None:
    from core import reconciliation

    dienst = _dienst(sperrt=True)
    reconciliation.setze_aktiven(dienst)
    dienst.release_block(by="lokaler Bediener")
    client = _broker()

    await _sende(client)

    client.submit_order.assert_called_once()


async def test_vor_dem_start_abgleich_ist_gesperrt() -> None:
    """Owner-Entscheid 18.09.: die strenge Variante bleibt — ein Einstieg vor dem ersten
    Abgleich entschiede auf einem Bestand, den niemand gegen den Broker gehalten hat."""
    from core import reconciliation

    reconciliation.setze_aktiven(_dienst(sperrt=False, abgeglichen=False))
    client = _broker()

    with pytest.raises(TradingHaltedError, match="Abgleich-Sperre"):
        await _sende(client)

    client.submit_order.assert_not_called()


async def test_ohne_laufenden_abgleich_wird_nicht_geprueft() -> None:
    client = _broker()

    await _sende(client)

    client.submit_order.assert_called_once()


def test_der_start_abgleich_meldet_seinen_dienst_an() -> None:
    """Verdrahtung, nicht nur Baustein: _start_reconciliation macht den Dienst fuer die
    Absendestelle erreichbar — und nimmt ihn zurueck, wenn der Start scheitert."""
    import ast
    from pathlib import Path

    pfad = Path(__file__).resolve().parents[2] / "core" / "engine" / "trading_loop.py"
    text = pfad.read_text(encoding="utf-8")
    for knoten in ast.walk(ast.parse(text)):
        if (
            isinstance(knoten, ast.AsyncFunctionDef)
            and knoten.name == "_start_reconciliation"
        ):
            quelle = ast.get_source_segment(text, knoten)
            break
    assert "setze_aktiven(self.reconciler)" in quelle
    assert "setze_aktiven(None)" in quelle


def test_ein_halt_ist_am_typ_erkennbar() -> None:
    """#3500 / Review zu #3495 (POLICY-01): alle Halt-Gruende der Absendestelle werfen
    ``TradingHaltedError`` — eine Unterklasse von ``Exception``, damit jedes bestehende
    ``except Exception`` unveraendert greift."""
    from core.exceptions import TradingBotError

    assert issubclass(TradingHaltedError, TradingBotError)
    assert issubclass(TradingHaltedError, Exception)
