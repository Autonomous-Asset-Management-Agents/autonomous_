"""#4396 — ein passender Broker-Stop bleibt liegen, auch wenn alpaca-py die Zeitgeltung liefert.

Gemessen (Docker-Test nach ARC-E6, 08./09.10.2026): Die Engine stornierte jeden Stop in jedem
Zyklus und legte ihn mit gleicher Menge und gleichem Preis neu. Ursache: alpaca-py liefert
``Order.time_in_force`` als ``TimeInForce``-Enum, und ``str(TimeInForce.GTC)`` ist
``"TimeInForce.GTC"``, nicht ``"gtc"``. Der Vergleich in ``broker_stops._passt`` schlug daher
immer fehl. Die bisherigen Tests bauten die liegende Order mit der Zeichenkette ``"gtc"`` und
sahen das nie.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import TimeInForce
from alpaca.trading.models import Order

from core.broker_stops import plan_broker_stops

pytestmark = pytest.mark.vc4

HEUTE = date(2026, 10, 8)
GESTERN = date(2026, 10, 7)


def _stop(oid, qty, preis, tif, cid):
    return SimpleNamespace(
        id=oid,
        symbol="FFIV",
        qty=qty,
        stop_price=preis,
        time_in_force=tif,
        client_order_id=cid,
    )


def _plan(pos, liegend, vorher=HEUTE):
    return plan_broker_stops(
        [pos],
        stop_loss_pct=10.0,
        existing_stops=liegend,
        session_date=HEUTE,
        existing_session_date=vorher,
    )


def test_alpaca_liefert_die_zeitgeltung_als_enum():
    """Die Annahme hinter diesem Test: das Order-Modell von alpaca-py traegt das Enum."""
    assert Order.model_fields["time_in_force"].annotation is TimeInForce
    assert str(TimeInForce.GTC) != "gtc"


def test_ein_passender_gtc_stop_bleibt_liegen():
    # Messung FFIV: 7 Stueck, alle vom eigenen Stop gebunden; Stop 414.56 bei 10 % Schwelle.
    pos = SimpleNamespace(
        symbol="FFIV", qty="7", qty_available="0", avg_entry_price="460.62"
    )
    liegend = [_stop("o1", "7", "414.56", TimeInForce.GTC, "stop-388-stop-FFIV")]

    plan = _plan(pos, liegend)

    assert plan.to_place == ()
    assert plan.to_cancel == ()


def test_ganze_und_bruchstueck_bleiben_in_derselben_sitzung_liegen():
    pos = SimpleNamespace(
        symbol="FFIV", qty="7.5", qty_available="0", avg_entry_price="460.62"
    )
    liegend = [
        _stop("o1", "7", "414.56", TimeInForce.GTC, "stop-388-stop-FFIV"),
        _stop("o2", "0.5", "414.56", TimeInForce.DAY, "stop-12-stop-FFIV-2026-10-08"),
    ]

    plan = _plan(pos, liegend)

    assert plan.to_place == ()
    assert plan.to_cancel == ()


def test_ein_tages_stop_der_vorsitzung_zaehlt_nicht_als_deckung():
    """Der zweite Vergleich (Filter abgelaufener Tages-Stops) hatte denselben Fehler."""
    pos = SimpleNamespace(
        symbol="FFIV", qty="7.5", qty_available="0", avg_entry_price="460.62"
    )
    liegend = [
        _stop("o1", "7", "414.56", TimeInForce.GTC, "stop-388-stop-FFIV"),
        _stop("o2", "0.5", "414.56", TimeInForce.DAY, "stop-11-stop-FFIV-2026-10-07"),
    ]

    plan = _plan(pos, liegend, vorher=GESTERN)

    assert [(s.leg, s.qty) for s in plan.to_place] == [("fraction", 0.5)]
    assert "o1" not in plan.to_cancel
