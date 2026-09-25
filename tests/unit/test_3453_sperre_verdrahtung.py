"""#3453 — die Engine-Sperre wird verdrahtet (Plan: docs/3453-sperre-verdrahtung, PR #3465).

``EngineLease`` (#3390) hatte keinen Produktionsaufrufer. Jetzt:

* **Vor jeder Order** fragt ``_sende_durchs_tor``, ob diese Instanz die Schreibberechtigung fuer
  das Konto haelt. Wenn nicht: **ein** Versuch zu erwerben; bleibt er erfolglos, wird die Order
  zurueckgehalten — auch ein Schutz-Exit (Issue, Schritt 6) — und der Aufruf kehrt zurueck.
* **Erneuert** wird nur bei Fortschritt, auf dem Ring, der die Berechtigung haelt.
* **Stopp** gibt frei.

Die Ablage ist die echte des Desktops (SQLite unter ``AAA_USER_DATA_DIR``) — eine Sperre gegen
eine Attrappe prueft nichts.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from core.exceptions import TradingHaltedError

pytestmark = [pytest.mark.unit, pytest.mark.vc3]


@pytest.fixture
async def ablage(monkeypatch, tmp_path):
    from core import lease
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    lease._vergessen_fuer_tests()
    yield tmp_path
    lease._vergessen_fuer_tests()
    await zusammenbau.schliesse_alle()


@pytest.fixture
def tor_offen(monkeypatch):
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)


async def _fremde_instanz_haelt(konto: str):
    """Eine andere Instanz (eigener Port, eigenes Token) haelt die Berechtigung."""
    from core.lease import EngineLease
    from core.state import SqliteStateAdapter, zusammenbau

    port = SqliteStateAdapter(zusammenbau.ablage_pfad())
    fremd = EngineLease(port, konto=konto, instanz="andere-instanz")
    assert await fremd.erwerben()
    return fremd, port


async def _sende(client, *, user_id="global", **mehr):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from core.engine.order_executor import OrderExecutorMixin

    anfrage = MarketOrderRequest(
        symbol="AAPL",
        qty=1,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        client_order_id="stop-0-entscheidung-9",
    )
    argumente = dict(
        client=client,
        request=anfrage,
        symbol="AAPL",
        side_enum=OrderSide.SELL,
        qty=1.0,
        user_id=user_id,
        decision_id="entscheidung-9",
    )
    argumente.update(mehr)
    return await OrderExecutorMixin._sende_durchs_tor(**argumente)


def _konto(user_id="global"):
    from config import get_config
    from core.lease import konto_schluessel

    return konto_schluessel(user_id, paper=bool(get_config().PAPER_TRADING))


# ---------------------------------------------------------------------------
# Vor jeder Order
# ---------------------------------------------------------------------------


async def test_ohne_berechtigung_wird_auch_ein_schutz_exit_zurueckgehalten(
    ablage, tor_offen, caplog
) -> None:
    import logging

    fremd, port = await _fremde_instanz_haelt(_konto())
    client = MagicMock()
    try:
        with caplog.at_level(logging.WARNING):
            with pytest.raises(TradingHaltedError, match="Schreibberechtigung"):
                await _sende(client, is_protective_exit=True)
    finally:
        await fremd.freigeben()
        await port.aclose()

    client.submit_order.assert_not_called()
    assert any(
        r.levelno == logging.WARNING and "Schreibberechtigung" in r.getMessage()
        for r in caplog.records
    )


async def test_ohne_berechtigung_entsteht_kein_offener_intent(
    ablage, tor_offen
) -> None:
    """Zurueckgehalten wird VOR dem Festschreiben — sonst faende der Neustart einen offenen
    Intent zu einer Order, die nie hinausging."""
    from core.outbox import Outbox
    from core.state.zusammenbau import state_port

    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        with pytest.raises(Exception):
            await _sende(MagicMock())
    finally:
        await fremd.freigeben()
        await port.aclose()

    assert await Outbox(await state_port()).lese("stop-0-entscheidung-9") is None


async def test_ist_die_berechtigung_frei_erwirbt_die_absendestelle_sie(
    ablage, tor_offen
) -> None:
    """Der Gegentest aus dem Plan (Schritt 6): Die Absendestelle sperrt selbst — ohne dass
    jemand den Haken der Vorrichtung gefragt hat."""
    from core import lease

    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    await _sende(client)

    client.submit_order.assert_called_once()
    eintrag = lease.eintrag(_konto())
    assert eintrag is not None and await eintrag.lease.haelt_noch()


async def test_eine_verlorene_berechtigung_wird_vor_der_order_neu_erworben(
    ablage, tor_offen
) -> None:
    from core import lease

    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")
    await _sende(client)
    alt = lease.eintrag(_konto()).lease
    await alt.freigeben()  # abgelaufen oder freigegeben — niemand hat uebernommen

    await _sende(client)

    assert client.submit_order.call_count == 2
    assert await lease.eintrag(_konto()).lease.haelt_noch()


async def test_der_aufruf_kehrt_zurueck_statt_zu_schleifen(ablage, tor_offen) -> None:
    """Genau ein Erwerbsversuch — kein Warten, bis die andere Instanz loslaesst."""
    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        with pytest.raises(Exception):
            await asyncio.wait_for(_sende(MagicMock()), timeout=5)
    finally:
        await fremd.freigeben()
        await port.aclose()


async def test_mit_abgeschaltetem_flag_wird_nicht_geprueft(
    ablage, tor_offen, monkeypatch
) -> None:
    import config

    echt = config.get_config

    class _Aus:
        def __getattr__(self, name):
            if name == "ENGINE_LEASE_ENABLED":
                return False
            return getattr(echt(), name)

    monkeypatch.setattr(config, "get_config", lambda: _Aus())
    fremd, port = await _fremde_instanz_haelt(_konto())
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")
    try:
        await _sende(client)
    finally:
        await fremd.freigeben()
        await port.aclose()

    client.submit_order.assert_called_once()


async def test_ohne_gemeinsame_ablage_bleibt_die_sperre_aus(
    monkeypatch, tor_offen, tmp_path
) -> None:
    """Weder AAA_USER_DATA_DIR noch REDIS_URL: Es gibt keine Ablage, auf der eine zweite
    Instanz sperren koennte. Eine Datei relativ zum Arbeitsverzeichnis ist keine (#3468).
    """
    from core import lease

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    lease._vergessen_fuer_tests()
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    await _sende(client)

    client.submit_order.assert_called_once()
    assert lease.eintrag(_konto()) is None


async def test_ist_die_berechtigung_unlesbar_wird_zurueckgehalten(
    ablage, tor_offen, monkeypatch
) -> None:
    """Unlesbar heisst: nicht belegt, dass diese Instanz schreibt. Die Sperre ist eine
    Schutzvorrichtung wie der Halt — im Zweifel wird nicht gehandelt."""
    from core import lease

    async def _kaputt(*_a, **_k):
        raise OSError("database is locked")

    monkeypatch.setattr(lease, "_erwirb_auf_diesem_ring", _kaputt)
    client = MagicMock()

    with pytest.raises(TradingHaltedError, match="Schreibberechtigung"):
        await _sende(client)

    client.submit_order.assert_not_called()


# ---------------------------------------------------------------------------
# Eine Berechtigung auf einem anderen Ring
# ---------------------------------------------------------------------------


async def test_eine_berechtigung_auf_einem_anderen_ring_wird_dort_geprueft(
    ablage, tor_offen
) -> None:
    """Die Berechtigung lebt auf dem Ring, der sie erworben hat (die SQLite-Verbindung ist
    ringgebunden). Sendet ein anderer Ring, wird auf ihrem geprueft — nicht mit einem zweiten
    Token neu erworben, das gegen die eigene Berechtigung verloere."""
    from core import lease

    ring = asyncio.new_event_loop()
    faden = threading.Thread(target=ring.run_forever, daemon=True)
    faden.start()
    try:
        erhalten = asyncio.run_coroutine_threadsafe(
            lease.sichere_berechtigung(_konto(), instanz="ring-a"), ring
        ).result(timeout=10)
        assert erhalten

        client = MagicMock()
        client.submit_order.return_value = MagicMock(id="brk-1")
        await _sende(client)

        client.submit_order.assert_called_once()
        assert lease.eintrag(_konto()).ring is ring
    finally:
        asyncio.run_coroutine_threadsafe(lease.beende_auf_diesem_ring(), ring).result(
            timeout=10
        )
        ring.call_soon_threadsafe(ring.stop)
        faden.join(timeout=5)
        ring.close()


# ---------------------------------------------------------------------------
# Erneuern nur bei Fortschritt, Stopp gibt frei
# ---------------------------------------------------------------------------


async def test_ohne_fortschritt_wird_nicht_erneuert(ablage) -> None:
    from core import lease

    assert await lease.sichere_berechtigung(_konto(), instanz="a")
    eintrag = lease.eintrag(_konto())
    eintrag.lease._sperre.renew = MagicMock(side_effect=AssertionError("erneuert"))

    erneuert = await lease.erneuere_auf_diesem_ring(fortschritt=lambda: False)

    assert erneuert == 0


async def test_mit_fortschritt_wird_erneuert(ablage) -> None:
    from core import lease

    assert await lease.sichere_berechtigung(_konto(), instanz="a")

    assert await lease.erneuere_auf_diesem_ring(fortschritt=lambda: True) == 1


async def test_der_stopp_gibt_frei(ablage) -> None:
    from core import lease

    assert await lease.sichere_berechtigung(_konto(), instanz="a")
    fremd, port = None, None
    try:
        await lease.beende_auf_diesem_ring()
        assert lease.eintrag(_konto()) is None
        fremd, port = await _fremde_instanz_haelt(_konto())  # sofort frei
    finally:
        if fremd is not None:
            await fremd.freigeben()
            await port.aclose()


def test_die_schleife_erwirbt_beim_start_und_gibt_im_wrapper_frei() -> None:
    """Verdrahtung, nicht nur Bausteine: Start-Erwerb vor dem ersten Zyklus, Erwerb bei jedem
    Zyklusbeginn, Erneuerungs-Aufgabe, Freigabe im finally des Ring-Wrappers."""
    import inspect

    from core.engine.trading_loop import TradingLoopMixin

    schleife = inspect.getsource(TradingLoopMixin.live_trading_loop)
    wrapper = inspect.getsource(TradingLoopMixin.run_strategy_async_wrapper)

    assert schleife.index("_sichere_schreibberechtigung()") < schleife.index(
        "while self.strategy_running.is_set()"
    )
    assert schleife.count("_sichere_schreibberechtigung()") >= 2
    assert "_starte_lease_erneuerung()" in schleife
    assert "beende_auf_diesem_ring" in wrapper


# ---------------------------------------------------------------------------
# Haken fuer die Kettenabnahme, Flag
# ---------------------------------------------------------------------------


def test_der_haken_sagt_nein_solange_eine_andere_instanz_schreibt(
    monkeypatch, tmp_path
) -> None:
    from core import lease
    from core.engine.lease import erwerbe_schreibberechtigung

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    lease._vergessen_fuer_tests()
    konto = _konto("kette")

    async def _fremd():
        return await _fremde_instanz_haelt(konto)

    fremd, port = asyncio.run(_fremd())
    try:
        assert erwerbe_schreibberechtigung(konto="kette", instanz="b") is False

        async def _los():
            await fremd.freigeben()
            await port.aclose()

        asyncio.run(_los())
        assert erwerbe_schreibberechtigung(konto="kette", instanz="b") is True
    finally:
        lease._vergessen_fuer_tests(freigeben=True)


def test_der_haken_haelt_die_berechtigung_ueber_den_aufruf_hinaus(
    monkeypatch, tmp_path
) -> None:
    """Nach dem synchronen Aufruf haelt die Instanz die Berechtigung weiter — eine andere
    kommt nicht an sie heran."""
    from core import lease
    from core.engine.lease import erwerbe_schreibberechtigung

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    lease._vergessen_fuer_tests()
    try:
        assert erwerbe_schreibberechtigung(konto="kette", instanz="a") is True

        async def _versuch():
            from core.lease import EngineLease
            from core.state import SqliteStateAdapter, zusammenbau

            port = SqliteStateAdapter(zusammenbau.ablage_pfad())
            try:
                return await EngineLease(
                    port, konto=_konto("kette"), instanz="b"
                ).erwerben()
            finally:
                await port.aclose()

        assert asyncio.run(_versuch()) is False
    finally:
        lease._vergessen_fuer_tests(freigeben=True)


# ---------------------------------------------------------------------------
# Owner-Entscheid 18.09.: tote Halter auf demselben Rechner werden uebernommen
# ---------------------------------------------------------------------------


def _toter_prozess() -> int:
    """Die PID eines Prozesses, der sicher beendet ist."""
    import subprocess
    import sys

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


async def _halter_umschreiben(port, konto: str, **aenderung) -> None:
    import json

    from core.lease import HALTER_PRAEFIX

    roh = await port.get(HALTER_PRAEFIX + konto)
    satz = json.loads(roh)
    satz.update(aenderung)
    await port.set(HALTER_PRAEFIX + konto, json.dumps(satz), ttl_seconds=60)


async def test_ein_toter_halter_auf_diesem_rechner_wird_sofort_uebernommen(
    ablage,
) -> None:
    """Absturz und sofortiger Neustart: Die alte Instanz haelt die Berechtigung noch bis zu
    60 s. Ist sie auf DIESEM Rechner nachweislich tot, uebernimmt der Neustart sofort — sonst
    handelte der Desktop nach jedem Absturz eine Minute nicht, auch keinen Stop-Loss."""
    from core.lease import EngineLease

    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        await _halter_umschreiben(port, _konto(), pid=_toter_prozess())
        neu = EngineLease(port, konto=_konto(), instanz="neustart")

        assert await neu.erwerben() is True
        assert await fremd.haelt_noch() is False
        await neu.freigeben()
    finally:
        await port.aclose()


async def test_ein_lebender_halter_wird_nicht_uebernommen(ablage) -> None:
    """Zwei gleichzeitig laufende Apps auf einem Rechner: der Fall, gegen den die Sperre da ist."""
    from core.lease import EngineLease

    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        neu = EngineLease(port, konto=_konto(), instanz="zweite-app")
        assert await neu.erwerben() is False
        assert await fremd.haelt_noch() is True
    finally:
        await fremd.freigeben()
        await port.aclose()


async def test_ein_halter_auf_einem_anderen_rechner_wird_nicht_uebernommen(
    ablage,
) -> None:
    """Enterprise: Ob ein Prozess auf einem anderen Rechner lebt, laesst sich von hier nicht
    pruefen. Dort gilt weiter die Frist."""
    from core.lease import EngineLease

    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        await _halter_umschreiben(
            port, _konto(), host="anderer-rechner", pid=_toter_prozess()
        )
        neu = EngineLease(port, konto=_konto(), instanz="neu")
        assert await neu.erwerben() is False
    finally:
        await fremd.freigeben()
        await port.aclose()


async def test_ohne_halter_vermerk_wird_die_frist_abgewartet(ablage) -> None:
    from core.lease import HALTER_PRAEFIX, EngineLease

    fremd, port = await _fremde_instanz_haelt(_konto())
    try:
        await port.delete(HALTER_PRAEFIX + _konto())
        neu = EngineLease(port, konto=_konto(), instanz="neu")
        assert await neu.erwerben() is False
    finally:
        await fremd.freigeben()
        await port.aclose()


async def test_ein_gleichzeitiger_wal_wechsel_wird_wiederholt(
    tmp_path, monkeypatch
) -> None:
    """Gemessen an der Zwei-Instanzen-Vorrichtung (2 von 20 Laeufen): Oeffnen zwei Prozesse
    eine frische Ablage gleichzeitig, wollen beide auf WAL umstellen. SQLite loest diese
    Verklemmung, indem es EINEM sofort „database is locked" antwortet — ohne Wartezeit. Die
    Instanz stuerzte ab. Der Wechsel ist dauerhaft; ein zweiter Versuch findet ihn erledigt.
    """
    import sqlite3

    import aiosqlite

    from core.state import SqliteStateAdapter

    echt = aiosqlite.Connection.execute
    versuche = {"n": 0}

    def _einmal_gesperrt(self, sql, *args, **kwargs):
        if "journal_mode" in sql and versuche["n"] == 0:
            versuche["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return echt(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", _einmal_gesperrt)
    adapter = SqliteStateAdapter(tmp_path / "engine_state.db")
    try:
        await adapter.set("probe", "1")
        assert await adapter.get("probe") == "1"
    finally:
        await adapter.aclose()
    assert versuche["n"] == 1


async def test_ein_gleichzeitiges_schema_anlegen_wird_wiederholt(
    tmp_path, monkeypatch
) -> None:
    """Gemessen (Lauf 11 von 60): Nach dem WAL-Wechsel traf es das Anlegen des Schemas.
    ``CREATE TABLE IF NOT EXISTS`` liest erst und schreibt dann; hat der andere Prozess
    dazwischen geschrieben, antwortet SQLite sofort „database is locked"."""
    import sqlite3

    import aiosqlite

    from core.state import SqliteStateAdapter

    echt = aiosqlite.Connection.executescript
    versuche = {"n": 0}

    def _einmal_gesperrt(self, sql, *args, **kwargs):
        if versuche["n"] == 0:
            versuche["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return echt(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "executescript", _einmal_gesperrt)
    adapter = SqliteStateAdapter(tmp_path / "engine_state.db")
    try:
        await adapter.set("probe", "1")
        assert await adapter.get("probe") == "1"
    finally:
        await adapter.aclose()


async def test_eine_dauerhaft_gesperrte_ablage_meldet_den_fehler(
    tmp_path, monkeypatch
) -> None:
    """Die Wiederholung ist begrenzt — eine wirklich gesperrte Ablage bleibt ein Fehler."""
    import sqlite3

    import aiosqlite

    from core.state import SqliteStateAdapter
    from core.state import sqlite_adapter as sa

    echt = aiosqlite.Connection.execute

    def _immer_gesperrt(self, sql, *args, **kwargs):
        if "journal_mode" in sql:
            raise sqlite3.OperationalError("database is locked")
        return echt(self, sql, *args, **kwargs)

    monkeypatch.setattr(aiosqlite.Connection, "execute", _immer_gesperrt)
    monkeypatch.setattr(sa, "_WAL_PAUSE_S", 0.001)
    adapter = SqliteStateAdapter(tmp_path / "engine_state.db")
    try:
        with pytest.raises(sqlite3.OperationalError):
            await adapter.get("probe")
    finally:
        await adapter.aclose()


def test_das_flag_steht_in_beiden_editionen_mit_demselben_default() -> None:
    import re
    from pathlib import Path

    wurzel = Path(__file__).resolve().parents[2]
    werte = []
    for datei in ("settings.py",):
        text = (wurzel / datei).read_text(encoding="utf-8")
        treffer = re.search(
            r'ENGINE_LEASE_ENABLED.*?getenv\(\s*"ENGINE_LEASE_ENABLED",\s*"(\w+)"',
            text,
            re.S,
        )
        assert treffer, f"ENGINE_LEASE_ENABLED fehlt in {datei}"
        werte.append(treffer.group(1))
    assert werte == ["True"]
