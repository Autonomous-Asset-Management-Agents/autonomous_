"""Marktdaten und Konto liegen in ``core/engine/marktdaten.py``, der Kern erbt sie.

#4249 (H-2h), Teil von ARC-E6 (#3738). Reiner Umzug der fuenf Methoden fuer Snapshot-Abruf,
Specialist-Abdeckung, Closed-Report-Pass, Breaker-Feed und Bar-Cache aus
``core/engine/trading_loop.py`` nach ``MarktdatenMixin`` (Schnitt-Entscheidung #4184 §2). Die
Patch-Ziele der Tests liegen weiter am Kern; das Modul liest sie zur Laufzeit als
``_tl.<name>`` (§3). ``config`` bleibt das direkte Modulobjekt, weil Tests
``config.get_config`` und ``config.ALPACA_DATA_FEED`` patchen.
Plan: ``docs/4249-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

METHODEN = {
    "_fetch_snapshots_chunked",
    "_reconcile_specialist_coverage",
    "_run_closed_report_pass",
    "_update_live_account_equity",
    "_warm_lstm_bar_cache",
}


def test_marktdaten_liegen_in_marktdaten():
    from core.engine.marktdaten import MarktdatenMixin
    from core.engine.trading_loop import TradingLoopMixin

    assert METHODEN <= set(vars(MarktdatenMixin))
    assert not METHODEN & set(vars(TradingLoopMixin))
    assert issubclass(TradingLoopMixin, MarktdatenMixin)


class _Reg:
    def __init__(self):
        self._symbols = ["AAPL"]
        self.priority = None

    def add_symbol(self, s):
        pass

    def update_priority(self, syms):
        self.priority = list(syms)


async def test_specialist_abdeckung_liest_config_modul(monkeypatch):
    import config
    from core.engine.trading_loop import TradingLoopMixin

    an = types.SimpleNamespace(
        SPECIALIST_COVERAGE_DYNAMIC=True,
        SPECIALIST_TOP_N_CONVICTION=2,
        SPECIALIST_COVER_POSITIONS=False,
    )
    aus = types.SimpleNamespace(SPECIALIST_COVERAGE_DYNAMIC=False)
    monkeypatch.setattr(config, "get_config", lambda: an)
    monkeypatch.setattr("core.engine.trading_loop.get_config", lambda: aus)

    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.specialist_registry = _Reg()
    eng.api = None
    eng._last_round_table_state = []
    await eng._reconcile_specialist_coverage(base_watchlist=["AAPL"])
    assert eng.specialist_registry.priority is not None


async def test_snapshot_chunks_folgen_kern_patches():
    from core.engine.trading_loop import TradingLoopMixin

    anfragen = []

    def anfrage(**kw):
        anfragen.append(kw["symbol_or_symbols"])
        return kw

    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.data_api = types.SimpleNamespace(
        get_stock_snapshot=lambda req: {s: object() for s in req["symbol_or_symbols"]}
    )
    with patch(
        "core.engine.trading_loop.get_config",
        return_value=types.SimpleNamespace(SNAPSHOT_FETCH_CHUNK_SIZE=2),
    ), patch("core.engine.trading_loop.StockSnapshotRequest", side_effect=anfrage):
        ergebnis = await eng._fetch_snapshots_chunked(["A", "B", "C", "D", "E"])
    assert len(anfragen) == 3
    assert set(ergebnis) == {"A", "B", "C", "D", "E"}


async def test_breaker_feed_ohne_unlock():
    from core.engine.trading_loop import TradingLoopMixin

    rm = MagicMock()
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.api = MagicMock()
    eng.api.get_account.return_value = MagicMock(equity=123_456.0)
    await eng._update_live_account_equity(types.SimpleNamespace(risk_manager=rm))
    rm.update_account_equity.assert_called_once()
    _, kw = rm.update_account_equity.call_args
    assert kw == {"allow_unlock": False}
