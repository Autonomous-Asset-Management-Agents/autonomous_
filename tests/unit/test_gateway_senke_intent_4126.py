"""#4126 Teil 1a (Plan §10b): Die Tor-Senke nennt Symbol, Seite, Menge und Art.

Bisher bekam ``record_gateway_decision`` nur die ``ComplianceDecision``. Daraus liess sich
nicht ablesen, welche Order abgelehnt, freigegeben oder storniert wurde. Der Intent kommt
jetzt als optionales Schluesselwort mit; ein Storno traegt Symbol und Order-ID.
"""

import logging
from unittest.mock import MagicMock

import pytest

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode
from core.gateway import fabrik

pytestmark = pytest.mark.vc3


def _entscheidung(**ueber):
    basis = dict(
        decision_id="dec-1",
        approved=False,
        reason_code=ReasonCode.TRADING_HALTED,
        detail="Handel gehalten",
        halted=True,
    )
    basis.update(ueber)
    return ComplianceDecision(**basis)


def _intent():
    return OrderIntent(
        decision_id="dec-1",
        symbol="AAPL",
        side="buy",
        qty=2.0,
        intent_kind="entry",
        halted=True,
    )


def test_logzeile_nennt_symbol_seite_menge_und_art(caplog):
    with caplog.at_level(logging.INFO):
        fabrik.record_gateway_decision(_entscheidung(), intent=_intent())

    zeile = next(
        r.getMessage() for r in caplog.records if "decision_id=" in r.getMessage()
    )
    assert "decision_id=dec-1" in zeile
    assert "symbol=AAPL" in zeile
    assert "side=buy" in zeile
    assert "qty=2.0" in zeile
    assert "kind=entry" in zeile


def test_logzeile_nennt_storno_symbol_und_order_id(caplog):
    with caplog.at_level(logging.INFO):
        fabrik.record_gateway_decision(
            _entscheidung(approved=True, reason_code=ReasonCode.EXIT_EXEMPT),
            storno={"symbol": "AAPL", "order_id": "stop-1"},
        )

    zeile = next(
        r.getMessage() for r in caplog.records if "decision_id=" in r.getMessage()
    )
    assert "symbol=AAPL" in zeile
    assert "storno=stop-1" in zeile


def test_ohne_intent_bleibt_die_logzeile_wie_bisher(caplog):
    with caplog.at_level(logging.INFO):
        fabrik.record_gateway_decision(_entscheidung())

    zeile = next(
        r.getMessage() for r in caplog.records if "decision_id=" in r.getMessage()
    )
    assert "decision_id=dec-1" in zeile
    assert "reason=trading_halted" in zeile


def test_fabrik_reicht_den_intent_an_die_senke_weiter(monkeypatch):
    gesehen = []
    monkeypatch.setattr(
        fabrik,
        "record_gateway_decision",
        lambda d, **kw: gesehen.append((d, kw)),
    )
    tor = fabrik.gateway_for(MagicMock(), is_halted=lambda user_id=None: True)
    intent = _intent()

    tor.submit(intent, request={"symbol": "AAPL"})

    assert gesehen[-1][1]["intent"] is intent


def test_executor_reicht_den_intent_an_die_senke_weiter(monkeypatch):
    from core.engine import order_executor as oe

    gesehen = []
    monkeypatch.setattr(
        oe, "_record_gateway_decision", lambda d, **kw: gesehen.append((d, kw))
    )
    monkeypatch.setattr(oe.kill_switch, "is_halted", lambda user_id=None: True)
    intent = _intent()

    oe.gateway_for(MagicMock()).submit(intent, request={"symbol": "AAPL"})

    assert gesehen[-1][1]["intent"] is intent


def test_alte_senke_mit_nur_einem_argument_und_schluckendem_rest_bleibt_gueltig():
    gesammelt = []
    tor = fabrik.gateway_for(
        MagicMock(),
        is_halted=lambda user_id=None: True,
        record=lambda d, **_: gesammelt.append(d),
    )

    tor.submit(_intent(), request={"symbol": "AAPL"})

    assert gesammelt[-1].reason_code is ReasonCode.TRADING_HALTED
