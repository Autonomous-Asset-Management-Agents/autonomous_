"""#3384 — Zwei-Instanzen-Vorrichtung: zwei Engine-Prozesse auf demselben Konto.

Zwei **Prozesse**, nicht zwei Threads. Der Plan verwirft die Thread-Variante
ausdruecklich (§4, Option C): Zwei Threads teilen sich einen Interpreter und damit
jeden prozesslokalen Zustand. Der zu pruefende Fall ist aber gerade der zweite
*Prozess* — auf Enterprise die zweite Cloud-Run-Instanz, auf dem Desktop die zweite
gestartete App. Ein Thread-Test wuerde den No-Op-Lock aus ``local_state_client.py:94-95``
nicht auffliegen lassen.

Beide Instanzen teilen sich ein Datenverzeichnis und damit dasselbe Auftragsbuch —
das ist die Nachbildung des einen Kontos beim Broker.

``seed`` und ``uhr`` sind Pflicht; siehe ``chaos.ChaosVorrichtung``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .chaos import _WURZEL, ChaosVorrichtung


@dataclass(frozen=True)
class Instanz:
    name: str
    rueckgabecode: int
    ausgabe: str


class ZweiInstanzenVorrichtung:
    def __init__(
        self,
        datenverzeichnis: str | os.PathLike[str],
        *,
        seed: int,
        uhr: str,
        symbol: str = "AAPL",
        preis: float = 100.0,
        menge: float = 1.0,
        zeitgrenze_s: float = 180.0,
        modul: str = "tests.chain._ein_intent",
    ):
        if seed is None or not uhr:
            raise ValueError(
                "seed und uhr sind Pflicht — ohne beide sind zwei Laeufe nicht "
                "vergleichbar (#3376)."
            )
        self.verzeichnis = Path(datenverzeichnis)
        self.verzeichnis.mkdir(parents=True, exist_ok=True)
        self._seed = seed
        self._uhr = uhr
        self._symbol = symbol
        self._preis = preis
        self._menge = menge
        self._zeitgrenze = zeitgrenze_s
        # #3489: welcher Prozess laeuft — ohne Angabe wie bisher der eine Intent.
        self._modul = modul
        # Nur zur Auswertung — die Vorrichtung startet ihre Prozesse selbst.
        self._auswertung = ChaosVorrichtung(
            self.verzeichnis,
            seed=seed,
            uhr=uhr,
            symbol=symbol,
            preis=preis,
            menge=menge,
        )

    def _umgebung(self, instanz: str) -> Dict[str, str]:
        umgebung = dict(os.environ)
        umgebung.update(
            {
                "PYTHONPATH": str(_WURZEL),
                "KETTE_DATENVERZEICHNIS": str(self.verzeichnis),
                "AAA_USER_DATA_DIR": str(self.verzeichnis),
                "KETTE_TOETEN_BEI": "",
                "KETTE_INSTANZ": instanz,
                # Beide warten nach der Berechtigungsfrage aufeinander (_gleichzeitig):
                # geprueft werden zwei GLEICHZEITIG laufende Instanzen.
                "KETTE_GLEICHZEITIG": "2",
                "KETTE_SYMBOL": self._symbol,
                "KETTE_PREIS": str(self._preis),
                "KETTE_MENGE": str(self._menge),
                "PYTHONHASHSEED": str(self._seed),
                "AAA_SIM_MODE": "true",
                "AAA_SIM_DATE": self._uhr.split(" ")[0],
                "REDIS_URL": "",
                "AUTO_START_STRATEGY": "False",
                "ENABLE_HEARTBEAT": "False",
                "CLOUD_LOGGING_ENABLED": "false",
            }
        )
        return umgebung

    def beide_starten(self) -> List[Instanz]:
        """Startet beide Instanzen gleichzeitig und wartet auf beide."""
        prozesse = {
            name: subprocess.Popen(
                [sys.executable, "-m", self._modul],
                cwd=str(_WURZEL),
                env=self._umgebung(name),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for name in ("instanz-a", "instanz-b")
        }
        ergebnisse = []
        for name, p in prozesse.items():
            ausgabe, _ = p.communicate(timeout=self._zeitgrenze)
            ergebnisse.append(Instanz(name, p.returncode, ausgabe or ""))
        return ergebnisse

    # -- Auswertung ---------------------------------------------------------

    def broker_orders(self) -> List[Dict[str, Any]]:
        return self._auswertung.broker_orders()

    def beobachtung(self) -> List[Dict[str, Any]]:
        return self._auswertung.beobachtung()

    def instanzen_die_gehandelt_haben(self) -> List[str]:
        """Welche Instanzen haben tatsaechlich eine Order abgesetzt?

        Abgeleitet aus der Beobachtungsspur: eine Instanz, die ``nicht_gehandelt``
        meldet, hat keine Order abgesetzt. Heute meldet das keine — es gibt keine
        Berechtigung, die es verhindern koennte.
        """
        nicht = {
            e.get("instanz")
            for e in self.beobachtung()
            if e.get("ereignis") == "nicht_gehandelt"
        }
        return [n for n in ("instanz-a", "instanz-b") if n not in nicht]
