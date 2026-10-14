"""#3390 (ARC-E2.7) — Vertragstest der Schreibberechtigung, zweimal gefahren.

Je Konto haelt genau **eine** Engine-Instanz eine Berechtigung auf Zeit. Sie erneuert
sie, solange sie lebt und **vorankommt**; verliert oder bekommt sie sie nicht, handelt
sie nicht.

Wie bei #3443 laeuft jeder Fall zweimal — einmal gegen den SQLite-Adapter (Desktop),
einmal gegen Redis (Enterprise). Der Plan verbietet ausdruecklich eine
editionsabhaengige Voreinstellung nach dem Muster „auf Desktop braucht man das nicht":
Genau diese Annahme hat den heutigen No-Op in ``local_state_client.py:86-89`` erzeugt.

**Die drei Zahlen und woher sie kommen.** Gemessen aus der laufenden Konfiguration
(``core/engine/time_budget.py:134``): ``agent=60s``, ``symbol=120s``, ``cycle=1800s``.
Ein Zyklus darf also eine halbe Stunde dauern — eine Berechtigung **ohne** Erneuerung
muesste so lange gelten, und genau so lange saesse das Konto nach einem Absturz fest.
Darum die Erneuerung.

**Die Falle, die dieser Test ausdruecklich prueft:** Ein Heartbeat, der nur am Leben
des Prozesses haengt, erneuert auch dann, wenn die Schleife festgefahren ist — dann
hortet eine haengende Engine ihre Berechtigung und die Uebernahme findet nie statt.
Das waere dieselbe Fehlerklasse wie in #3438, wo ``stop()`` zurueckgab, als haette es
gestoppt. Die Erneuerung ist darum **an den Fortschritt gebunden**, nicht ans Leben.
"""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.unit

KONTO = "nutzer-1:paper"
FRIST = 1.0  # s — großzügig, um Flakiness auf dem CI-Runner unter Last zu vermeiden


@pytest.fixture(params=["sqlite", "redis"])
async def port(request, tmp_path):
    """Derselbe Vertrag, zwei Ablagen — wie in test_state_port_contract.py."""
    from core.state import RedisStateAdapter, SqliteStateAdapter

    if request.param == "sqlite":
        p = SqliteStateAdapter(tmp_path / "state.db")
    else:
        try:
            import fakeredis
            import fakeredis.aioredis as fake
        except ImportError:  # pragma: no cover
            pytest.skip("fakeredis nicht installiert")
        p = RedisStateAdapter(
            fake.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
        )
    yield p
    await p.aclose()


# ---------------------------------------------------------------------------
# Erwerb
# ---------------------------------------------------------------------------


async def test_nur_eine_instanz_bekommt_die_berechtigung(port) -> None:
    from core.lease import EngineLease

    erste = EngineLease(port, konto=KONTO, instanz="a", frist_s=FRIST)
    zweite = EngineLease(port, konto=KONTO, instanz="b", frist_s=FRIST)

    assert await erste.erwerben() is True
    assert await zweite.erwerben() is False, (
        "Zwei Instanzen halten dieselbe Berechtigung. Genau das ist der Schaden aus "
        "#3390: doppelte Position und doppeltes Risiko auf einem Konto."
    )


async def test_verschiedene_konten_stoeren_sich_nicht(port) -> None:
    """Paper und Live sind ZWEI Konten — belegt in config.py:56-58.

    `user_id` allein waere zu grob: ``_select_alpaca_account`` tauscht bei
    ``PAPER_TRADING=False`` auf getrennte Schluesselfaecher, bei unveraenderter
    ``user_id``. Eine Sperre je Nutzer wuerde Paper gegen Live sperren — und damit
    genau den Fall verhindern, den #1425 ermoeglichen wollte.
    """
    from core.lease import EngineLease

    paper = EngineLease(port, konto="nutzer-1:paper", instanz="a", frist_s=FRIST)
    live = EngineLease(port, konto="nutzer-1:live", instanz="b", frist_s=FRIST)

    assert await paper.erwerben() is True
    assert (
        await live.erwerben() is True
    ), "Paper und Live sind getrennte Broker-Konten und duerfen sich nicht sperren."


# ---------------------------------------------------------------------------
# Erneuerung — an den Fortschritt gebunden, nicht ans Leben
# ---------------------------------------------------------------------------


async def test_erneuern_haelt_die_berechtigung_ohne_unterbrechung(port) -> None:
    from core.lease import EngineLease

    # Grosszuegige Margen, siehe test_state_port_contract.py: Eine fruehere
    # Fassung mit 0,3 s Frist und 0,15 s Takt war lokal gruen und auf dem
    # CI-Runner unter Last rot.
    halter = EngineLease(port, konto=KONTO, instanz="a", frist_s=FRIST)
    assert await halter.erwerben() is True

    for _ in range(4):
        await asyncio.sleep(0.35)
        assert await halter.erneuern() is True

    fremde = EngineLease(port, konto=KONTO, instanz="b", frist_s=FRIST)
    assert (
        await fremde.erwerben() is False
    ), "Die Berechtigung ist trotz laufender Erneuerung abgelaufen."


async def test_eine_haengende_instanz_verliert_die_berechtigung(port) -> None:
    """Der wichtigste Fall dieser Datei.

    Ein Heartbeat am Leben des Prozesses wuerde hier gruen bleiben und die
    Berechtigung ewig halten. Gebunden ist er darum an den **Fortschritt**: Meldet
    der Zyklus keinen frischen Stand, wird nicht erneuert — und die zweite Instanz
    darf uebernehmen.
    """
    from core.lease import EngineLease

    haenger = EngineLease(
        port,
        konto=KONTO,
        instanz="a",
        frist_s=FRIST,
        fortschritt=lambda: False,  # die Schleife kommt nicht voran
    )
    assert await haenger.erwerben() is True
    assert await haenger.erneuern() is False, (
        "Eine festgefahrene Instanz hat ihre Berechtigung erneuert. Dann findet die "
        "Uebernahme nie statt — dieselbe Fehlerklasse wie #3438."
    )


# ---------------------------------------------------------------------------
# Ablauf und Uebernahme
# ---------------------------------------------------------------------------


async def test_uebernahme_nach_ausfall_ohne_freigabe(port) -> None:
    """Ein hart getoeteter Prozess gibt nichts frei — deshalb ist die Frist Pflicht."""
    from core.lease import EngineLease

    tot = EngineLease(port, konto=KONTO, instanz="a", frist_s=FRIST)
    assert await tot.erwerben() is True
    # kein `freigeben()` — der Prozess ist weg

    await asyncio.sleep(FRIST * 2)

    nachfolger = EngineLease(port, konto=KONTO, instanz="b", frist_s=FRIST)
    assert (
        await nachfolger.erwerben() is True
    ), "Nach Ablauf der Frist konnte niemand uebernehmen — das Konto saesse fest."


async def test_der_verlierer_weiss_dass_er_verloren_hat(port) -> None:
    """„Und die andere handelt nicht und meldet ihren Zustand" (Akzeptanzkriterium).

    Eine Instanz, die ihre Berechtigung verloren hat, muss das **erkennen koennen** —
    sonst setzt sie weiter Orders ab, im Glauben, sie sei der Schreiber.
    """
    from core.lease import EngineLease

    halter = EngineLease(port, konto=KONTO, instanz="a", frist_s=FRIST)
    assert await halter.erwerben() is True
    assert await halter.haelt_noch() is True

    await asyncio.sleep(FRIST * 2)
    nachfolger = EngineLease(port, konto=KONTO, instanz="b", frist_s=FRIST)
    assert await nachfolger.erwerben() is True

    assert await halter.haelt_noch() is False, (
        "Der alte Halter haelt sich weiterhin fuer den Schreiber, obwohl ein anderer "
        "uebernommen hat. Beide wuerden handeln."
    )


async def test_freigeben_wirkt_nur_fuer_den_halter(port) -> None:
    """Dieselbe Zusage wie beim StatePort: Ein Fremder gibt nichts frei.

    Sonst loescht die verspaetete Freigabe eines abgeloesten Halters die Berechtigung
    seines Nachfolgers — und beide halten sich fuer den einzigen Schreiber.
    """
    from core.lease import EngineLease

    halter = EngineLease(port, konto=KONTO, instanz="a", frist_s=5)
    fremder = EngineLease(port, konto=KONTO, instanz="b", frist_s=5)

    assert await halter.erwerben() is True
    assert await fremder.erwerben() is False
    assert await fremder.freigeben() is False, "Ein Nicht-Halter hat freigegeben."
    assert await halter.haelt_noch() is True
    assert await halter.freigeben() is True


async def test_nach_freigabe_darf_der_naechste_sofort(port) -> None:
    from core.lease import EngineLease

    erste = EngineLease(port, konto=KONTO, instanz="a", frist_s=5)
    assert await erste.erwerben() is True
    assert await erste.freigeben() is True

    zweite = EngineLease(port, konto=KONTO, instanz="b", frist_s=5)
    assert await zweite.erwerben() is True, (
        "Nach einer ordentlichen Freigabe muss die Uebernahme sofort moeglich sein — "
        "sonst wartet ein geplanter Neustart unnoetig eine volle Frist ab."
    )
