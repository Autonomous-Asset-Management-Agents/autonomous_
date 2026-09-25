"""#3384 — Chaos-Vorrichtung: toetet einen Order-Durchlauf an drei benannten Punkten.

Die Vorrichtung startet ``tests/chain/_ein_intent.py`` als eigenen Prozess, laesst ihn
an einem der drei Punkte hart sterben und kann denselben Durchlauf danach **im selben
Datenverzeichnis** erneut starten. Genau das ist der Unterschied zu einem Test im
eigenen Prozess: Wiederhergestellt werden kann nur, was auf der Platte liegt.

Benutzung::

    vorrichtung = ChaosVorrichtung(tmp_path, seed=7, uhr="2026-09-16 09:35")
    erst = vorrichtung.lauf(toeten_bei="zwischen_absenden_und_bestaetigung")
    zweit = vorrichtung.lauf()              # Neustart, gleiches Verzeichnis
    vorrichtung.broker_orders()             # was der Broker insgesamt gesehen hat

``seed`` und ``uhr`` sind **Pflicht**. Ohne feste Uhr und festen Seed sind zwei
Laeufe nicht vergleichbar (Streuung des Sim-Harness, #3376); ein Lauf ohne beide ist
ein Konfigurationsfehler, kein Vorgabewert.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ._ein_intent import MARKE_SAUBERES_ENDE, TOETUNGSPUNKTE

#: Wurzel des Backends (``ai_trading_bot/``) — von dort ist ``core`` und ``tests`` importierbar.
_WURZEL = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Lauf:
    """Das Ergebnis eines Durchlaufs."""

    toeten_bei: Optional[str]
    rueckgabecode: int
    sauber_beendet: bool
    ausgabe: str

    @property
    def wurde_getoetet(self) -> bool:
        return self.toeten_bei is not None and not self.sauber_beendet


class ChaosVorrichtung:
    def __init__(
        self,
        datenverzeichnis: str | os.PathLike[str],
        *,
        seed: int,
        uhr: str,
        symbol: str = "AAPL",
        preis: float = 100.0,
        menge: float = 1.0,
        zeitgrenze_s: float = 120.0,
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

    # -- Durchlauf ----------------------------------------------------------

    def lauf(
        self,
        *,
        toeten_bei: Optional[str] = None,
        modul: str = "tests.chain._ein_intent",
        umgebung_zusatz: Optional[Dict[str, str]] = None,
    ) -> Lauf:
        """Ein Prozesslauf. ``modul`` und ``umgebung_zusatz`` fuer weitere Abnahmen (#3487);
        ohne sie laeuft alles wie bisher."""
        if toeten_bei is not None and toeten_bei not in TOETUNGSPUNKTE:
            raise ValueError(
                f"Unbekannter Toetungspunkt {toeten_bei!r}; bekannt: {TOETUNGSPUNKTE}"
            )
        marke = self.verzeichnis / MARKE_SAUBERES_ENDE
        if marke.exists():
            marke.unlink()

        umgebung = dict(os.environ)
        umgebung.update(
            {
                "PYTHONPATH": str(_WURZEL),
                "KETTE_DATENVERZEICHNIS": str(self.verzeichnis),
                "KETTE_TOETEN_BEI": toeten_bei or "",
                "KETTE_SYMBOL": self._symbol,
                "KETTE_PREIS": str(self._preis),
                "KETTE_MENGE": str(self._menge),
                # Feste Uhr und fester Seed — siehe Klassendokumentation.
                "PYTHONHASHSEED": str(self._seed),
                "AAA_SIM_MODE": "true",
                "AAA_SIM_DATE": self._uhr.split(" ")[0],
                # Kein Redis, keine Cloud, kein Netz.
                "REDIS_URL": "",
                # #3449: das Datenverzeichnis des Laufs ist das Nutzerverzeichnis — so,
                # wie es der Desktop-Launcher in Produktion immer setzt. Dort liegt die
                # Outbox (engine_state.db), und dort sucht der Neustart sie. Ohne diese
                # Zeile teilten sich alle Laeufe eine Datei im Arbeitsverzeichnis.
                "AAA_USER_DATA_DIR": str(self.verzeichnis),
                "AUTO_START_STRATEGY": "False",
                "ENABLE_HEARTBEAT": "False",
                "CLOUD_LOGGING_ENABLED": "false",
            }
        )
        umgebung.update(umgebung_zusatz or {})

        fertig = subprocess.run(
            [sys.executable, "-m", modul],
            cwd=str(_WURZEL),
            env=umgebung,
            capture_output=True,
            text=True,
            timeout=self._zeitgrenze,
        )
        return Lauf(
            toeten_bei=toeten_bei,
            rueckgabecode=fertig.returncode,
            sauber_beendet=marke.exists(),
            ausgabe=(fertig.stdout or "") + (fertig.stderr or ""),
        )

    # -- Auswertung ---------------------------------------------------------

    def broker_orders(self) -> List[Dict[str, Any]]:
        """Was der Broker angenommen hat — ueber alle Prozesse hinweg."""
        return self._jsonl("auftragsbuch.jsonl")

    def unsere_saetze(self) -> List[Dict[str, Any]]:
        """Was *unsere* Seite dauerhaft weiss."""
        return self._jsonl("unsere_saetze.jsonl")

    def beobachtung(self) -> List[Dict[str, Any]]:
        """Die Spur der Vorrichtung — Ereignisse, keine Systemzustaende."""
        return self._jsonl("beobachtung.jsonl")

    def _jsonl(self, name: str) -> List[Dict[str, Any]]:
        pfad = self.verzeichnis / name
        if not pfad.exists():
            return []
        return [
            json.loads(z)
            for z in pfad.read_text(encoding="utf-8").splitlines()
            if z.strip()
        ]
