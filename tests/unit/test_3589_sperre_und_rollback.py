"""#3589 (ARC-E1.6b) — Die Sperre bei Befund, und was beim Zurückschalten liegen bleibt.

Zwei Owner-Entscheide vom 25.09.2026:

**A3 — die Sperre unterscheidet geheilte von offenen Befunden.** Die Sperrwirkung des
Abgleichs (`RECONCILIATION_BLOCK_ON_BREAK`) haelt neue EINSTIEGE an, bis ein Mensch sie
aufhebt; Schutz-Exits und Notfallpfade gehen immer hinaus (ADR-R04). Gebaut war sie so,
dass **jeder** Befund sperrt — auch ein ``missing_fill`` mit dem Vermerk „nachgetragen",
also eine Abweichung, die derselbe Lauf gerade selbst geheilt hat. Gemessen am 23.09. am
Paper-Konto: Ein einziger Verbindungsabriss erzeugte zwei solche Befunde. Mit der Sperre
haette der Handel danach stillgestanden, bis jemand klickt — ausgeloest durch ein
Ereignis, das die Buecher bereits in Ordnung gebracht hat.

Ein geheilter Befund wird weiter **gemeldet**; er sperrt nur nicht. Bleibt nach dem
Nachtragen wirklich etwas offen, erzeugt derselbe Lauf dafuer einen eigenen Befund
(``position_mismatch``) — die Unterscheidung verliert also nichts.

**B3 — beim Zurueckschalten bleiben liegende Stops liegen, werden aber genannt.**
``BROKER_STOPS_ENABLED=false`` schaltet die PFLEGE ab, nicht den SCHUTZ: Was beim Broker
liegt, liegt weiter. Das war bisher unsichtbar (die Pflege kehrte stumm zurueck). Jetzt
nennt sie beim ersten Lauf, was liegen bleibt — abgeraeumt wird nur auf ausdruecklichen
Befehl.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = [pytest.mark.vc2, pytest.mark.mutates_global_state]


# ── A3: geheilt sperrt nicht, offen sperrt ───────────────────────────────────


def _dienst(block: bool = True):
    from core.reconciliation import ReconciliationService

    api = MagicMock()
    api.get_orders = MagicMock(return_value=[])
    api.get_all_positions = MagicMock(return_value=[])
    dienst = ReconciliationService(api=api, redis_client=MagicMock())
    dienst._block_on_break = block
    dienst.reconciled_once = True
    return dienst


def _record(*breaks):
    from datetime import datetime, timezone

    from core.contracts import ReconciliationRecord

    jetzt = datetime.now(timezone.utc)
    return ReconciliationRecord(
        run_id="lauf-3589",
        started_at=jetzt,
        finished_at=jetzt,
        breaks=tuple(breaks),
    )


def _befund(kind: str, geheilt: bool = False):
    from core.contracts import ReconciliationBreak

    return ReconciliationBreak(
        kind=kind,
        symbol="SPY",
        broker_side="gefuellt 1.0 @ 769.4",
        engine_side="kein FillEvent",
        detail="nachgetragen" if geheilt else "",
        geheilt=geheilt,
    )


def test_ein_nachgetragener_fill_sperrt_keine_einstiege():
    """Der Vorfall vom 23.09.: zwei geheilte Befunde haetten den Handel angehalten."""
    dienst = _dienst(block=True)
    dienst._report(_record(_befund("missing_fill", geheilt=True)))
    assert dienst.entries_blocked is False
    assert dienst.blocks("entry") is False


def test_ein_offener_befund_sperrt_einstiege():
    dienst = _dienst(block=True)
    dienst._report(_record(_befund("position_mismatch")))
    assert dienst.entries_blocked is True
    assert dienst.blocks("entry") is True


def test_ein_offener_befund_neben_einem_geheilten_sperrt():
    dienst = _dienst(block=True)
    dienst._report(
        _record(_befund("missing_fill", geheilt=True), _befund("orphaned_order"))
    )
    assert dienst.entries_blocked is True


def test_ein_schutz_exit_geht_auch_bei_sperre_hinaus():
    dienst = _dienst(block=True)
    dienst._report(_record(_befund("unknown_position")))
    assert dienst.entries_blocked is True
    for art in ("stop", "trim", "displacement", "panic", "breaker", "strategy_switch"):
        assert dienst.blocks(art) is False, art


def test_ein_geheilter_befund_wird_trotzdem_gemeldet():
    """Nicht sperren heisst nicht verschweigen."""
    from core import reconciliation

    dienst = _dienst(block=True)
    # Am Modul-Logger, nicht ueber caplog: Die Engine richtet das Logging beim Import
    # neu ein und entfernt dabei den Auffang-Handler von pytest.
    with patch.object(reconciliation.logger, "error") as alarm:
        dienst._report(_record(_befund("missing_fill", geheilt=True)))
    gemeldet = " ".join(str(a) for ruf in alarm.call_args_list for a in ruf.args)
    assert "missing_fill" in gemeldet, gemeldet
    assert "SPY" in gemeldet, gemeldet


def test_der_periodische_lauf_kennzeichnet_den_nachtrag_als_geheilt():
    """Der Weg von der Fundstelle bis zum Datensatz — nicht nur das Datenfeld."""
    from core.reconciliation import ReconciliationService

    order = MagicMock()
    order.id = "ord-3589"
    order.symbol = "SPY"
    order.side = "buy"
    order.status = "filled"
    order.filled_qty = "1"
    order.filled_avg_price = "769.4"
    order.client_order_id = "aaa-3589"

    api = MagicMock()
    api.get_orders = MagicMock(return_value=[order])
    api.get_all_positions = MagicMock(return_value=[])
    dienst = ReconciliationService(api=api, redis_client=MagicMock())
    dienst._internal_order_ids = {"ord-3589"}
    # Der ERSTE Lauf uebernimmt den Bestand, statt ihn zu melden — gemessen am
    # 23.09. und dort als Messfehler erkannt. Hier ist der Bestand schon uebernommen.
    dienst._adopted = True

    befunde = dienst._compare({"orders": [order], "positions": []})
    nachtraege = [b for b in befunde if b.kind == "missing_fill"]
    assert nachtraege, [b.kind for b in befunde]
    assert all(b.geheilt for b in nachtraege)


# ── B3: was beim Zurueckschalten liegen bleibt, wird genannt ──────────────────


@pytest.mark.anyio
async def test_abgeschaltete_pflege_nennt_die_liegenden_stops(caplog, monkeypatch):
    """`BROKER_STOPS_ENABLED=false` raeumt nicht ab — es sagt jetzt aber, was liegt."""
    import config
    from core.engine.trading_loop import TradingLoopMixin

    stop = MagicMock()
    stop.id = "stop-1"
    stop.symbol = "AAPL"
    stop.stop_price = "180.0"
    stop.qty = "3"

    api = MagicMock()
    api.get_orders = MagicMock(return_value=[stop])
    # Kein MagicMock als Engine: Die Pflege ruft eine EIGENE Koroutine auf, und ein
    # MagicMock liefert dort keine — der Test pruefte sonst die Attrappe, nicht den Pfad.
    engine = type(
        "EngineStub",
        (),
        {
            "api": api,
            "_broker_stop_rollback_gemeldet": False,
            "_melde_liegende_stops_einmal": (
                TradingLoopMixin._melde_liegende_stops_einmal
            ),
        },
    )()

    monkeypatch.setattr(
        config.get_config(), "BROKER_STOPS_ENABLED", False, raising=False
    )

    # Nicht ueber caplog: Die Engine richtet das Logging beim Import neu ein und
    # entfernt dabei den Auffang-Handler von pytest — der Test pruefte dann eine leere
    # Zeichenkette und waere immer gruen.
    with patch.object(logging, "warning") as meldung:
        await TradingLoopMixin._maintain_broker_stops(engine)

    gemeldet = " ".join(str(a) for ruf in meldung.call_args_list for a in ruf.args)
    assert "BROKER_STOPS_ENABLED=false" in gemeldet, gemeldet
    assert "AAPL" in gemeldet and "stop-1" in gemeldet, gemeldet
    # Nichts wird storniert — die Pflege ist aus, der Schutz bleibt.
    api.cancel_order_by_id.assert_not_called()
