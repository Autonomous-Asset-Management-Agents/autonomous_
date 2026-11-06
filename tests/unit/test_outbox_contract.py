"""#3449 (ARC-E2.5b) — Vertragstest der Outbox, zweimal gefahren.

Die Outbox haelt einen Order-Intent **dauerhaft fest, bevor gesendet wird**. Stirbt der
Prozess zwischen Absenden und Bestaetigung, findet der Neustart den Intent samt seiner
``decision_id`` und sendet **denselben** Idempotenz-Schluessel erneut — der Broker weist
das Duplikat ab, statt eine zweite Order anzunehmen.

Ohne sie faellt der Neustart eine neue Entscheidung mit frischer ``decision_id``
(``cloud_logger.py:82``), und der Broker haelt zwei Orders fuer eine Entscheidung. Das ist
der heute gemessene CH-4-Befund, kein hypothetisches Risiko.

Wie der ``StatePort`` selbst wird auch die Outbox gegen **beide** Adapter gefahren
(SQLite fuer Desktop, Redis fuer Enterprise). Sie liegt *auf* dem Port und erbt dessen
Zusagen — aber ob sie sie richtig benutzt, zeigt nur der Lauf gegen beide.

**Grenze der Aussage:** wie beim Port laeuft der Redis-Fall gegen ``fakeredis``.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Ablage:
    def __init__(self, oeffne):
        self._oeffne = oeffne
        self._offen: list = []

    async def port(self):
        p = await self._oeffne()
        self._offen.append(p)
        return p

    async def schliesse_alle(self) -> None:
        for p in self._offen:
            await p.aclose()
        self._offen.clear()


@pytest.fixture(params=["sqlite", "redis"])
async def ablage(request, tmp_path):
    from core.state import RedisStateAdapter, SqliteStateAdapter

    if request.param == "sqlite":
        datei = tmp_path / "engine_state.db"

        async def oeffne():
            return SqliteStateAdapter(datei)

    else:
        try:
            import fakeredis
            import fakeredis.aioredis as fake
        except ImportError:  # pragma: no cover
            pytest.skip("fakeredis nicht installiert")

        server = fakeredis.FakeServer()

        async def oeffne():
            return RedisStateAdapter(
                fake.FakeRedis(server=server, decode_responses=True)
            )

    a = _Ablage(oeffne)
    yield a
    await a.schliesse_alle()


def _eintrag(**abweichend):
    from core.outbox import OutboxEintrag

    felder = dict(
        decision_id="entscheidung-1",
        leg="entry",
        client_order_id="entry-0-entscheidung-1",
        symbol="AAPL",
        side="buy",
        qty=2.0,
    )
    felder.update(abweichend)
    return OutboxEintrag(**felder)


# ---------------------------------------------------------------------------
# Festschreiben vor Absenden
# ---------------------------------------------------------------------------


async def test_ein_festgeschriebener_intent_ist_offen(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())

    gelesen = await outbox.lese("entry-0-entscheidung-1")
    assert gelesen is not None
    assert gelesen.zustand == "offen"
    assert gelesen.decision_id == "entscheidung-1"


async def test_der_intent_ueberlebt_den_neustart(ablage) -> None:
    """Szenario: Festschreiben vor Absenden.

    Der Kern der Sache. „Neustart" heisst hier dasselbe wie im Port-Vertrag: Der Prozess
    geht, die Ablage bleibt. Ein Intent, der nur im Arbeitsspeicher laege, waere nach
    dem Absturz fort — und mit ihm die ``decision_id``, an der die Wiedererkennung haengt.
    """
    from core.outbox import Outbox

    vorher = await ablage.port()
    await Outbox(vorher).festschreiben(_eintrag())
    await vorher.aclose()

    nachher = Outbox(await ablage.port())
    offene = await nachher.unbestaetigte()

    assert [e.client_order_id for e in offene] == ["entry-0-entscheidung-1"], (
        "Der Intent hat den Neustart nicht ueberlebt. Der Neustart wuerde eine neue "
        "Entscheidung faellen und eine zweite Order absetzen (#3449)."
    )
    assert offene[0].decision_id == "entscheidung-1"


# ---------------------------------------------------------------------------
# Zustaende laufen nur vorwaerts
# ---------------------------------------------------------------------------


async def test_der_lebenslauf_eines_intents(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    coid = "entry-0-entscheidung-1"
    await outbox.festschreiben(_eintrag())
    assert (await outbox.lese(coid)).zustand == "offen"

    await outbox.als_abgesendet(coid)
    assert (await outbox.lese(coid)).zustand == "abgesendet"

    await outbox.als_bestaetigt(coid, broker_order_id="brk-1")
    fertig = await outbox.lese(coid)
    assert fertig.zustand == "bestaetigt"
    assert fertig.broker_order_id == "brk-1"


async def test_ein_abgesendeter_intent_gilt_weiter_als_unbestaetigt(ablage) -> None:
    """Szenario: Wiederaufnahme sendet denselben Schluessel.

    Genau dieses Fenster ist der CH-4-Fall: abgesendet, Bestaetigung fehlt, Prozess tot.
    Der Intent muss nach dem Neustart auffindbar sein — mit demselben Schluessel.
    """
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    await outbox.als_abgesendet("entry-0-entscheidung-1")

    offene = await outbox.unbestaetigte()
    assert [(e.client_order_id, e.zustand) for e in offene] == [
        ("entry-0-entscheidung-1", "abgesendet")
    ]


async def test_ein_bestaetigter_intent_ist_erledigt(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    await outbox.als_abgesendet("entry-0-entscheidung-1")
    await outbox.als_bestaetigt("entry-0-entscheidung-1", broker_order_id="brk-1")

    assert await outbox.unbestaetigte() == []


async def test_ein_zustand_laeuft_nie_rueckwaerts(ablage) -> None:
    """Eine verspaetete „abgesendet"-Meldung darf eine Bestaetigung nicht ueberschreiben.

    Sonst stuende ein erledigter Intent wieder als unbestaetigt da — und die
    Wiederaufnahme schickte eine Order, die laengst gefuellt ist.
    """
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    coid = "entry-0-entscheidung-1"
    await outbox.festschreiben(_eintrag())
    await outbox.als_abgesendet(coid)
    await outbox.als_bestaetigt(coid, broker_order_id="brk-1")

    await outbox.als_abgesendet(coid)  # verspaetet
    await outbox.festschreiben(_eintrag())  # doppelt

    nachher = await outbox.lese(coid)
    assert nachher.zustand == "bestaetigt", (
        f"Der Zustand ist rueckwaerts gelaufen: {nachher.zustand!r}. Ein erledigter "
        "Intent stuende wieder zur Wiederaufnahme an."
    )
    assert nachher.broker_order_id == "brk-1"


# ---------------------------------------------------------------------------
# Idempotenz und Filter
# ---------------------------------------------------------------------------


async def test_doppeltes_festschreiben_erzeugt_keinen_zweiten_intent(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    await outbox.festschreiben(_eintrag())

    assert len(await outbox.unbestaetigte()) == 1


async def test_unbestaetigte_lassen_sich_je_symbol_lesen(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    await outbox.festschreiben(
        _eintrag(
            decision_id="entscheidung-2",
            client_order_id="entry-0-entscheidung-2",
            symbol="MSFT",
        )
    )

    nur_msft = await outbox.unbestaetigte(symbol="MSFT")
    assert [e.symbol for e in nur_msft] == ["MSFT"]
    assert len(await outbox.unbestaetigte()) == 2


async def test_ein_unbekannter_schluessel_ist_none(ablage) -> None:
    from core.outbox import Outbox

    assert await Outbox(await ablage.port()).lese("gibt-es-nicht") is None


async def test_eine_meldung_zu_einem_unbekannten_intent_wirft_nicht(ablage) -> None:
    """Die Outbox steht im Absendepfad. Sie darf ihn nie zum Stehen bringen.

    Eine „bestaetigt"-Meldung zu einem Intent, den es nicht gibt, ist ein Befund — aber
    die Order, um die es geht, liegt dann bereits beim Broker. Eine Ausnahme hier braeche
    den Pfad ab, nachdem Kapital bewegt wurde.
    """
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    assert (
        await outbox.als_bestaetigt("gibt-es-nicht", broker_order_id="brk-x") is False
    )
    assert await outbox.als_abgesendet("gibt-es-nicht") is False


# ---------------------------------------------------------------------------
# Das Verzeichnis waechst nicht ohne Ende
# ---------------------------------------------------------------------------


async def test_erledigte_intents_werden_aus_dem_verzeichnis_geraeumt(ablage) -> None:
    """Eine Warteschlange ohne Deckel ist ein Speicherleck mit Anlauf (Port-Vertrag).

    Geraeumt wird **nur von vorn und nur Erledigtes**. Ein unbestaetigter Intent am Kopf
    haelt die Raeumung an — lieber ein Verzeichnis, das waechst, als ein Intent, der
    verschwindet.
    """
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    for n in (1, 2, 3):
        await outbox.festschreiben(
            _eintrag(
                decision_id=f"entscheidung-{n}",
                client_order_id=f"entry-0-entscheidung-{n}",
            )
        )
    # 1 und 2 erledigt, 3 offen
    for n in (1, 2):
        await outbox.als_abgesendet(f"entry-0-entscheidung-{n}")
        await outbox.als_bestaetigt(f"entry-0-entscheidung-{n}", broker_order_id="b")

    geraeumt = await outbox.aufraeumen()

    assert geraeumt == 2
    assert [e.client_order_id for e in await outbox.unbestaetigte()] == [
        "entry-0-entscheidung-3"
    ]


async def test_ein_offener_intent_am_kopf_haelt_die_raeumung_an(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())  # bleibt offen
    await outbox.festschreiben(
        _eintrag(decision_id="e-2", client_order_id="entry-0-e-2")
    )
    await outbox.als_abgesendet("entry-0-e-2")
    await outbox.als_bestaetigt("entry-0-e-2", broker_order_id="b")

    assert (
        await outbox.aufraeumen() == 0
    ), "Die Raeumung hat einen offenen Intent uebersprungen oder entfernt."
    assert len(await outbox.unbestaetigte()) == 1


async def test_ein_unlesbarer_eintrag_wird_nicht_geraeumt(ablage) -> None:
    """Unlesbar heisst nicht erledigt — er koennte eine offene Order sein."""
    from core.outbox import Outbox

    port = await ablage.port()
    outbox = Outbox(port)
    await outbox.festschreiben(_eintrag())
    await port.set("outbox:intent:entry-0-entscheidung-1", "{kein json")

    assert await outbox.aufraeumen() == 0
    assert await port.read("outbox:verzeichnis") == ["entry-0-entscheidung-1"]


# ---------------------------------------------------------------------------
# Verworfen: der Intent wird sicher nicht zur Order
# ---------------------------------------------------------------------------


async def test_ein_verworfener_intent_wird_nicht_wiederaufgenommen(ablage) -> None:
    """Lehnt das Tor ab, darf der Neustart die Order nicht nachholen.

    Sonst setzte die Wiederaufnahme genau die Order ab, die der Halt verhindert hat.
    """
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    assert await outbox.als_verworfen("entry-0-entscheidung-1", grund="trading_halted")

    assert await outbox.unbestaetigte() == []
    gelesen = await outbox.lese("entry-0-entscheidung-1")
    assert (gelesen.zustand, gelesen.grund) == ("verworfen", "trading_halted")


async def test_bestaetigt_und_verworfen_ueberschreiben_einander_nicht(ablage) -> None:
    from core.outbox import Outbox

    outbox = Outbox(await ablage.port())
    await outbox.festschreiben(_eintrag())
    await outbox.als_abgesendet("entry-0-entscheidung-1")
    await outbox.als_bestaetigt("entry-0-entscheidung-1", broker_order_id="brk-1")

    assert await outbox.als_verworfen("entry-0-entscheidung-1", grund="x") is False
    assert (await outbox.lese("entry-0-entscheidung-1")).zustand == "bestaetigt"
