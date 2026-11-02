"""#3712 schnelle Sim: Sizing-Adapter, Fuellung, fehlende Daten (Plan TDD-Schritt 3)."""

from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.vc1]

LSTM = "LSTMSignalAgent"


def test_sizing_adapter_entkoppelt(monkeypatch):
    import core.risk_manager as rm
    from core.kill_switch import LokalerHalt
    from core.sim.stimmen_sim import StimmenSimStrategie, bemesse_position

    global_vorher = rm.effective_max_positions()
    zwei = StimmenSimStrategie(name="zwei", max_positions=2)
    zwanzig = StimmenSimStrategie(name="zwanzig", max_positions=20)
    args = {"equity": 100_000.0, "cash": 100_000.0, "preis": 50.0, "atr": 1.0}

    klein, gross = bemesse_position(zwanzig, **args), bemesse_position(zwei, **args)

    assert 0 < klein < gross
    assert rm.effective_max_positions() == global_vorher
    # Referenz: der echte Sizer mit der Slot-Zahl der Strategie als Deckel.
    monkeypatch.setattr(rm, "effective_max_positions", lambda: 2)
    echt = rm.RiskManager(
        client=None, total_capital=100_000.0, kill_switch=LokalerHalt(), clock=None
    )
    erwartet = echt.calculate_position_size(
        stop_loss_atr_multiplier=3.0,
        atr=1.0,
        confidence="high",
        size_scaler=1.0,
        market_data={"vix": 20.0},
        num_stocks_in_strategy=rm.effective_free_slots(0),
        current_price=50.0,
        account_cash=100_000.0,
        allow_fractional=True,
        conviction_score=0.5,
    )
    assert gross == pytest.approx(erwartet)


def _fill_engine_referenz(auftraege):
    """Dieselben Auftraege durch die echte SimFillEngine (Broker und Kurse gedoppelt)."""
    from alpaca.trading.enums import OrderSide

    from core.sim.fill_engine import SimFillEngine

    uhr = SimpleNamespace(current_time=datetime(2026, 1, 2))
    broker = SimpleNamespace(cash=1_000.0, positions={}, clock=uhr)
    engine = SimFillEngine.__new__(SimFillEngine)  # ohne SimDataClient-Aufbau
    engine.broker = broker
    for seite, sym, qty, preis in auftraege:
        bars = SimpleNamespace(data={sym: [SimpleNamespace(close=preis)]})
        engine.data_client = SimpleNamespace(get_stock_bars=lambda req, b=bars: b)
        order = SimpleNamespace(symbol=sym, qty=str(qty), side=OrderSide[seite])
        order.status, order.stop_price, order.filled_qty = "new", None, None
        engine.process_orders([order])
    return broker.cash, {s: float(p.qty) for s, p in broker.positions.items()}


def test_fuellung_wie_sim_fill_engine():
    from core.sim.stimmen_sim import Portfolio

    auftraege = [
        ("BUY", "A", 3.0, 100.0),
        ("BUY", "A", 2.0, 110.0),
        ("BUY", "B", 50.0, 100.0),  # zu teuer: scheitert wie in der SimFillEngine
        ("SELL", "A", 5.0, 120.0),
        ("BUY", "B", 4.0, 90.0),
    ]
    p = Portfolio(cash=1_000.0)
    for seite, sym, qty, preis in auftraege:
        if seite == "BUY":
            p.kaufe(sym, qty, preis)
        else:
            p.verkaufe(sym, preis)

    cash, positionen = _fill_engine_referenz(auftraege)
    assert p.cash == pytest.approx(cash)
    assert p.positionen == pytest.approx(positionen)

    mit_kosten = Portfolio(cash=1_000.0, kosten_bp=10.0)
    mit_kosten.kaufe("A", 5.0, 100.0)
    assert mit_kosten.cash == pytest.approx(1_000.0 - 500.0 * 1.001)


def test_fehlender_kurs_warning_kein_trade(caplog):
    from core.sim.stimmen_sim import StimmenSimStrategie, StimmenStore, simuliere

    kauf = {LSTM: (0.9, 1.0, False)}
    store = StimmenStore({("A", "2026-01-02"): kauf, ("B", "2026-01-02"): kauf})
    kurse = {"2026-01-02": {"A": 100.0}}
    strategie = StimmenSimStrategie(name="s", pacing_aktiv=False)

    with caplog.at_level(logging.WARNING, logger="core.sim.stimmen_sim"):
        r = simuliere(store, kurse, strategie, "2026-01-02", "2026-01-02")

    assert {o["symbol"] for o in r.orders} == {"A"}
    meldungen = [
        rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING
    ]
    assert any("B" in m and "2026-01-02" in m for m in meldungen)
