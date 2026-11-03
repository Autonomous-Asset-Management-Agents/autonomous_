"""#4631 (STW-5) — Datenpfade je Profil: ``core.profil_pfade``.

Bei ``STRATEGY_PROFILES_ENABLED`` aus liefert jeder Helfer **byte-gleich** den Altpfad. Bei an
liegen veraenderliche Dateien unter ``AAA_USER_DATA_DIR`` — dem Datenverzeichnis des Profils —,
und eine vorhandene Altdatei wird einmal **kopiert**, nie verschoben (Rollback per Flag).

Plan: ``docs/4631-*/implementation_plan.md`` §2.1, TDD-Schritt 1.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


@pytest.fixture
def profil(monkeypatch, tmp_path):
    """Flag an, eigenes Datenverzeichnis; ``AAA_LEASE_DIR`` ungesetzt."""
    nutzer = tmp_path / "profil"
    nutzer.mkdir()
    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", "true")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(nutzer))
    monkeypatch.delenv("AAA_LEASE_DIR", raising=False)
    return nutzer


@pytest.fixture
def flag_aus(monkeypatch, tmp_path):
    monkeypatch.delenv("STRATEGY_PROFILES_ENABLED", raising=False)
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "profil"))
    monkeypatch.delenv("AAA_LEASE_DIR", raising=False)


@pytest.mark.parametrize("wert", ["", "false", "0", "yes", "1"])
def test_aktiv_folgt_genau_der_lesart_von_settings(monkeypatch, wert):
    """Dieselbe Lesart wie ``settings.STRATEGY_PROFILES_ENABLED`` (``== "true"``): Ein Pfad
    darf nicht umschalten, waehrend die Konfiguration das Flag fuer aus haelt."""
    from core import profil_pfade

    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", wert)
    assert profil_pfade.aktiv() is False


@pytest.mark.parametrize("wert", ["true", "True", "TRUE"])
def test_aktiv_bei_true(monkeypatch, wert):
    from core import profil_pfade

    monkeypatch.setenv("STRATEGY_PROFILES_ENABLED", wert)
    assert profil_pfade.aktiv() is True


def test_flag_aus_ergibt_die_altpfade_byte_gleich(flag_aus, tmp_path):
    from core import profil_pfade

    alt = os.path.join(str(tmp_path), "data", "trade_intelligence.json")
    assert profil_pfade.nutzer_datei(alt) == alt
    assert profil_pfade.markt_cache_verzeichnis() == "market_data_cache"


def test_flag_aus_ignoriert_aaa_lease_dir(monkeypatch, flag_aus, tmp_path):
    from core import profil_pfade
    from core.state import zusammenbau

    monkeypatch.setenv("AAA_LEASE_DIR", str(tmp_path / "gemeinsam"))
    assert profil_pfade.sperr_ablage_datei() == zusammenbau.ablage_pfad()


def test_flag_an_legt_die_datei_ins_datenverzeichnis(profil, tmp_path):
    from core import profil_pfade

    alt = os.path.join(str(tmp_path), "bundle", "data", "ai_learned_rules.json")
    assert profil_pfade.nutzer_datei(alt) == os.path.join(
        str(profil), "ai_learned_rules.json"
    )


def test_flag_an_ohne_datenverzeichnis_bleibt_beim_altpfad(
    monkeypatch, profil, tmp_path
):
    from core import profil_pfade

    monkeypatch.delenv("AAA_USER_DATA_DIR")
    alt = os.path.join(str(tmp_path), "data", "x.json")
    assert profil_pfade.nutzer_datei(alt) == alt
    assert profil_pfade.markt_cache_verzeichnis() == os.path.join(
        ".", "market_data_cache"
    )


def test_flag_an_markt_cache_unter_dem_datenverzeichnis(profil):
    from core import profil_pfade

    assert profil_pfade.markt_cache_verzeichnis() == os.path.join(
        str(profil), "market_data_cache"
    )


def test_altdatei_wird_kopiert_nicht_verschoben(profil, tmp_path):
    from core import profil_pfade

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    alt = bundle / "trade_intelligence.json"
    alt.write_text('{"completed_trades": [1]}', encoding="utf-8")

    neu = profil_pfade.nutzer_datei(str(alt))

    assert alt.read_text(encoding="utf-8") == '{"completed_trades": [1]}'
    assert open(neu, encoding="utf-8").read() == '{"completed_trades": [1]}'


def test_vorhandene_neue_datei_wird_nicht_ueberschrieben(profil, tmp_path):
    from core import profil_pfade

    alt = tmp_path / "alt.json"
    alt.write_text("alt", encoding="utf-8")
    neu = profil / "alt.json"
    neu.write_text("eigen", encoding="utf-8")

    profil_pfade.uebernimm_altdatei(str(alt), str(neu))

    assert neu.read_text(encoding="utf-8") == "eigen"


def test_uebernahme_wirft_nie_und_meldet_bei_warning(profil, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from core import profil_pfade

    alt = tmp_path / "alt.json"
    alt.write_text("alt", encoding="utf-8")
    # Ziel liegt unter einer DATEI statt einem Verzeichnis -> das Kopieren muss scheitern.
    sperre = profil / "keinverzeichnis"
    sperre.write_text("", encoding="utf-8")
    ziel = sperre / "alt.json"
    # Spion statt caplog: unabhaengig davon, was andere Tests am Logging umgebaut haben.
    warnung = MagicMock()
    monkeypatch.setattr(profil_pfade.logger, "warning", warnung)

    profil_pfade.uebernimm_altdatei(str(alt), str(ziel))

    assert not ziel.exists()
    assert warnung.call_count == 1
    assert "alt.json" in warnung.call_args.args[0] % warnung.call_args.args[1:]


def test_ohne_altdatei_keine_uebernahme(profil, tmp_path):
    from core import profil_pfade

    neu = profil_pfade.nutzer_datei(str(tmp_path / "gibt_es_nicht.json"))
    assert not os.path.exists(neu)


def test_sperr_ablage_liegt_im_gemeinsamen_verzeichnis(monkeypatch, profil, tmp_path):
    from core import profil_pfade

    gemeinsam = tmp_path / "gemeinsam"
    monkeypatch.setenv("AAA_LEASE_DIR", str(gemeinsam))
    assert profil_pfade.sperr_ablage_datei() == os.path.join(
        str(gemeinsam), "engine_state.db"
    )
    assert gemeinsam.is_dir()


def test_sperr_ablage_ohne_lease_dir_wie_heute(profil):
    from core import profil_pfade
    from core.state import zusammenbau

    assert profil_pfade.sperr_ablage_datei() == zusammenbau.ablage_pfad()
