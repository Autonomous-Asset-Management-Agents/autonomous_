"""#3388 (ARC-E2.5) — Vertragstest fuer den ``StatePort``, zweimal gefahren.

Dies ist der Ort, an dem BORA ausfuehrbar wird. Jeder Fall laeuft einmal gegen
den SQLite-Adapter (Desktop) und einmal gegen den Redis-Adapter (Enterprise).
Ein Adapter, der eine Zusicherung nicht haelt, macht die CI rot — unabhaengig
davon, welche Edition ihn benutzt.

Der Plan warnt ausdruecklich davor, dass so ein Test zur Attrappe wird. Er ist
darum auf die drei Stellen gerichtet, an denen sich zwei Ablagen erfahrungsgemaess
unterscheiden, und nicht auf das, was ohnehin gleich ist:

* **Ablauf** — eine Frist muss in beiden Ablagen dasselbe bedeuten.
* **Nebenlaeufigkeit** — ein Zaehler muss unter gleichzeitigem Zugriff exakt
  bleiben, nicht ungefaehr.
* **Wiederherstellung nach Neustart** — der Betriebszustand muss einen neuen
  Prozess ueberleben.

Dazu die Sperre, weil #3390 („genau ein Schreiber je Konto") auf ihr aufsetzt.
Der heutige Desktop-Stub (``local_state_client.py:94-95``) gibt bedingungslos
``True`` zurueck und ist ein No-Op, den niemand bemerkt hat, weil es keinen
gemeinsamen Abnahmetest gab. Genau diese Luecke schliessen die Sperrfaelle hier.

**Grenze der Aussage:** Der Redis-Lauf faehrt gegen ``fakeredis``, nicht gegen
einen echten Redis-Server. Er beweist, dass der *Adapter* den Vertrag gegen eine
getreue In-Process-Nachbildung haelt — nicht, dass ein echter Server sich unter
Netzfehlern, Failover oder Eviction ebenso verhaelt. Was ``fakeredis`` nicht
nachbildet, ist hier nicht abgenommen.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit

_TTL_KURZ = 1.0  # s — großzügig, um Flakiness auf CI-Runner unter Last zu vermeiden

#: Zwei Fristen statt einer, und der Grund ist nicht Geschmack.
#:
#: Eine Zusage ueber Fristen hat zwei Richtungen, und sie sind NICHT gleich
#: empfindlich gegen einen ausgelasteten Runner:
#:
#: * „nach der Frist ist der Wert weg" scheitert nur, wenn die Uhr rueckwaerts
#:   laeuft. Dafuer genuegt eine kurze Frist — je kuerzer, desto schneller die Suite.
#: * „vor der Frist ist der Wert noch da" scheitert, sobald zwischen Setzen und
#:   Lesen mehr Zeit vergeht als die Frist. Bei 0,15 s entscheidet unter ``-n 4``
#:   die Laune des Schedulers. Genau so ist
#:   ``test_frist_laesst_den_wert_verschwinden[sqlite]`` auf PR #3452 rot geworden —
#:   nicht an der Ablage, sondern an der Zeile davor.
#:
#: Die Antwort ist nicht „die Marge weiten" (das verschiebt den Zufall nur), sondern
#: jede Richtung an dem Schluessel zu pruefen, an dem sie nicht vom Takt abhaengt.
_TTL_LANG = 30.0  # s — so weit ueber jeder Runner-Laune, dass es keine Frage mehr ist


# ---------------------------------------------------------------------------
# Vorrichtung: derselbe Vertrag, zwei Ablagen
# ---------------------------------------------------------------------------


class _Ablage:
    """Eine Ablage, die einen Adapter mehrfach oeffnen kann.

    Ein „Neustart" heisst fuer beide Adapter dasselbe: Der *Prozess* geht, die
    *Ablage* bleibt. Fuer SQLite ist das dieselbe Datei, fuer Redis derselbe
    Server bei neuer Verbindung. Ohne diese Gleichsetzung wuerde der
    Wiederherstellungsfall zwei verschiedene Dinge pruefen.
    """

    def __init__(self, name: str, oeffne):
        self.name = name
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
        except ImportError:  # pragma: no cover - nur ohne CI-Abhaengigkeiten
            pytest.skip("fakeredis nicht installiert — pip install fakeredis")

        # Derselbe Server ueber alle Verbindungen hinweg: „Neustart" heisst, dass
        # der Prozess geht und die Ablage bleibt. Ein frischer Server je
        # Verbindung wuerde den Wiederherstellungsfall trivial rot machen.
        server = fakeredis.FakeServer()

        async def oeffne():
            return RedisStateAdapter(
                fake.FakeRedis(server=server, decode_responses=True)
            )

    a = _Ablage(request.param, oeffne)
    yield a
    await a.schliesse_alle()


@pytest.fixture
async def port(ablage):
    return await ablage.port()


# ---------------------------------------------------------------------------
# Schluessel und Werte
# ---------------------------------------------------------------------------


async def test_gesetzter_wert_kommt_unveraendert_zurueck(port) -> None:
    await port.set("halt", "gesetzt-durch-mensch")
    assert await port.get("halt") == "gesetzt-durch-mensch"


async def test_unbekannter_schluessel_ist_none_und_nicht_leerstring(port) -> None:
    """``None`` und ``""`` duerfen nicht verwechselbar sein.

    Ein Halt-Zustand, der als Leerstring zurueckkommt, wird von einer
    Wahrheitspruefung als „nicht gesetzt" gelesen.
    """
    assert await port.get("gibt-es-nicht") is None
    await port.set("leer", "")
    assert await port.get("leer") == ""


async def test_loeschen_meldet_ob_etwas_da_war(port) -> None:
    await port.set("k", "v")
    assert await port.delete("k") is True
    assert await port.delete("k") is False
    assert await port.get("k") is None


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------


async def test_frist_laesst_den_wert_verschwinden(port) -> None:
    # Noch da: am langen Schluessel, weil diese Richtung sonst am Takt haengt.
    await port.set("noch_da", "v", ttl_seconds=_TTL_LANG)
    assert await port.get("noch_da") == "v"
    # Weg: am kurzen, weil diese Richtung nur bei rueckwaerts laufender Uhr kippt.
    await port.set("kurz", "v", ttl_seconds=_TTL_KURZ)
    await asyncio.sleep(_TTL_KURZ * 2)
    assert await port.get("kurz") is None


async def test_ohne_frist_bleibt_der_wert(port) -> None:
    await port.set("lang", "v")
    await asyncio.sleep(_TTL_KURZ * 2)
    assert await port.get("lang") == "v"


async def test_neues_setzen_ohne_frist_hebt_die_alte_frist_auf(port) -> None:
    """Sonst verschwaende ein frisch gesetzter Halt nach der alten Frist."""
    await port.set("h", "alt", ttl_seconds=_TTL_KURZ)
    await port.set("h", "neu")
    await asyncio.sleep(_TTL_KURZ * 2)
    assert await port.get("h") == "neu"


# ---------------------------------------------------------------------------
# Nebenlaeufigkeit
# ---------------------------------------------------------------------------


async def test_zaehler_bleibt_unter_gleichzeitigem_zugriff_exakt(port) -> None:
    """Das Tagesbudget ist ein Zaehler. „Ungefaehr" ist hier kein Ergebnis."""
    await asyncio.gather(*(port.increment("budget", 1.0) for _ in range(50)))
    assert float(await port.get("budget")) == pytest.approx(50.0)


async def test_zaehler_gibt_den_neuen_stand_zurueck(port) -> None:
    assert await port.increment("z", 2.5) == pytest.approx(2.5)
    assert await port.increment("z", 2.5) == pytest.approx(5.0)


async def test_zaehler_beginnt_bei_null_und_zaehlt_auch_zurueck(port) -> None:
    assert await port.increment("z", -3.0) == pytest.approx(-3.0)


# ---------------------------------------------------------------------------
# Warteschlange (HITL, Frequenz-Guards)
# ---------------------------------------------------------------------------


async def test_warteschlange_ist_fifo(port) -> None:
    for w in ("a", "b", "c"):
        await port.append("q", w)
    assert await port.read("q") == ["a", "b", "c"]
    assert await port.pop("q") == "a"
    assert await port.read("q") == ["b", "c"]


async def test_leere_warteschlange_gibt_none_statt_zu_werfen(port) -> None:
    assert await port.pop("leer") is None
    assert await port.read("leer") == []


async def test_deckel_wirft_die_aeltesten_eintraege_weg(port) -> None:
    for i in range(10):
        await port.append("q", str(i), max_length=3)
    assert await port.read("q") == ["7", "8", "9"]


# ---------------------------------------------------------------------------
# Sperre — die Grundlage von #3390
# ---------------------------------------------------------------------------


async def test_eine_gehaltene_sperre_wird_kein_zweites_mal_vergeben(port) -> None:
    erste = port.lock("konto:1", ttl_seconds=5)
    zweite = port.lock("konto:1", ttl_seconds=5)
    assert await erste.acquire() is True
    assert await zweite.acquire() is False
    await erste.release()


async def test_nach_der_freigabe_ist_die_sperre_wieder_zu_haben(port) -> None:
    erste = port.lock("konto:2", ttl_seconds=5)
    assert await erste.acquire() is True
    assert await erste.release() is True
    zweite = port.lock("konto:2", ttl_seconds=5)
    assert await zweite.acquire() is True
    await zweite.release()


async def test_eine_sperre_nennt_ihr_besitz_token(port) -> None:
    """#3453: Das Token wird neben die Sperre geschrieben, damit ein Nachfolger auf demselben
    Rechner einen nachweislich toten Halter erkennen und genau DESSEN Sperre brechen kann.
    """
    erste = port.lock("konto:t", ttl_seconds=5)
    zweite = port.lock("konto:t", ttl_seconds=5)
    assert erste.besitz_token and erste.besitz_token != zweite.besitz_token


async def test_brechen_wirkt_nur_gegen_das_genannte_token(port) -> None:
    """#3453: Gebrochen wird nur die Sperre des genannten (toten) Halters. Hat inzwischen ein
    anderer uebernommen, bleibt dessen Sperre stehen — sonst hielten sich zwei fuer Schreiber.
    """
    tot = port.lock("konto:b", ttl_seconds=30)
    nachfolger = port.lock("konto:b", ttl_seconds=30)
    assert await tot.acquire() is True

    assert await nachfolger.brechen(tot.besitz_token) is True
    assert await tot.held() is False
    assert await nachfolger.acquire() is True

    dritter = port.lock("konto:b", ttl_seconds=30)
    assert await dritter.brechen(tot.besitz_token) is False, "Veraltetes Token"
    assert await nachfolger.held() is True
    await nachfolger.release()


async def test_ein_fremder_gibt_die_sperre_nicht_frei(port) -> None:
    """Die Zusicherung, an der die heutige Freigabe scheitert.

    ``RedisClient.release_lock`` loescht den Schluessel bedingungslos
    (``redis_client.py:262-269``). Laeuft die Frist des ersten Halters ab und
    uebernimmt ein zweiter, dann loescht die verspaetete Freigabe des ersten die
    Sperre des zweiten — und beide halten sich fuer den einzigen Schreiber. Der
    Port gibt nur frei, was ihm noch gehoert.
    """
    halter = port.lock("konto:3", ttl_seconds=5)
    fremder = port.lock("konto:3", ttl_seconds=5)
    assert await halter.acquire() is True
    assert await fremder.acquire() is False

    assert await fremder.release() is False, "ein Nicht-Halter hat freigegeben"
    assert (
        await port.lock("konto:3", ttl_seconds=5).acquire() is False
    ), "die Sperre wurde durch die Freigabe eines Fremden frei"
    await halter.release()


async def test_erneuern_verlaengert_die_frist(port) -> None:
    """#3390: Ohne Erneuerung muss die Frist so lang sein wie die laengste Arbeit.

    Fuer die Engine-Sperre waere das ein ganzer Zyklus — gemessen bis zu 1800 s. So
    lange saesse das Konto nach einem Absturz fest. Mit Erneuerung entkoppeln sich
    beide Zahlen: kurze Frist, haeufige Verlaengerung, schnelle Uebernahme.
    """
    # Die Zahlen sind mit Absicht grosszuegig. Eine fruehere Fassung nahm
    # _TTL_KURZ (0,15 s) und erneuerte nach 0,075 s — lokal gruen, auf dem
    # CI-Runner unter `-n 4` rot: In der Luecke zwischen Schlafen und Erneuern
    # lief die Frist ab, und `renew()` verweigerte korrekt die Wiederbelebung.
    # Bei so kleinen absoluten Werten entscheidet die Laune des Schedulers.
    # Jetzt: Frist 1,0 s, Erneuerung alle 0,35 s — 650 ms Luft je Runde. Und die
    # Gesamtdauer (1,4 s) liegt UEBER der urspruenglichen Frist, sonst wuerde der
    # Test die Erneuerung gar nicht pruefen, sondern nur, dass 1 s noch nicht um ist.
    FRIST = 2.0
    halter = port.lock("konto:8", ttl_seconds=FRIST)
    assert await halter.acquire() is True

    for _ in range(4):
        await asyncio.sleep(0.6)
        assert await halter.renew() is True

    assert (
        await port.lock("konto:8", ttl_seconds=5).acquire() is False
    ), "Die Sperre ist trotz laufender Erneuerung abgelaufen."
    await halter.release()


async def test_ein_fremder_erneuert_nicht(port) -> None:
    """Sonst verlaengert ein abgeloester Halter die Sperre seines Nachfolgers."""
    halter = port.lock("konto:9", ttl_seconds=5)
    fremder = port.lock("konto:9", ttl_seconds=5)
    assert await halter.acquire() is True
    assert await fremder.acquire() is False
    assert await fremder.renew() is False, "Ein Nicht-Halter hat erneuert."
    await halter.release()


async def test_eine_abgelaufene_sperre_wird_nicht_wiederbelebt(port) -> None:
    """Nach dem Ablauf ist die Sperre fort — auch fuer den frueheren Halter.

    Ohne diese Bedingung koennte eine langsame Instanz ihre laengst abgelaufene
    Sperre zurueckholen, womoeglich waehrend ein anderer sie gerade uebernimmt.
    """
    halter = port.lock("konto:10", ttl_seconds=_TTL_KURZ)
    assert await halter.acquire() is True
    await asyncio.sleep(_TTL_KURZ * 2)

    assert (
        await halter.renew() is False
    ), "Eine abgelaufene Sperre liess sich wiederbeleben."
    assert await port.lock("konto:10", ttl_seconds=5).acquire() is True


async def test_der_halter_weiss_dass_er_haelt_und_wann_nicht_mehr(port) -> None:
    """„Und die andere handelt nicht und meldet ihren Zustand" (#3390).

    Eine abgeloeste Instanz muss das erkennen koennen — sonst arbeitet sie weiter im
    Glauben, sie sei zustaendig.
    """
    halter = port.lock("konto:11", ttl_seconds=_TTL_KURZ)
    assert await halter.held() is False, "Vor dem Erwerb haelt niemand etwas."
    assert await halter.acquire() is True
    assert await halter.held() is True

    await asyncio.sleep(_TTL_KURZ * 2)
    nachfolger = port.lock("konto:11", ttl_seconds=5)
    assert await nachfolger.acquire() is True

    assert await halter.held() is False, (
        "Der alte Halter haelt sich weiterhin fuer zustaendig, obwohl ein anderer "
        "uebernommen hat. Beide wuerden arbeiten."
    )
    assert await nachfolger.held() is True
    await nachfolger.release()


async def test_abgelaufene_sperre_gibt_den_platz_frei(port) -> None:
    erste = port.lock("konto:4", ttl_seconds=_TTL_KURZ)
    assert await erste.acquire() is True
    await asyncio.sleep(_TTL_KURZ * 2)
    zweite = port.lock("konto:4", ttl_seconds=5)
    assert await zweite.acquire() is True
    await zweite.release()


async def test_sperre_als_kontextmanager_gibt_am_ende_frei(port) -> None:
    async with port.lock("konto:5", ttl_seconds=5):
        assert await port.lock("konto:5", ttl_seconds=5).acquire() is False
    assert await port.lock("konto:5", ttl_seconds=5).acquire() is True


async def test_kontextmanager_laeuft_niemals_ohne_die_sperre(port) -> None:
    """Der Fall, den die erste Fassung des Ports durchgehen liess (FINDING-01).

    Damals gab ``__aenter__`` ein ``bool`` zurueck und verliess sich darauf, dass
    der Aufrufer prueft. ``async with lock:`` — ohne ``as`` — fuehrt den Block in
    Python aber **bedingungslos** aus. Wer so schrieb, lief still ohne Sperre
    weiter: derselbe Fehlermodus wie der No-Op-Stub in
    ``local_state_client.py:94-95``, den dieser Port beheben soll.

    Der Test ist bewusst ohne ``as`` geschrieben — genau so, wie ein Aufrufer es
    versehentlich tun wuerde. Gegen die alte Fassung ist er rot, gegen die neue
    gruen.
    """
    from core.state import StateLockNotAcquired

    halter = port.lock("konto:7", ttl_seconds=5)
    assert await halter.acquire() is True

    betreten = False
    with pytest.raises(StateLockNotAcquired):
        async with port.lock("konto:7", ttl_seconds=5):
            betreten = True

    assert not betreten, (
        "Der Block wurde OHNE Sperre ausgefuehrt. Eine Sperre, die nicht sperrt und "
        "es nicht sagt, ist schlimmer als keine."
    )
    # Die fehlgeschlagene Klammer darf die Sperre des Halters nicht angetastet haben.
    assert await port.lock("konto:7", ttl_seconds=5).acquire() is False
    await halter.release()


async def test_kontextmanager_gibt_auch_bei_einer_ausnahme_frei(port) -> None:
    with pytest.raises(ValueError):
        async with port.lock("konto:6", ttl_seconds=5):
            raise ValueError("Absturz mitten in der Arbeit")
    assert await port.lock("konto:6", ttl_seconds=5).acquire() is True


# ---------------------------------------------------------------------------
# Wiederherstellung nach Neustart
# ---------------------------------------------------------------------------


async def test_zustand_ueberlebt_den_neustart(ablage) -> None:
    """Der fuehrende Fall des Sub-Issues.

    Halt, Tagesbudget und Warteschlange muessen unveraendert vorhanden sein,
    nachdem der Prozess gegangen und ein neuer gekommen ist.
    """
    vorher = await ablage.port()
    await vorher.set("halt", "gesetzt-durch-mensch")
    await vorher.increment("budget", 7.0)
    await vorher.append("hitl", "freigabe-1")
    await vorher.aclose()

    nachher = await ablage.port()
    assert await nachher.get("halt") == "gesetzt-durch-mensch"
    assert float(await nachher.get("budget")) == pytest.approx(7.0)
    assert await nachher.read("hitl") == ["freigabe-1"]


async def test_frist_ueberlebt_den_neustart_als_frist(ablage) -> None:
    """Eine Frist darf durch einen Neustart weder verfallen noch unendlich werden."""
    vorher = await ablage.port()
    await vorher.set("ueberlebt", "v", ttl_seconds=_TTL_LANG)
    await vorher.set("kurz", "v", ttl_seconds=_TTL_KURZ)
    await vorher.aclose()

    nachher = await ablage.port()
    # Nicht verfallen — am langen Schluessel. Zuvor stand hier der kurze, und dann
    # entschied die Dauer von aclose()+oeffnen darueber, ob der Test etwas prueft:
    # Dauert der Neustart laenger als 0,15 s, ist der Wert regelkonform weg und der
    # Test meldet einen Defekt, den es nicht gibt.
    assert (
        await nachher.get("ueberlebt") == "v"
    ), "die Frist ist durch den Neustart verfallen"
    # Nicht unendlich geworden — am kurzen.
    await asyncio.sleep(_TTL_KURZ * 2)
    assert (
        await nachher.get("kurz") is None
    ), "die Frist wurde durch den Neustart unendlich"


async def test_ping_meldet_eine_offene_ablage(port) -> None:
    assert await port.ping() is True
