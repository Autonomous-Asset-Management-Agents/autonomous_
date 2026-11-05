"""#3449 — der Zusammenbau des ``StatePort``: welche Ablage in welcher Edition.

Port und Adapter stehen seit #3388, die Outbox seit #3458, die Engine-Sperre seit #3451 —
und **kein Produktionscode baut je einen Adapter**. Ohne diese eine Stelle bleibt alles
davon ein Modul ohne Wirkung.

Die Entscheidung folgt derselben Frage, die ``RedisClient`` heute schon stellt
(``core/redis_client.py``, ``_is_local_mode``): Ist ``REDIS_URL`` gesetzt? Das ist keine
Editionsabfrage, sondern eine Frage an die Umgebung — der Kern bleibt editionsneutral.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
async def _aufraeumen():
    yield
    from core.state import zusammenbau

    await zusammenbau.schliesse_alle()


async def test_ohne_redis_liegt_der_zustand_in_sqlite_unter_userdata(
    monkeypatch, tmp_path
) -> None:
    from core.state import SqliteStateAdapter, zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    port = await zusammenbau.state_port()

    assert isinstance(port, SqliteStateAdapter)
    await port.set("halt", "true")
    assert (tmp_path / "engine_state.db").exists(), (
        "Der Betriebszustand muss unter USER_DATA_DIR liegen — nur dort ueberlebt er ein "
        "Update der Anwendung (derselbe Grund wie bei den Audit-Logs, core/audit_paths.py)."
    )


async def test_mit_redis_url_ist_es_der_redis_adapter(monkeypatch) -> None:
    import fakeredis.aioredis as fake

    from core.state import RedisStateAdapter, zusammenbau

    monkeypatch.setenv("REDIS_URL", "redis://irgendwo:6379/0")
    client = fake.FakeRedis(decode_responses=True)

    async def _get_redis():
        return client

    monkeypatch.setattr("core.redis_client.RedisClient.get_redis", _get_redis)

    port = await zusammenbau.state_port()

    assert isinstance(port, RedisStateAdapter)
    await port.set("halt", "true")
    assert await client.get("halt") == "true"


async def test_derselbe_ring_bekommt_denselben_port(monkeypatch, tmp_path) -> None:
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    assert await zusammenbau.state_port() is await zusammenbau.state_port()


async def test_ein_anderer_ring_bekommt_einen_eigenen_port(
    monkeypatch, tmp_path
) -> None:
    """Der Kern der Sache.

    Die Engine hat mehrere Ereignisringe: den der API, den des ``CloudLoggerWorker``, und
    der Herzschlag der Engine-Sperre (#3453) bekommt einen eigenen Thread. Eine
    ``aiosqlite``-Verbindung und ein ``asyncio.Lock`` gehoeren dem Ring, der sie angelegt
    hat — ein prozessweiter Einzelgaenger wuerde im zweiten Ring haengen oder werfen.

    Geteilt wird die **Datei**, nicht das Objekt: Was der eine Ring schreibt, liest der
    andere.
    """
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    hier = await zusammenbau.state_port()
    await hier.set("halt", "true")

    ergebnis: dict = {}

    def _anderer_thread() -> None:
        async def _lauf():
            dort = await zusammenbau.state_port()
            ergebnis["port"] = dort
            ergebnis["gelesen"] = await dort.get("halt")
            await zusammenbau.schliesse_alle()

        asyncio.run(_lauf())

    t = threading.Thread(target=_anderer_thread)
    t.start()
    await asyncio.to_thread(t.join, 20)

    assert ergebnis.get("port") is not hier, "Zwei Ringe teilen sich ein Port-Objekt."
    assert ergebnis.get("gelesen") == "true", "Der zweite Ring sieht den Zustand nicht."


async def test_ohne_erreichbares_redis_gibt_es_keinen_stillen_ersatz(
    monkeypatch,
) -> None:
    """Enterprise ohne Redis darf **nicht** still auf eine lokale Datei ausweichen.

    Zwei Instanzen, jede mit ihrer eigenen Datei, hielten sich beide fuer den einzigen
    Schreiber — genau der Zustand, den die Engine-Sperre verhindern soll. Lieber kein Port
    als ein falscher.
    """
    from core.state import StatePortUnavailable, zusammenbau

    monkeypatch.setenv("REDIS_URL", "redis://irgendwo:6379/0")

    async def _kein_redis():
        return None

    monkeypatch.setattr("core.redis_client.RedisClient.get_redis", _kein_redis)

    with pytest.raises(StatePortUnavailable):
        await zusammenbau.state_port()


async def test_ein_toter_ring_hinterlaesst_keinen_port(monkeypatch, tmp_path) -> None:
    """Python vergibt ``id(loop)`` nach dem Tod eines Rings neu.

    Hinge der Port an der id, bekaeme ein frischer Ring irgendwann den Port eines toten —
    mit einer Verbindung an einem geschlossenen Ring. Darum haengt er schwach am Ring
    selbst: Stirbt der Ring, ohne aufzuraeumen, verschwindet der Eintrag mit ihm.
    """
    import gc

    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    def _ring_der_nicht_aufraeumt() -> None:
        async def _lauf():
            port = await zusammenbau.state_port()
            await port.set("x", "1")
            await port.aclose()  # Verbindung zu, aber der Eintrag bleibt stehen

        asyncio.run(_lauf())

    t = threading.Thread(target=_ring_der_nicht_aufraeumt)
    t.start()
    await asyncio.to_thread(t.join, 20)
    gc.collect()

    lebende = [ring for ring in list(zusammenbau._ports) if not ring.is_closed()]
    tote = [ring for ring in list(zusammenbau._ports) if ring.is_closed()]
    assert not tote, "Ein geschlossener Ring haelt noch einen Port."
    assert len(lebende) <= 1
