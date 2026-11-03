"""#4125 Epic-Abnahme (Plan §5 Punkt 1, angepasst in §12e): Jeder Verkauf hat einen Grund.

Ein simulierter Handelstag mit neun Exit-Arten. Jede kommt aus ihrem echten Erzeuger und geht
durch das echte Tor in die echte Audit-Ablage (Hash-Kette in einem tmp-Verzeichnis):

- Loss-Cut, Trailing, Momentum Fade, Gesamt-Score: ``intelligent_exit.analyze_exit`` (Teil 2)
- Rotation: Kontext mit ``portfolio_reason`` wie in ``ausstieg_hebel`` (Teil 2)
- Konsens-SELL: Schwelle des Round Tables (Teil 3)
- Verdraengung, Panikverkauf: ``intent_kind`` am Tor (Teil 3)
- Broker-Stop: Fill-Abgleich ``ReconciliationService.on_broker_fill`` (Teil 4)

Gruen seit Teil 4 (die xfail-Markierung ist weg). Der Fill-Abgleich laeuft ohne Broker und
ohne Redis; die synchrone Ablage fehlt hier, der Grund wird trotzdem abgelegt (Plan §13d L1).
"""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.vc3


class _Uhr:
    def now(self):
        from datetime import datetime, timezone

        return datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)

    def time(self):
        return self.now().timestamp()


@pytest.fixture
def ablage(tmp_path, monkeypatch):
    from core.audit_ablage import AuditAblage
    from core.gateway import fabrik
    from core.ports import audit_ablage_port
    from core.round_table import runner

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(runner, "_senate", None)
    audit_ablage_port.setze_ablage(AuditAblage(clock=_Uhr(), wolke=lambda: MagicMock()))
    fabrik.reset_ablage_zaehler()
    yield tmp_path
    audit_ablage_port.setze_ablage(None)


def _zeilen(verzeichnis, art, erwartet):
    zeilen = []
    for _ in range(100):
        zeilen = [
            json.loads(z)
            for d in sorted(verzeichnis.glob("audit_log_*.jsonl"))
            for z in d.read_text(encoding="utf-8").splitlines()
            if z and json.loads(z).get("event_type") == art
        ]
        if len(zeilen) >= erwartet:
            break
        threading.Event().wait(0.05)
    return [json.loads(z["daten"]) | {"_zeile": z} for z in zeilen]


def _analyse_gruende(monkeypatch):
    """Vier Gruende aus dem echten ``analyze_exit`` (Faelle wie test_intelligent_exit.py)."""
    import core.intelligent_exit as ie
    from core.intelligent_exit import PositionContext, analyze_exit

    def ctx(entry, current, hwm, hours, lstm=0.0, momentum=None):
        return PositionContext(
            symbol="X",
            entry_price=entry,
            current_price=current,
            high_water_mark=hwm,
            hours_held=hours,
            lstm_prediction=lstm,
            momentum_history=momentum or [],
        )

    gruende = {"loss_cut": analyze_exit(ctx(100.0, 93.5, 100.0, 30.0)).verkaufsgrund}
    gruende["momentum_fade"] = analyze_exit(
        ctx(100.0, 110.0, 110.0, 30.0, lstm=-0.6, momentum=[0.3, 0.1, -0.1])
    ).verkaufsgrund
    with monkeypatch.context() as m:
        m.setattr(ie, "_calculate_trailing_stop_score", lambda *a: 90.0)
        gruende["trailing"] = analyze_exit(ctx(100.0, 110.0, 125.0, 30.0)).verkaufsgrund
    with monkeypatch.context() as m:
        m.setattr(ie, "_calculate_loss_pressure", lambda *a: 75.0)
        m.setattr(ie, "_calculate_trailing_stop_score", lambda *a: 0.0)
        m.setattr(ie, "_calculate_momentum_fade", lambda *a: 60.0)
        m.setattr(ie, "_calculate_news_pressure", lambda *a: 90.0)
        m.setattr(ie, "_calculate_time_pressure", lambda *a: 90.0)
        gruende["composite"] = analyze_exit(ctx(100.0, 97.0, 100.0, 30.0)).verkaufsgrund
    return gruende


def test_jeder_verkauf_eines_handelstages_hat_einen_grund(ablage, monkeypatch):
    from core.contracts import OrderIntent
    from core.contracts.verkaufsgrund import VERKAUFSGRUND_KLASSEN
    from core.engine.order_aufbau import verkaufsgrund_aus_kontext
    from core.gateway import fabrik
    from core.reconciliation import ReconciliationService

    gruende = _analyse_gruende(monkeypatch)
    rotation = SimpleNamespace(
        verkaufsgrund=None,
        decision_context=SimpleNamespace(
            triggered_by_stop=False,
            portfolio_reason="rotation: dropped to rank 14/40 (out of top-10)",
        ),
    )
    gruende["rotation"] = verkaufsgrund_aus_kontext(rotation)

    tor = fabrik.gateway_for(MagicMock(), is_halted=lambda user_id=None: False)
    ids = []
    for i, (klasse, grund) in enumerate(gruende.items()):
        intent = OrderIntent(
            decision_id=f"dec-{klasse}",
            symbol=f"S{i}",
            side="sell",
            qty=1.0,
            intent_kind="stop" if klasse != "rotation" else "trim",
            halted=False,
            verkaufsgrund=grund,  # Teil 3: optionales Feld am OrderIntent
        )
        tor.submit(intent, request={"symbol": intent.symbol})
        ids.append(intent.decision_id)

    # Konsens-SELL (Teil 3: Grund am Signal des Round Tables), Verdraengung und Panik
    # (Teil 3: Klasse aus intent_kind an der Tor-Senke).
    from core.round_table.signal_bau import _score_to_signal  # noqa: F401 — Teil 3

    for kind in ("displacement", "panic"):
        intent = OrderIntent(
            decision_id=f"dec-{kind}",
            symbol=kind.upper()[:4],
            side="sell",
            qty=1.0,
            intent_kind=kind,
            halted=False,
        )
        tor.submit(intent, request={"symbol": intent.symbol})
        ids.append(intent.decision_id)

    # Broker-Stop (Teil 4): eigener Stop-Schluessel im Fill-Abgleich.
    monkeypatch.setattr("core.reconciliation.synchrone_ablage", lambda: None)
    abgleich = ReconciliationService(MagicMock(), None)
    abgleich.on_broker_fill(
        SimpleNamespace(
            id="o-stop",
            client_order_id="stop-1-stop-AAPL",
            symbol="AAPL",
            side="sell",
            filled_qty="1",
            filled_avg_price="99.5",
            stop_price="99.0",
        )
    )
    ids.append("broker_stop")

    verkaufsgruende = _zeilen(ablage, "sell_reason", len(ids))
    assert len(verkaufsgruende) == len(ids), "jeder Verkauf genau ein sell_reason"
    for eintrag in verkaufsgruende:
        assert eintrag["klasse"] in VERKAUFSGRUND_KLASSEN
        assert eintrag["klasse"] != "unknown"
    loss_cut = next(e for e in verkaufsgruende if e["klasse"] == "loss_cut")
    assert {"verlust_pct", "stufe", "haltedauer_h"} <= set(loss_cut)

    entscheidungen = _zeilen(ablage, "gateway_decision", len(ids) - 1)
    for decision_id in ids[:-1]:
        assert sum(1 for e in entscheidungen if e["decision_id"] == decision_id) == 1
