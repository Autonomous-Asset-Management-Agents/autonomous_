"""#3447, Schritt 3 — der Strategiepfad geht durchs Tor (Plan: docs/3447-strategiepfad-durchs-tor).

``BaseStrategy._submit_order_safe`` ist der Legacy-Rückfallpfad: ``_run_strategy_node`` fällt bei
jeder Ausnahme aus dem Round Table auf ihn zurück, und über ihn läuft der Stop-Loss der
Default-Strategie. Er war die letzte kapitalbewegende Stelle am Tor vorbei.

Was dieser Schritt **nicht** anfasst — und was diese Tests darum ausdrücklich festhalten: die vier
Regeln davor (ComplianceGuardian, Marktzeit, doppelte Pending-Order, Kaufkraft/PDT), ``held_qty``
und das Rückgabeverhalten.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from tests.unit.test_base_strategy import _make_concrete_strategy

pytestmark = pytest.mark.unit


class _AlpacaClient:
    """Die produktive Form: ``submit_order(order_data)`` wie ``alpaca.trading.TradingClient``.

    Ein ``MagicMock`` taugt dafür nicht — seine Signatur ist ``(*args, **kwargs)``, und
    ``_do_submit`` waehlt die Aufrufform ueber ``inspect.signature``.
    """

    def __init__(self, fehler: Exception | None = None):
        self.gesendet = []
        self._fehler = fehler

    def submit_order(self, order_data):
        if self._fehler is not None:
            raise self._fehler
        self.gesendet.append(order_data)
        return MagicMock(id="brk-1")

    def get_clock(self):
        return MagicMock(is_open=True)

    def get_account(self):
        return MagicMock(
            buying_power=10000.0,
            daytrading_buying_power=1000.0,
            cash=8000.0,
            pattern_day_trader=False,
        )

    def list_orders(self, status="open"):
        return []


@pytest.fixture
def datensaetze(monkeypatch):
    """Faengt ab, was das Tor in die Senke schreibt."""
    import core.gateway.fabrik as fabrik

    gesammelt = []
    monkeypatch.setattr(fabrik, "record_gateway_decision", gesammelt.append)
    return gesammelt


def _halt(monkeypatch, gehalten: bool) -> None:
    """Auf der KLASSE patchen — ein Instanz-Patch leckt (Lehre aus PR #3457)."""
    from core.kill_switch import kill_switch

    klasse = type(kill_switch)
    monkeypatch.setattr(klasse, "is_halted", lambda self, user_id=None: gehalten)

    def _check(self, user_id=None):
        if gehalten:
            raise Exception("System is HALTED by Kill Switch")

    monkeypatch.setattr(klasse, "check_halt", _check)


# ---------------------------------------------------------------------------
# Die Order geht durchs Tor
# ---------------------------------------------------------------------------


async def test_die_order_des_strategiepfads_traegt_eine_compliancedecision(
    datensaetze, monkeypatch
) -> None:
    _halt(monkeypatch, False)
    client = _AlpacaClient()
    strategie = _make_concrete_strategy(client)

    ok = await strategie._submit_order_safe(
        "AAPL", 2.0, "buy", expected_cost=200.0, current_price=100.0
    )

    assert ok is True
    assert (
        len(client.gesendet) == 1
    ), "Der Broker hat die Order nicht genau einmal erhalten."
    assert [d.approved for d in datensaetze] == [True], (
        "Zur Order des Strategiepfads gibt es keine ComplianceDecision — sie ging am Tor "
        "vorbei (#3447)."
    )


async def test_die_kwargs_form_ruft_den_client_unveraendert(
    datensaetze, monkeypatch, live_client
) -> None:
    """Clients ohne ``order_data`` (Tests, Altclients) bekommen byte-gleich dieselben Argumente."""
    _halt(monkeypatch, False)
    strategie = _make_concrete_strategy(live_client)

    ok = await strategie._submit_order_safe("AAPL", 2.0, "buy", expected_cost=200.0)

    assert ok is True
    live_client.submit_order.assert_called_once_with(
        symbol="AAPL", qty=2.0, side="buy", type="market", time_in_force="day"
    )
    assert len(datensaetze) == 1


@pytest.fixture
def live_client():
    client = MagicMock(spec=["get_clock", "get_account", "list_orders", "submit_order"])
    client.get_clock.return_value = MagicMock(is_open=True)
    client.get_account.return_value = MagicMock(
        buying_power=10000.0,
        daytrading_buying_power=1000.0,
        cash=8000.0,
        pattern_day_trader=False,
    )
    client.list_orders.return_value = []
    return client


# ---------------------------------------------------------------------------
# Was sich NICHT aendert
# ---------------------------------------------------------------------------


async def test_die_regeln_greifen_vor_dem_tor(datensaetze, monkeypatch) -> None:
    """Blockt der ComplianceGuardian, werden weder Tor noch Broker gerufen."""
    _halt(monkeypatch, False)
    client = _AlpacaClient()
    strategie = _make_concrete_strategy(client)
    strategie.compliance_guardian = MagicMock()
    strategie.compliance_guardian.check_order.return_value = False

    ok = await strategie._submit_order_safe("AAPL", 2.0, "buy", expected_cost=200.0)

    assert ok is False
    assert client.gesendet == []
    assert datensaetze == [], "Das Tor wurde gerufen, obwohl die Regel davor blockte."


async def test_ein_brokerfehler_fuehrt_weiter_zu_false(
    datensaetze, monkeypatch
) -> None:
    _halt(monkeypatch, False)
    strategie = _make_concrete_strategy(_AlpacaClient(fehler=RuntimeError("abgelehnt")))

    ok = await strategie._submit_order_safe("AAPL", 2.0, "buy", expected_cost=200.0)

    assert ok is False
    assert [d.reason_code.value for d in datensaetze] == [
        "system_error"
    ], "Auch der gescheiterte Versuch gehoert in den Datensatz."


# ---------------------------------------------------------------------------
# Halt: Schutz-Exit frei, alles andere geblockt (#3380 / #3466)
# ---------------------------------------------------------------------------


async def test_bei_halt_wird_ein_normaler_verkauf_geblockt(
    datensaetze, monkeypatch
) -> None:
    _halt(monkeypatch, True)
    client = _AlpacaClient()
    strategie = _make_concrete_strategy(client)

    ok = await strategie._submit_order_safe("AAPL", 2.0, "sell", held_qty=2.0)

    assert ok is False
    assert client.gesendet == []


async def test_bei_halt_passiert_der_stop_loss(
    datensaetze, monkeypatch, caplog
) -> None:
    """Der Stop-Loss der Default-Strategie laeuft ueber diesen Pfad — er darf nicht haengen."""
    _halt(monkeypatch, True)
    client = _AlpacaClient()
    strategie = _make_concrete_strategy(client)

    with caplog.at_level(logging.WARNING):
        ok = await strategie._submit_order_safe(
            "AAPL", 2.0, "sell", held_qty=2.0, is_protective_exit=True
        )

    assert ok is True
    assert len(client.gesendet) == 1
    assert any(
        "Schutz-Exit" in r.getMessage() and "3380" in r.getMessage()
        for r in caplog.records
    ), "Die Freistellung ist nicht auf WARNING gemeldet."


async def test_bei_halt_oeffnet_das_kennzeichen_keinen_kauf(
    datensaetze, monkeypatch
) -> None:
    """Ein Halt heisst: kein neues Kapital. Auch nicht mit falsch gesetztem Kennzeichen."""
    _halt(monkeypatch, True)
    client = _AlpacaClient()
    strategie = _make_concrete_strategy(client)

    ok = await strategie._submit_order_safe(
        "AAPL", 2.0, "buy", expected_cost=200.0, is_protective_exit=True
    )

    assert ok is False
    assert client.gesendet == []


# ---------------------------------------------------------------------------
# Wer ist ein Schutz-Exit? — die Stufe aus #3180
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("triggered", "exit_info", "erwartet"),
    [
        (True, {"triggered": True, "tier": "risk"}, True),  # Stop, Trailing, Loss-Cut
        (True, {"triggered": True, "tier": "opinion"}, False),  # Gewinnmitnahme
        (False, {"triggered": False}, False),  # Modell-SELL
        (True, {"triggered": True}, False),  # ohne Stufe: keine Freistellung
    ],
)
def test_nur_ein_risiko_ausstieg_ist_ein_schutz_exit(triggered, exit_info, erwartet):
    from core.strategies.rl_execution import ist_schutz_exit

    assert ist_schutz_exit(triggered, exit_info) is erwartet


def test_der_stop_loss_aufruf_reicht_das_kennzeichen_herein() -> None:
    """Ohne diese Uebergabe waere die Freistellung toter Code: Der einzige SELL der
    Default-Strategie, der ein Schutz-Exit sein kann, muss ``is_protective_exit`` setzen —
    abgeleitet aus der Stufe, nicht fest verdrahtet."""
    import ast
    from pathlib import Path

    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "strategies" / "rl_execution.py"
    ).read_text(encoding="utf-8")
    verkaeufe = []
    for knoten in ast.walk(ast.parse(quelle)):
        if (
            isinstance(knoten, ast.Call)
            and isinstance(knoten.func, ast.Attribute)
            and knoten.func.attr == "_submit_order_safe"
            and any(
                isinstance(a, ast.Constant) and a.value == "sell" for a in knoten.args
            )
            and any(k.arg == "held_qty" for k in knoten.keywords)
        ):
            verkaeufe.append(knoten)

    haltefest = [k for k in verkaeufe if not _rotation_oder_tausch(quelle, k)]
    assert haltefest, "Der Stop-Loss-Aufruf in rl_execution.py wurde nicht gefunden."
    for aufruf in haltefest:
        kennzeichen = {k.arg: k.value for k in aufruf.keywords}.get(
            "is_protective_exit"
        )
        assert (
            isinstance(kennzeichen, ast.Call)
            and getattr(kennzeichen.func, "id", "") == "ist_schutz_exit"
        ), (
            f"rl_execution.py:{aufruf.lineno}: der Verkauf reicht is_protective_exit nicht "
            "ueber ist_schutz_exit(...) herein."
        )


def _rotation_oder_tausch(quelle: str, aufruf) -> bool:
    """Der Verkauf fuer einen Portfolio-Tausch ist eine Meinung, kein Schutz."""
    zeilen = quelle.splitlines()[max(0, aufruf.lineno - 6) : aufruf.lineno]
    return any("portfolio swap" in z for z in zeilen)
