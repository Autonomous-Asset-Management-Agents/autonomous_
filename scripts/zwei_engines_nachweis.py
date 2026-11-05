"""#4631 (STW-5) — Machbarkeitsnachweis: zwei echte Engines nebeneinander (manuell, nicht CI).

Startet zwei ``core.engine``-Prozesse mit ``STRATEGY_PROFILES_ENABLED=true``, je eigenem
``AAA_USER_DATA_DIR``, ``ENGINE_PORT``, ``SENATE_LOG_DIR`` und ``AAA_KEY_SLOT`` und einem
gemeinsamen ``AAA_LEASE_DIR`` — so, wie der Launcher (#4632) es tun wird. Jeder Prozess ruft
vor dem Engine-Start ausdruecklich ``load_secrets_from_keychain()`` fuer seinen Platz auf.

Gemeldet wird:

a) jede neue oder geaenderte Datei ausserhalb der Datenverzeichnisse (Schnappschuss des
   Bundle-Verzeichnisses ``ai_trading_bot/`` und von ``DATA_DIR`` vorher und nachher),
b) welche Engine die Schreibberechtigung bekam und welche die Sperre meldete.

Braucht echte Paper-Keys auf den Plaetzen (``python -m core.keychain_cli`` bzw. Setup-Wizard);
darum laeuft es von Hand. Aufruf aus ``ai_trading_bot/``::

    python scripts/zwei_engines_nachweis.py                  # paper1 + paper2, zwei Konten
    python scripts/zwei_engines_nachweis.py --gleicher-platz # beide paper1: Sperre erwartet
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PAKET = Path(__file__).resolve().parents[1]  # ai_trading_bot/
START = (
    "try:\n"
    "    import torch\n"
    "except ImportError:\n"
    "    pass\n"
    "from core.keychain import load_secrets_from_keychain; "
    "load_secrets_from_keychain(); "
    "import runpy; runpy.run_module('core.engine', run_name='__main__')"
)


def _stand(wurzel: Path, rekursiv: bool) -> dict[str, float]:
    dateien = wurzel.rglob("*") if rekursiv else wurzel.iterdir()
    return {
        str(p.relative_to(PAKET)): p.stat().st_mtime
        for p in dateien
        if p.is_file() and "__pycache__" not in p.parts
    }


def _schnappschuss() -> dict[str, float]:
    return {**_stand(PAKET, rekursiv=False), **_stand(PAKET / "data", rekursiv=True)}


def _starte(basis: Path, nr: int, platz: str, port: int, log) -> subprocess.Popen:
    profil = basis / f"profil{nr}"
    profil.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ALPACA_")}
    env.update(
        STRATEGY_PROFILES_ENABLED="true",
        AAA_USER_DATA_DIR=str(profil),
        AAA_LEASE_DIR=str(basis / "gemeinsam"),
        SENATE_LOG_DIR=str(profil / "cloud_fallback_logs"),
        AAA_KEY_SLOT=platz,
        ENGINE_PORT=str(port),
        ENGINE_HOST="127.0.0.1",
        DEPLOYMENT_MODE="LOCAL",
        PAPER_TRADING="true",
        REDIS_URL="",
        DATABASE_URL="",
        AUTO_START_STRATEGY="true",
        PYTHONUNBUFFERED="1",
        PYTHONIOENCODING="utf-8",
    )
    return subprocess.Popen(
        [sys.executable, "-u", "-c", START],
        cwd=PAKET,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


def main() -> int:
    teil = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    teil.add_argument("--basis", type=Path, default=None)
    teil.add_argument("--dauer", type=int, default=180, help="Sekunden Laufzeit")
    teil.add_argument("--ports", type=int, nargs=2, default=(18201, 18202))
    teil.add_argument("--gleicher-platz", action="store_true")
    arg = teil.parse_args()

    basis = arg.basis or Path(tempfile.mkdtemp(prefix="aaa_zwei_engines_"))
    plaetze = ("paper1", "paper1") if arg.gleicher_platz else ("paper1", "paper2")
    vorher = _schnappschuss()

    logs, prozesse = [], []
    for nr, (platz, port) in enumerate(zip(plaetze, arg.ports), start=1):
        log = open(basis / f"engine{nr}.log", "w", encoding="utf-8")
        logs.append(log)
        prozesse.append(_starte(basis, nr, platz, port, log))
        time.sleep(20)  # die erste Engine erwirbt zuerst
    time.sleep(arg.dauer)
    for p in prozesse:
        p.terminate()
    for p, log in zip(prozesse, logs):
        try:
            p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            p.kill()
        log.close()

    nachher = _schnappschuss()
    fremd = sorted(k for k, t in nachher.items() if vorher.get(k) != t)
    print(f"Basis: {basis}")
    print(f"a) Dateien ausserhalb der Datenverzeichnisse: {fremd or 'keine'}")
    for nr in (1, 2):
        text = (basis / f"engine{nr}.log").read_text(encoding="utf-8", errors="replace")
        zeilen = [z for z in text.splitlines() if "EngineLease:" in z]
        print(f"b) Engine {nr} ({plaetze[nr - 1]}):")
        for z in zeilen[:5] or ["(keine Lease-Meldung — Log pruefen)"]:
            print(f"     {z}")
    return 1 if fremd else 0


if __name__ == "__main__":
    sys.exit(main())
