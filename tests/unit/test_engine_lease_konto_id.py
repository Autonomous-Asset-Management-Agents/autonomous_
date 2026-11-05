"""#4631 (STW-5) — Die Schreibsperre meint das Alpaca-Konto, nicht den Prozess.

Bei ``STRATEGY_PROFILES_ENABLED`` an:

* Der Schluessel ist ``alpaca:<Konto-ID>`` — zwei Profile auf **demselben** Konto sperren
  einander, zwei auf **verschiedenen** nicht mehr (heute sperrt ``global:paper`` beide).
* Ist die Konto-ID unbekannt, schreibt die Engine nicht (fail-closed, WARNING).
* Die Sperre liegt in einer **gemeinsamen** Ablage (``AAA_LEASE_DIR``). Ohne sie laege sie je
  Profil in einer eigenen Datei — und sperrte nichts (die Gegenprobe haelt das fest).

Bei aus bleibt alles wie vor #4631. Plan: ``docs/4631-*/implementation_plan.md`` §2.3/2.4.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]


@pytest.fixture
async def profile(monkeypatch, tmp_path):
    """Flag an, zwei Datenverzeichnisse, gemeinsames ``AAA_LEASE_DIR``, saubere Registratur."""
    from core import lease
    from core.state import zusammenbau

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", "true")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "profil1"))
    monkeypatch.setenv("AAA_LEASE_DIR", str(tmp_path / "gemeinsam"))
    lease._vergessen_fuer_tests()
    lease.setze_konto_id(None)
    yield tmp_path
    lease._vergessen_fuer_tests()
    lease.setze_konto_id(None)
    await zusammenbau.schliesse_alle()


@pytest.fixture
def warnungen(monkeypatch):
    """Die WARNING-Meldungen von ``core.lease``, fertig formatiert.

    Ein Spion statt caplog: Der Engine-Import baut die Root-Handler neu (structured_logging),
    und im gemeinsamen Lauf mit anderen Testdateien verlor caplog eine Meldung, die abging.
    """
    from core import lease

    spion = MagicMock()
    monkeypatch.setattr(lease.logger, "warning", spion)
    return lambda: [c.args[0] % c.args[1:] for c in spion.call_args_list]


# ── Schluessel ────────────────────────────────────────────────────────────────


def test_flag_aus_schluessel_wie_heute(monkeypatch):
    from core import lease

    monkeypatch.delenv("STRATEGY_PROFILES_ENABLED", raising=False)
    lease.setze_konto_id("PA123")
    try:
        assert lease.konto_schluessel(None, paper=True) == "global:paper"
        assert lease.konto_schluessel("u1", paper=False) == "u1:live"
        assert (
            lease.konto_schluessel(None, paper=True, konto_id="PA9") == "global:paper"
        )
    finally:
        lease.setze_konto_id(None)


def test_flag_an_schluessel_ist_die_konto_id_ohne_modus(monkeypatch):
    from core import lease

    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", "true")
    assert lease.konto_schluessel(None, paper=True, konto_id="PA123") == "alpaca:PA123"
    assert lease.konto_schluessel("u1", paper=False, konto_id="PA123") == "alpaca:PA123"


def test_flag_an_ohne_angabe_gilt_die_vermerkte_konto_id(monkeypatch):
    """Alle drei Aufrufer muessen dasselbe Konto meinen — darum liest der Schluessel die
    vermerkte ID selbst, statt dass ein vergessener Aufrufer einen anderen Schluessel baut.
    """
    from core import lease

    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", "true")
    lease.setze_konto_id("PA777")
    try:
        assert lease.konto_schluessel(None, paper=True) == "alpaca:PA777"
        assert lease.konto_schluessel("global", paper=False) == "alpaca:PA777"
    finally:
        lease.setze_konto_id(None)


# ── Konto-ID ermitteln ────────────────────────────────────────────────────────


async def test_konto_id_aus_get_account_account_number_vor_id(profile):
    from core import lease

    api = MagicMock()
    api.get_account.return_value = SimpleNamespace(account_number="PA123", id="uuid-1")
    assert await lease.ermittle_konto_id(api) == "PA123"
    assert lease.konto_id() == "PA123"


async def test_konto_id_faellt_auf_id_zurueck(profile):
    from core import lease

    api = MagicMock()
    api.get_account.return_value = SimpleNamespace(account_number=None, id="uuid-1")
    assert await lease.ermittle_konto_id(api) == "uuid-1"


async def test_konto_id_wird_nur_einmal_abgefragt(profile):
    from core import lease

    api = MagicMock()
    api.get_account.return_value = SimpleNamespace(account_number="PA1", id=None)
    await lease.ermittle_konto_id(api)
    await lease.ermittle_konto_id(api)
    assert api.get_account.call_count == 1


async def test_get_account_scheitert_meldet_warning(profile, warnungen):
    from core import lease

    api = MagicMock()
    api.get_account.side_effect = RuntimeError("Alpaca nicht erreichbar")
    assert await lease.ermittle_konto_id(api) is None
    assert lease.konto_id() is None
    assert any("Konto-ID unbekannt" in t for t in warnungen())


async def test_flag_aus_fragt_keine_konto_id_ab(monkeypatch):
    from core import lease

    monkeypatch.delenv("STRATEGY_PROFILES_ENABLED", raising=False)
    api = MagicMock()
    assert await lease.ermittle_konto_id(api) is None
    api.get_account.assert_not_called()


# ── Fail-closed ───────────────────────────────────────────────────────────────


async def test_unbekannte_konto_id_darf_nicht_schreiben(profile, warnungen):
    from core import lease

    konto = lease.konto_schluessel(None, paper=True)
    assert await lease.darf_schreiben(konto) is False
    assert any("Konto-ID unbekannt" in t for t in warnungen())


async def test_bekannte_konto_id_darf_schreiben(profile):
    from core import lease

    lease.setze_konto_id("PA123")
    assert await lease.darf_schreiben(lease.konto_schluessel(None, paper=True))


# ── Gemeinsame Ablage ─────────────────────────────────────────────────────────


async def test_zweites_profil_mit_derselben_konto_id_wird_gesperrt(
    profile, monkeypatch, warnungen
):
    from core import lease

    konto = lease.konto_schluessel(None, paper=True, konto_id="PA123")
    assert konto == "alpaca:PA123"
    assert await lease._erwirb_auf_diesem_ring(konto, "engine-1") is not None

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(profile / "profil2"))
    assert await lease._erwirb_auf_diesem_ring(konto, "engine-2") is None
    assert any("alpaca:PA123" in t for t in warnungen())


async def test_zweites_profil_mit_anderer_konto_id_bekommt_seine(profile, monkeypatch):
    from core import lease

    assert await lease._erwirb_auf_diesem_ring("alpaca:PA1", "engine-1") is not None
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(profile / "profil2"))
    assert await lease._erwirb_auf_diesem_ring("alpaca:PA2", "engine-2") is not None


async def test_gegenprobe_ohne_gemeinsame_ablage_sperrt_nichts(profile, monkeypatch):
    """Der Befund aus Plan §1: Hinge die Sperre am Datenverzeichnis, bekaeme das zweite
    Profil dasselbe Konto. Haelt fest, warum ``AAA_LEASE_DIR`` gebraucht wird."""
    from core import lease

    monkeypatch.delenv("AAA_LEASE_DIR")
    assert await lease._erwirb_auf_diesem_ring("alpaca:PA123", "engine-1") is not None
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(profile / "profil2"))
    assert await lease._erwirb_auf_diesem_ring("alpaca:PA123", "engine-2") is not None


def test_aaa_lease_dir_allein_macht_die_sperre_aktiv(monkeypatch):
    from core import lease

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", "true")
    monkeypatch.setenv("AAA_LEASE_DIR", "irgendwo")
    lease._vergessen_fuer_tests()
    assert lease.aktiv() is True


# ── Schleifenstart ────────────────────────────────────────────────────────────


async def test_schleifenstart_ermittelt_die_konto_id_ueber_self_api(profile):
    from core.engine.schleifen_start import StartMixin

    engine = StartMixin()
    engine.api = MagicMock()
    engine.api.get_account.return_value = SimpleNamespace(
        account_number="PA55", id=None
    )

    assert await engine._sichere_schreibberechtigung() is True

    from core import lease

    assert lease.konto_id() == "PA55"
    assert lease.eintrag("alpaca:PA55") is not None


async def test_schleifenstart_ohne_konto_id_haelt_zurueck(profile):
    from core.engine.schleifen_start import StartMixin

    engine = StartMixin()
    engine.api = None

    assert await engine._sichere_schreibberechtigung() is False


def test_synchroner_haken_meint_dasselbe_konto(profile):
    from core import lease
    from core.engine.lease import erwerbe_schreibberechtigung

    lease.setze_konto_id("PA321")
    assert erwerbe_schreibberechtigung(konto="global", instanz="haken") is True
    assert lease.eintrag("alpaca:PA321") is not None


# ── Order-Tor ─────────────────────────────────────────────────────────────────


async def test_order_tor_haelt_bei_unbekannter_konto_id_zurueck(profile, warnungen):
    """Gherkin „Konto-ID unbekannt": ``_tor_gesperrt`` liefert den Halt-Text, gesendet wird
    nichts. Das Tor selbst bleibt unveraendert — der Schluessel liest die vermerkte ID.
    """
    from core.engine.order_executor import OrderExecutorMixin

    grund = await OrderExecutorMixin._tor_gesperrt(
        symbol="AAPL", user_id="global", art="exit"
    )

    assert grund and "keine Schreibberechtigung" in grund
    assert any("Konto-ID unbekannt" in t for t in warnungen())


async def test_order_tor_sperrt_das_zweite_profil_auf_demselben_konto(
    profile, monkeypatch
):
    from core import lease
    from core.engine.order_executor import OrderExecutorMixin

    assert await lease._erwirb_auf_diesem_ring("alpaca:PA123", "fremde-engine")
    lease._vergessen_fuer_tests()  # dieser Prozess ist ab jetzt „die zweite Engine"
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(profile / "profil2"))
    lease.setze_konto_id("PA123")

    grund = await OrderExecutorMixin._tor_gesperrt(
        symbol="AAPL", user_id="global", art="exit"
    )
    assert grund and "alpaca:PA123" in grund
