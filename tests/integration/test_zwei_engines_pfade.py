"""#4631 (STW-5) — Zwei Engine-Prozesse nebeneinander: getrennte Daten, gemeinsame Sperre.

Zwei Unterprozesse mit Flag an, je eigenem ``AAA_USER_DATA_DIR``, ``SENATE_LOG_DIR``,
``ENGINE_PORT`` und ``AAA_KEY_SLOT`` und gemeinsamem ``AAA_LEASE_DIR``. Jeder fuehrt einen
Pfad-Schreibzyklus auf den **echten** Schreibern aus (Marktdaten-Cache von
``HistoricalDataProvider``, ``_save_benchmark_comparison_csv``, Altdatei-Uebernahme beim Import)
und erwirbt die Schreibberechtigung fuer seine Konto-ID aus einer Broker-Attrappe.

Geprueft: Im Arbeitsverzeichnis und unter ``DATA_DIR`` entsteht keine Datei; mit verschiedenen
IDs erhalten beide die Berechtigung, mit gleicher ID wird der zweite gesperrt.

**Kein Schreiben ins Bundle, auch nicht im Rotlauf.** Liegt ein Ziel ausserhalb des
Profils, meldet das Kind den Pfad, statt zu schreiben — ein Test darf ``ai_trading_bot/data``
nicht veraendern.

Plan: ``docs/4631-*/implementation_plan.md`` §5 (Szenarien 1 und 2), TDD-Schritt 6.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
DATA_DIR = PAKET / "data"
DATA_DIR_DATEIEN = (
    "trade_intelligence.json",
    "ai_learned_rules.json",
    "benchmark_equity_comparison.csv",
)

KIND = r"""
try:
    import torch
except ImportError:
    pass
import asyncio, json, os, sys
from types import SimpleNamespace

nutzer = os.path.abspath(os.environ["AAA_USER_DATA_DIR"])
ausserhalb = []

def im_profil(pfad):
    if os.path.abspath(pfad).startswith(nutzer + os.sep):
        return True
    ausserhalb.append(os.path.abspath(pfad))
    return False

import config  # settings: LEARNED_RULES_FILE, BENCHMARK_COMPARISON_CSV
from core import lease
from core import data_provider
from core import trade_intelligence

for pfad in (
    config.LEARNED_RULES_FILE,
    config.BENCHMARK_COMPARISON_CSV,
    trade_intelligence.TRADE_INTELLIGENCE_FILE,
    data_provider.DATA_CACHE_DIR,
):
    im_profil(pfad)

if im_profil(data_provider.DATA_CACHE_DIR):
    data_provider.HistoricalDataProvider(api=None)
if im_profil(config.BENCHMARK_COMPARISON_CSV):
    from core.engine.simulation_runner import SimulationRunnerMixin

    punkte = [{"date": "2026-10-01", "equity": 100.0}]
    SimulationRunnerMixin._save_benchmark_comparison_csv(None, punkte, punkte, 100.0)


class Broker:
    def get_account(self):
        return SimpleNamespace(account_number=os.environ["TEST_KONTO_ID"], id=None)


async def main():
    await lease.ermittle_konto_id(Broker())
    schluessel = lease.konto_schluessel(None, paper=True)
    berechtigt = await lease.sichere_berechtigung(schluessel)
    print("ERGEBNIS " + json.dumps(
        {"schluessel": schluessel, "berechtigt": berechtigt, "ausserhalb": ausserhalb}
    ), flush=True)
    sys.stdin.readline()  # haelt die Berechtigung, bis der Test fertig ist
    await lease.beende_auf_diesem_ring()

asyncio.run(main())
"""


def _schnappschuss(verzeichnis: Path) -> set[str]:
    return {str(p.relative_to(verzeichnis)) for p in verzeichnis.rglob("*")}


def _data_dir_stand() -> dict[str, float | None]:
    stand: dict[str, float | None] = {}
    for name in (*DATA_DIR_DATEIEN, "market_data_cache"):
        pfad = DATA_DIR / name
        stand[name] = pfad.stat().st_mtime if pfad.exists() else None
    return stand


def _umgebung(tmp_path: Path, nr: int, konto_id: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "REDIS_URL"}
    profil = tmp_path / f"profil{nr}"
    env.update(
        STRATEGY_PROFILES_ENABLED="true",
        AAA_USER_DATA_DIR=str(profil),
        AAA_LEASE_DIR=str(tmp_path / "gemeinsam"),
        SENATE_LOG_DIR=str(profil / "cloud_fallback_logs"),
        AAA_KEY_SLOT=f"paper{nr}",
        ENGINE_PORT=str(18100 + nr),
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="True",
        TEST_KONTO_ID=konto_id,
        PYTHONPATH=str(PAKET),
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _starte(tmp_path: Path, arbeit: Path, nr: int, konto_id: str):
    return subprocess.Popen(
        [sys.executable, "-c", KIND],
        cwd=arbeit,
        env=_umgebung(tmp_path, nr, konto_id),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _ergebnis(prozess) -> dict:
    for zeile in prozess.stdout:
        if zeile.startswith("ERGEBNIS "):
            return json.loads(zeile[len("ERGEBNIS ") :])
    _, fehler = prozess.communicate(timeout=30)
    raise AssertionError(f"Kind ohne Ergebnis (rc={prozess.returncode}):\n{fehler}")


def _beende(*prozesse) -> None:
    for p in prozesse:
        try:
            p.communicate(input="\n", timeout=60)
        except Exception:  # noqa: BLE001 — Aufraeumen
            p.kill()


@pytest.fixture
def arbeit(tmp_path):
    verzeichnis = tmp_path / "arbeit"
    verzeichnis.mkdir()
    return verzeichnis


def test_zwei_engines_verschiedene_konten_schreiben_nur_ins_eigene_profil(
    tmp_path, arbeit
):
    vorher = _data_dir_stand()

    eins = _starte(tmp_path, arbeit, 1, "PA111")
    try:
        e1 = _ergebnis(eins)
        zwei = _starte(tmp_path, arbeit, 2, "PA222")
        try:
            e2 = _ergebnis(zwei)
        finally:
            _beende(zwei)
    finally:
        _beende(eins)

    assert e1["ausserhalb"] == [], e1["ausserhalb"]
    assert e2["ausserhalb"] == [], e2["ausserhalb"]
    assert _schnappschuss(arbeit) == set(), "Datei im Arbeitsverzeichnis"
    assert _data_dir_stand() == vorher, "Datei unter DATA_DIR geschrieben"
    for nr in (1, 2):
        profil = tmp_path / f"profil{nr}"
        assert (profil / "market_data_cache").is_dir()
        assert (profil / "benchmark_equity_comparison.csv").is_file()

    assert (e1["schluessel"], e1["berechtigt"]) == ("alpaca:PA111", True)
    assert (e2["schluessel"], e2["berechtigt"]) == ("alpaca:PA222", True)


def test_zweite_engine_auf_demselben_konto_wird_gesperrt(tmp_path, arbeit):
    eins = _starte(tmp_path, arbeit, 1, "PA123")
    try:
        e1 = _ergebnis(eins)
        zwei = _starte(tmp_path, arbeit, 2, "PA123")
        try:
            e2 = _ergebnis(zwei)
        finally:
            _beende(zwei)
    finally:
        _beende(eins)

    assert (e1["schluessel"], e1["berechtigt"]) == ("alpaca:PA123", True)
    assert (e2["schluessel"], e2["berechtigt"]) == ("alpaca:PA123", False)
