"""#3449, Schritt 2 — die Outbox wird verdrahtet (Plan: docs/3449-outbox-verdrahtung, PR #3464).

Owner-Entscheide vom 18.09.2026, die diese Tests festhalten:

1. **Nie blind nachsenden.** Nach einem Neustart wird beim Broker abgeglichen: Kennt er den
   Schlüssel, ist der Intent bestätigt; kennt er ihn nicht, wird er verworfen. Gesendet wird
   dabei nichts.
2. **Flag ``ORDER_OUTBOX_ENABLED``**, Default an, beide Editionen gleich.
3. **Buchführung verhindert nie eine Order.** Scheitert die Outbox, geht die Order trotzdem
   hinaus — laut gemeldet.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from core.exceptions import TradingHaltedError

pytestmark = [pytest.mark.unit, pytest.mark.vc3]


@pytest.fixture
async def ablage(monkeypatch, tmp_path):
    """Die echte Ablage des Desktops: SQLite unter AAA_USER_DATA_DIR."""
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    yield tmp_path
    await zusammenbau.schliesse_alle()


@pytest.fixture
def tor_offen(monkeypatch):
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)


async def _outbox():
    from core.outbox import Outbox
    from core.state.zusammenbau import state_port

    return Outbox(await state_port())


def _anfrage(coid="entry-0-entscheidung-7"):
    return MagicMock(client_order_id=coid, symbol="AAPL")


async def _sende(client, request=None, **mehr):
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import OrderExecutorMixin

    argumente = dict(
        client=client,
        request=request or _anfrage(),
        symbol="AAPL",
        side_enum=OrderSide.BUY,
        qty=2.0,
        user_id="global",
        decision_id="entscheidung-7",
    )
    argumente.update(mehr)
    return await OrderExecutorMixin._sende_durchs_tor(**argumente)


# ---------------------------------------------------------------------------
# Festschreiben vor Absenden
# ---------------------------------------------------------------------------


async def test_der_intent_liegt_in_der_outbox_bevor_der_broker_ihn_sieht(
    ablage, tor_offen
) -> None:
    gesehen = {}

    class _Broker:
        def submit_order(self, request):
            import asyncio

            # Der Broker-Aufruf laeuft im Thread — dort gibt es keinen Ring. Gelesen wird
            # darum ueber einen eigenen Ring: dieselbe Datei, ein eigener Port.
            gesehen["eintrag"] = asyncio.run(_lies_im_eigenen_ring(request))
            return MagicMock(id="brk-1")

    await _sende(_Broker())

    eintrag = gesehen["eintrag"]
    assert (
        eintrag is not None
    ), "Der Broker sah die Order, bevor sie festgeschrieben war."
    assert eintrag.decision_id == "entscheidung-7"


async def _lies_im_eigenen_ring(request):
    from core.state import zusammenbau

    try:
        return await (await _outbox()).lese(request.client_order_id)
    finally:
        await zusammenbau.schliesse_alle()


async def test_nach_der_antwort_ist_der_intent_bestaetigt(ablage, tor_offen) -> None:
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-9")

    await _sende(client)

    eintrag = await (await _outbox()).lese("entry-0-entscheidung-7")
    assert (eintrag.zustand, eintrag.broker_order_id) == ("bestaetigt", "brk-9")


async def test_ein_brokerfehler_laesst_den_intent_abgesendet(ablage, tor_offen) -> None:
    """Bei einem Fehler weiss niemand, ob die Order ankam — das klaert der Abgleich."""
    client = MagicMock()
    client.submit_order.side_effect = TimeoutError("keine Antwort")

    with pytest.raises(TimeoutError):
        await _sende(client)

    eintrag = await (await _outbox()).lese("entry-0-entscheidung-7")
    assert eintrag.zustand == "abgesendet"


async def test_eine_tor_ablehnung_verwirft_den_intent(ablage, monkeypatch) -> None:
    from core.engine import order_executor as oe

    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: True
    )
    monkeypatch.setattr(oe, "_record_gateway_decision", lambda d: None)
    client = MagicMock()

    with pytest.raises(TradingHaltedError, match="TRADING HALTED"):
        await _sende(client)

    client.submit_order.assert_not_called()
    eintrag = await (await _outbox()).lese("entry-0-entscheidung-7")
    assert (
        eintrag.zustand == "verworfen"
    ), "Ein abgelehnter Intent darf nach dem Neustart nicht als offen gelten."


# ---------------------------------------------------------------------------
# Buchfuehrung verhindert nie eine Order (Owner-Entscheid 3)
# ---------------------------------------------------------------------------


async def test_scheitert_die_outbox_geht_die_order_trotzdem_hinaus(
    monkeypatch, tor_offen, caplog, tmp_path
) -> None:
    import core.state.zusammenbau as zusammenbau

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    import contextlib

    @contextlib.asynccontextmanager
    async def _kaputt():
        raise RuntimeError("Platte voll")
        yield  # pragma: no cover

    monkeypatch.setattr(zusammenbau, "kurzer_port", _kaputt)
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    with caplog.at_level(logging.ERROR):
        order = await _sende(client, is_protective_exit=True)

    assert order.id == "brk-1"
    client.submit_order.assert_called_once()
    assert any(
        r.levelno >= logging.ERROR and "Outbox" in r.getMessage()
        for r in caplog.records
    ), "Der Ausfall der Outbox wurde nicht laut gemeldet."


async def test_der_ausfall_der_outbox_behaelt_den_stacktrace(
    monkeypatch, tor_offen, caplog, tmp_path
) -> None:
    """Review #3476, FINDING-01: Ohne Stacktrace ist ein Ausfall im Kapitalpfad nicht zu
    untersuchen. Jede Fehlermeldung der Outbox traegt die Ausnahme mit."""
    import contextlib

    import core.state.zusammenbau as zusammenbau

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    @contextlib.asynccontextmanager
    async def _kaputt():
        raise RuntimeError("Platte voll")
        yield  # pragma: no cover

    monkeypatch.setattr(zusammenbau, "kurzer_port", _kaputt)
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    with caplog.at_level(logging.ERROR):
        await _sende(client)

    fehler = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and "Outbox" in r.getMessage()
    ]
    assert fehler and all(r.exc_info for r in fehler)


async def test_ein_fehlgeschlagener_schritt_behaelt_den_stacktrace(caplog) -> None:
    from core.engine.order_executor import _outbox_schritt

    async def _scheitert():
        raise OSError("Datentraeger voll")

    with caplog.at_level(logging.ERROR):
        await _outbox_schritt("Festschreiben", _scheitert())

    (satz,) = [r for r in caplog.records if "Outbox" in r.getMessage()]
    assert satz.exc_info and satz.exc_info[0] is OSError


async def test_die_ablage_erfaehrt_beim_schliessen_den_echten_fehler(
    monkeypatch, tmp_path
) -> None:
    """Review #3476, FINDING-02: Scheitert der Rumpf, bekommt das Schliessen der Ablage die
    Ausnahme mitgeteilt — nicht blind ``None, None, None``."""
    import contextlib

    import core.state.zusammenbau as zusammenbau
    from core.engine.order_executor import _outbox_sitzung

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    gesehen = {}

    @contextlib.asynccontextmanager
    async def _port():
        try:
            yield MagicMock()
        except BaseException as exc:
            gesehen["fehler"] = exc
            raise
        else:
            gesehen["fehler"] = None

    monkeypatch.setattr(zusammenbau, "kurzer_port", _port)

    with pytest.raises(TimeoutError):
        async with _outbox_sitzung():
            raise TimeoutError("keine Antwort")

    assert isinstance(gesehen["fehler"], TimeoutError)


async def test_scheitert_das_schliessen_bleibt_das_ergebnis_der_order(
    monkeypatch, tmp_path, caplog
) -> None:
    """Die Order ist draussen; ein Fehler beim Schliessen der Ablage darf das Ergebnis nicht
    verschlucken — er wird gemeldet, nicht weitergereicht."""
    import contextlib

    import core.state.zusammenbau as zusammenbau
    from core.engine.order_executor import _outbox_sitzung

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    @contextlib.asynccontextmanager
    async def _port():
        yield MagicMock()
        raise OSError("Schliessen gescheitert")

    monkeypatch.setattr(zusammenbau, "kurzer_port", _port)

    with caplog.at_level(logging.ERROR):
        async with _outbox_sitzung() as outbox:
            ergebnis = "gesendet" if outbox is not None else "ohne"

    assert ergebnis == "gesendet"
    assert any(r.exc_info for r in caplog.records if "schliessen" in r.getMessage())


async def test_mit_abgeschaltetem_flag_wird_nichts_festgeschrieben(
    ablage, tor_offen, monkeypatch
) -> None:
    import config

    echt = config.get_config

    class _Aus:
        def __getattr__(self, name):
            if name == "ORDER_OUTBOX_ENABLED":
                return False
            return getattr(echt(), name)

    monkeypatch.setattr(config, "get_config", lambda: _Aus())
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    await _sende(client)

    assert await (await _outbox()).lese("entry-0-entscheidung-7") is None


def test_das_flag_steht_in_beiden_editionen_mit_demselben_default() -> None:
    """BORA: kein editionsabhaengiger Default."""
    import re
    from pathlib import Path

    wurzel = Path(__file__).resolve().parents[2]
    for datei in ("settings.py",):
        text = (wurzel / datei).read_text(encoding="utf-8")
        treffer = re.search(r'ORDER_OUTBOX_ENABLED[^\n]*\n?[^\n]*"(True|False)"', text)
        assert treffer, f"ORDER_OUTBOX_ENABLED fehlt in {datei}"
        assert treffer.group(1) == "True", f"{datei}: Default ist nicht True"


# ---------------------------------------------------------------------------
# Der Haken fuer den Neustart
# ---------------------------------------------------------------------------


async def test_offener_intent_findet_den_letzten_nicht_verworfenen(ablage) -> None:
    """Auch einen bestätigten: Stirbt der Prozess nach dem Fill, bevor er es sich selbst
    notiert hat (CH-4, Szenario 2), weiss nur noch die Outbox, dass diese Order existiert.
    """
    from core.outbox import OutboxEintrag, offener_intent

    outbox = await _outbox()
    for n, zustand in ((1, "verworfen"), (2, "bestaetigt"), (3, "verworfen")):
        e = OutboxEintrag(
            decision_id=f"d{n}",
            leg="entry",
            client_order_id=f"entry-0-d{n}",
            symbol="AAPL",
            side="buy",
            qty=1.0,
        )
        await outbox.festschreiben(e)
        if zustand == "bestaetigt":
            await outbox.als_bestaetigt(e.client_order_id, broker_order_id="b")
        else:
            await outbox.als_verworfen(e.client_order_id, grund="test")
    from core.state import zusammenbau

    await zusammenbau.schliesse_alle()

    import asyncio

    gefunden = await asyncio.to_thread(offener_intent, "AAPL", str(ablage))
    assert gefunden is not None and gefunden.decision_id == "d2"
    assert await asyncio.to_thread(offener_intent, "MSFT", str(ablage)) is None


def test_offener_intent_ohne_ablage_ist_none(tmp_path) -> None:
    from core.outbox import offener_intent

    assert offener_intent("AAPL", str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Abgleich beim Start: nie blind nachsenden (Owner-Entscheid 1)
# ---------------------------------------------------------------------------


class _Nicht404(Exception):
    status_code = 500


class _Unbekannt(Exception):
    status_code = 404


async def _unbestaetigt(outbox, coid, user_id="global"):
    from core.outbox import OutboxEintrag

    await outbox.festschreiben(
        OutboxEintrag(
            decision_id=coid,
            leg="entry",
            client_order_id=coid,
            symbol="AAPL",
            side="buy",
            qty=1.0,
            user_id=user_id,
        )
    )
    await outbox.als_abgesendet(coid)


async def test_abgleich_bestaetigt_was_der_broker_kennt(ablage) -> None:
    from core.outbox import abgleichen

    outbox = await _outbox()
    await _unbestaetigt(outbox, "k1")
    client = MagicMock()
    client.get_order_by_client_id.return_value = MagicMock(id="brk-7")

    bericht = await abgleichen(outbox, client)

    assert (await outbox.lese("k1")).zustand == "bestaetigt"
    assert bericht.bestaetigt == 1
    client.submit_order.assert_not_called()


async def test_abgleich_verwirft_was_der_broker_nicht_kennt(ablage) -> None:
    from core.outbox import abgleichen

    outbox = await _outbox()
    await _unbestaetigt(outbox, "k1")
    client = MagicMock()
    client.get_order_by_client_id.side_effect = _Unbekannt("not found")

    bericht = await abgleichen(outbox, client)

    assert (await outbox.lese("k1")).zustand == "verworfen"
    assert bericht.verworfen == 1
    client.submit_order.assert_not_called(), "Der Abgleich hat nachgesendet."


async def test_abgleich_laesst_liegen_was_er_nicht_klaeren_kann(ablage) -> None:
    """Ein Netzfehler ist kein „nicht angekommen" — sonst verwuerfe er eine echte Order."""
    from core.outbox import abgleichen

    outbox = await _outbox()
    await _unbestaetigt(outbox, "k1")
    client = MagicMock()
    client.get_order_by_client_id.side_effect = _Nicht404("503")

    bericht = await abgleichen(outbox, client)

    assert (await outbox.lese("k1")).zustand == "abgesendet"
    assert bericht.ungeklaert == 1


async def test_abgleich_fasst_fremde_konten_nicht_an(ablage) -> None:
    """Ein Mandanten-Intent in einem anderen Konto waere hier „unbekannt" — und faelschlich
    verworfen. Nur Intents des Kontos, dessen Client vorliegt, werden abgeglichen."""
    from core.outbox import abgleichen

    outbox = await _outbox()
    await _unbestaetigt(outbox, "k1", user_id="mandant-a")
    client = MagicMock()
    client.get_order_by_client_id.side_effect = _Unbekannt("not found")

    bericht = await abgleichen(outbox, client, konten={"global"})

    assert (await outbox.lese("k1")).zustand == "abgesendet"
    assert bericht.fremd == 1


async def test_ohne_dauerhafte_ablage_bleibt_die_outbox_aus(
    monkeypatch, tor_offen, caplog, tmp_path
) -> None:
    """Weder AAA_USER_DATA_DIR (Desktop) noch REDIS_URL (Enterprise): keine Outbox.

    Eine Zustandsdatei relativ zum Arbeitsverzeichnis ist keine Installation — im Repo
    lagen dort schon 1,3 GB Hinterlassenschaften von Testlaeufen (#3468). Die Order geht
    trotzdem hinaus; dass die Outbox aus ist, wird gemeldet.
    """
    import core.state.zusammenbau as zusammenbau
    from core.engine import order_executor as oe

    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    # Die Meldung kommt einmal je Prozess — ein frueherer Test kann sie schon ausgeloest
    # haben. Ohne Zuruecksetzen haengt dieser Test von der Reihenfolge ab (so gemessen).
    monkeypatch.setattr(oe, "_OUTBOX_OHNE_ABLAGE_GEMELDET", False)
    gerufen = []

    def _port():
        gerufen.append(1)
        raise AssertionError("ohne dauerhafte Ablage darf kein Port gebaut werden")

    monkeypatch.setattr(zusammenbau, "kurzer_port", _port)
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    with caplog.at_level(logging.WARNING):
        order = await _sende(client)

    assert order.id == "brk-1"
    assert gerufen == []
    assert any("Outbox" in r.getMessage() for r in caplog.records)


async def test_der_start_gleicht_vor_dem_ersten_zyklus_ab(ablage) -> None:
    """Der Abgleich laeuft beim Start mit dem globalen Client — und sendet nichts."""
    from core.engine.trading_loop import TradingLoopMixin

    outbox = await _outbox()
    await _unbestaetigt(outbox, "k1")
    engine = TradingLoopMixin.__new__(TradingLoopMixin)
    engine.api = MagicMock()
    engine.api.get_order_by_client_id.return_value = MagicMock(id="brk-3")

    await engine._start_outbox_abgleich()

    assert (await outbox.lese("k1")).zustand == "bestaetigt"
    engine.api.submit_order.assert_not_called()


def test_der_abgleich_steht_vor_der_zyklus_schleife() -> None:
    """Reihenfolge ist der Kern: Ein Zyklus vor dem Abgleich entschiede neu."""
    import inspect

    from core.engine.trading_loop import TradingLoopMixin

    quelle = inspect.getsource(TradingLoopMixin.live_trading_loop)
    abgleich = quelle.index("await self._start_outbox_abgleich()")
    schleife = quelle.index("while self.strategy_running.is_set()")
    assert abgleich < schleife


async def test_nach_der_absendung_bleibt_keine_verbindung_offen(
    ablage, tor_offen
) -> None:
    """Eine offene aiosqlite-Verbindung haelt einen Nicht-Daemon-Thread — der Prozess endet
    dann nie. So gemessen an der Ketten-Vorrichtung (Timeout nach 120 s): Mit offen
    gelassenem Port haette die Engine beim Beenden gehangen."""
    import threading

    def _offene():
        return [
            t
            for t in threading.enumerate()
            if "_connection_worker_thread" in t.name and t.is_alive()
        ]

    vorher = len(_offene())
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    await _sende(client)

    import asyncio

    for _ in range(50):
        if len(_offene()) <= vorher:
            break
        await asyncio.sleep(0.02)
    assert (
        len(_offene()) <= vorher
    ), "Die Absendung hat eine SQLite-Verbindung offen gelassen."


# ---------------------------------------------------------------------------
# Die Entscheidung steckt im Schluessel (Plan #3464, Umsetzung Schritt 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("schluessel", "erwartet"),
    [
        (
            "entry-0-3f2b1c4d-0000-4000-8000-000000000001",
            "3f2b1c4d-0000-4000-8000-000000000001",
        ),
        ("displacement-1-abc", "abc"),
        ("entry-0-_" + "a" * 32, None),  # Digest-Form: nicht rueckgewinnbar
        ("keine-struktur", None),
        ("unbekanntesleg-0-abc", None),
        ("", None),
    ],
)
def test_decision_id_aus_dem_schluessel(schluessel, erwartet) -> None:
    from core.idempotency import decision_id_aus

    assert decision_id_aus(schluessel) == erwartet


async def test_ohne_hereingereichte_entscheidung_zaehlt_die_im_schluessel(
    ablage, tor_offen
) -> None:
    """Die Ketten-Vorrichtung reicht keine decision_id herein — die Entscheidung steckt nur
    im Schluessel. Mit dem Ersatzschluessel „entry-kette-AAPL" leitete der Neustart einen
    ANDEREN Schluessel ab, und der Broker naehme zwei Orders an (so gemessen, CH-4)."""
    client = MagicMock()
    client.submit_order.return_value = MagicMock(id="brk-1")

    await _sende(
        client, request=_anfrage("entry-0-die-echte-entscheidung"), decision_id=""
    )

    eintrag = await (await _outbox()).lese("entry-0-die-echte-entscheidung")
    assert eintrag.decision_id == "die-echte-entscheidung"


def test_der_wiederhergestellte_intent_ist_veraenderbar(tmp_path) -> None:
    """Die Vorrichtung schreibt ``kontext.alpaca_order_id`` auf das Wiederhergestellte —
    ein eingefrorener Eintrag brach den Neustart ab (so gemessen)."""
    import asyncio

    from core.outbox import Outbox, OutboxEintrag, offener_intent
    from core.state.sqlite_adapter import SqliteStateAdapter

    async def _lege_an():
        port = SqliteStateAdapter(str(tmp_path / "engine_state.db"))
        try:
            await Outbox(port).festschreiben(
                OutboxEintrag(
                    decision_id="d1",
                    leg="entry",
                    client_order_id="entry-0-d1",
                    symbol="AAPL",
                    side="buy",
                    qty=1.0,
                )
            )
        finally:
            await port.aclose()

    asyncio.run(_lege_an())
    wieder = offener_intent("AAPL", str(tmp_path))

    wieder.alpaca_order_id = "brk-9"
    assert (wieder.decision_id, wieder.alpaca_order_id) == ("d1", "brk-9")
