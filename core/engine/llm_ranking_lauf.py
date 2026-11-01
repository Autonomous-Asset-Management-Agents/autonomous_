"""Lauf-Protokoll, Rhythmus und Gueltigkeit der LLM-Vorauswahl (#4650, Epic #4638).

Der gespeicherte Prompt (`core.engine.llm_ranking.frage_llm_ranking`) laeuft im gewaehlten
`Rhythmus`, getaktet ueber `rank_funnel.should_refresh_rank` mit `takt_minuten`: fuer die alten
Rhythmen ist das der Ranking-Refresh, #4960 `stuendlich` und `alle_4_stunden` haben einen eigenen
Takt, unabhaengig von `ROUND_TABLE_RANK_REFRESH_MINUTES`. Jeder Lauf,
erfolgreich oder nicht, landet als eine JSON-Zeile im `LaufSpeicher` — so laesst sich jede
Order auf den Lauf zurueckfuehren, der ihr Symbol in die Bewertung brachte. Die gueltige Liste
ist die des letzten erfolgreichen Laufs, bis die `hoechstdauer` des Rhythmus erreicht ist;
danach ist sie `abgelaufen` und wird nicht still weiter genutzt.

Noch ohne Engine-Anbindung; die folgt mit Sub-Issue 3. Ein Schreibfehler kostet nie einen
Zyklus: er wird als WARNING geloggt, der Lauf bleibt im Prozess erhalten.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

from core.audit_paths import resolve_audit_log_path
from core.engine.llm_ranking import LlmRankingErgebnis
from core.sim import is_sim_mode
from core.sim.clock import engine_now

logger = logging.getLogger(__name__)

_MARKT_TZ = ZoneInfo("America/New_York")
DATEINAME = "llm_ranking_laeufe.jsonl"

# Hoechstdauern je Rhythmus — Vorschlag aus dem Plan #4650 (Offen: Entscheidung beim Owner).
# JE_REFRESH: zwei Refresh-Intervalle, ein einzelner Fehlschlag laesst die Liste also stehen.
HOECHSTDAUER_TAEGLICH = timedelta(hours=26)
HOECHSTDAUER_WOECHENTLICH = timedelta(days=8)
# #4960: eigener Takt der neuen Rhythmen in Minuten (Owner-Entscheidung in #4939, 29.10.2026);
# Hoechstdauer = 2x Takt, wie JE_REFRESH.
TAKT_STUENDLICH_MIN = 60
TAKT_ALLE_4_STUNDEN_MIN = 240

FRISCH = "frisch"
VERALTET_ABER_GUELTIG = "veraltet_aber_gueltig"
ABGELAUFEN = "abgelaufen"
KEINER = "keiner"
# #4861: die eigene Liste (Quelle ``liste``) gilt bis zur naechsten Aenderung — kein Ablauf.
DAUERHAFT = "dauerhaft"


class Rhythmus(str, Enum):
    JE_REFRESH = "je_refresh"
    TAEGLICH_VOR_OEFFNUNG = "taeglich_vor_oeffnung"
    WOECHENTLICH = "woechentlich"
    STUENDLICH = "stuendlich"
    ALLE_4_STUNDEN = "alle_4_stunden"

    @classmethod
    def default(cls) -> "Rhythmus":
        return cls.JE_REFRESH


_EIGENER_TAKT = {
    Rhythmus.STUENDLICH: TAKT_STUENDLICH_MIN,
    Rhythmus.ALLE_4_STUNDEN: TAKT_ALLE_4_STUNDEN_MIN,
}


def takt_minuten(rhythmus: Rhythmus, refresh_minuten: float) -> float:
    """#4960: das Intervall fuer `should_refresh_rank` — 60 bzw. 240 Minuten fuer die neuen
    Rhythmen, sonst unveraendert der Ranking-Refresh (`refresh_minuten`)."""
    return _EIGENER_TAKT.get(rhythmus, refresh_minuten)


def _utc(t: datetime) -> datetime:
    """Naive Zeitpunkte (Sim-Uhr) gelten als UTC."""
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def _ny_datum(t: datetime) -> date:
    return _utc(t).astimezone(_MARKT_TZ).date()


def ist_lauf_faellig(
    rhythmus: Rhythmus,
    letzter_erfolg_utc: Optional[datetime],
    jetzt_utc: datetime,
    refresh_faellig: bool,
) -> bool:
    """Ob der gespeicherte Prompt in diesem Zyklus laufen soll.

    `refresh_faellig` ist das Ergebnis von `should_refresh_rank` mit `takt_minuten` und
    deckelt jeden Rhythmus. `JE_REFRESH` folgt ihm unmittelbar (Takt = Ranking-Refresh).
    #4960 `STUENDLICH` und `ALLE_4_STUNDEN` folgen ihrem eigenen Takt (60 bzw. 240 min), nur
    Mo–Fr. Taeglich und woechentlich laufen am ersten Refresh eines neuen Handelstags bzw.
    einer neuen Handelswoche (Kalender in America/New_York, Mo–Fr). Ein spaeter Engine-Start
    holt den Lauf nach, statt den Tag auszulassen. Boersenfeiertage kennt die Pruefung nicht.
    """
    if not refresh_faellig:
        return False
    if rhythmus is Rhythmus.JE_REFRESH:
        return True
    heute = _ny_datum(jetzt_utc)
    if heute.weekday() >= 5:
        return False
    if rhythmus in _EIGENER_TAKT or letzter_erfolg_utc is None:
        return True
    letzter = _ny_datum(letzter_erfolg_utc)
    if rhythmus is Rhythmus.TAEGLICH_VOR_OEFFNUNG:
        return letzter < heute
    return letzter.isocalendar()[:2] < heute.isocalendar()[:2]


def hoechstdauer(rhythmus: Rhythmus, refresh_minuten: float) -> timedelta:
    """Wie lange die Liste des letzten erfolgreichen Laufs gilt."""
    if rhythmus is Rhythmus.TAEGLICH_VOR_OEFFNUNG:
        return HOECHSTDAUER_TAEGLICH
    if rhythmus is Rhythmus.WOECHENTLICH:
        return HOECHSTDAUER_WOECHENTLICH
    return timedelta(minutes=2 * takt_minuten(rhythmus, refresh_minuten))


@dataclass
class LlmRankingLauf:
    zeitpunkt_utc: datetime
    prompt_hash: str
    modell: Optional[str]
    anbieter: Optional[str]
    ok: bool
    geliefert: List[str] = field(default_factory=list)
    verworfen: Dict[str, str] = field(default_factory=dict)
    fehler: Optional[str] = None
    lauf_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # #4662: Lauf unter SIM_MODE — das LLM kennt die Zukunft des simulierten Zeitraums
    # (Look-ahead). Einziger Ursprung der Kennzeichnung "nicht belastbar" in Trichter und
    # Sim-Vergleich. Alte Protokollzeilen ohne das Feld gelten als Live-Lauf.
    sim: bool = False
    # #4960: effektiver Anbieter, Websuche und zitierte Quellen. Alte Zeilen ohne die Felder
    # lesen mit diesen Defaults (keine Suche, keine Quellen).
    kanonischer_anbieter: Optional[str] = None
    websuche: bool = False
    quellen: List[str] = field(default_factory=list)

    @classmethod
    def aus_ergebnis(
        cls, ergebnis: LlmRankingErgebnis, zeitpunkt_utc: Optional[datetime] = None
    ) -> "LlmRankingLauf":
        """Protokolleintrag zu einem Ergebnis; Zeitpunkt ohne Angabe von der Engine-Uhr,
        ``sim`` aus ``is_sim_mode()`` beim Protokollieren."""
        if zeitpunkt_utc is None:
            zeitpunkt_utc = engine_now(timezone.utc)
        return cls(
            zeitpunkt_utc=_utc(zeitpunkt_utc),
            prompt_hash=ergebnis.prompt_hash,
            modell=ergebnis.modell,
            anbieter=ergebnis.anbieter,
            ok=ergebnis.ok,
            geliefert=list(ergebnis.rangliste),
            verworfen=dict(ergebnis.verworfen),
            fehler=ergebnis.fehler,
            sim=is_sim_mode(),
            kanonischer_anbieter=ergebnis.kanonischer_anbieter,
            websuche=bool(ergebnis.websuche),
            quellen=list(ergebnis.quellen),
        )

    def als_json(self) -> str:
        daten = asdict(self)
        daten["zeitpunkt_utc"] = self.zeitpunkt_utc.isoformat()
        return json.dumps(daten, ensure_ascii=False)

    @classmethod
    def aus_json(cls, zeile: str) -> "LlmRankingLauf":
        daten = json.loads(zeile)
        daten["zeitpunkt_utc"] = _utc(datetime.fromisoformat(daten["zeitpunkt_utc"]))
        return cls(**daten)


class LaufSpeicher:
    """Anhaengeprotokoll der Laeufe (JSON Lines) im Datenverzeichnis.

    Pfad ohne Angabe: `<AAA_USER_DATA_DIR>/llm_ranking_laeufe.jsonl` (Desktop), sonst relativ
    zum Arbeitsverzeichnis — wie die Audit-Ablagen (`core/audit_paths.py`). Die Laeufe werden
    beim Anlegen nachgelesen und im Prozess gehalten; die Datei ist best effort.
    """

    def __init__(self, pfad: Union[str, Path, None] = None):
        self._pfad = (
            Path(pfad) if pfad is not None else Path(resolve_audit_log_path(DATEINAME))
        )
        self._laeufe: List[LlmRankingLauf] = self._nachlesen()

    def _nachlesen(self) -> List[LlmRankingLauf]:
        if not self._pfad.is_file():
            return []
        laeufe: List[LlmRankingLauf] = []
        try:
            zeilen = self._pfad.read_text(encoding="utf-8").splitlines()
        except (
            Exception
        ) as exc:  # noqa: BLE001 — das Protokoll darf den Zyklus nie kosten
            logger.warning(
                "LlmRankingLauf: Protokoll %s nicht lesbar (%s) → leerer Speicher",
                self._pfad,
                exc,
            )
            return []
        for nr, zeile in enumerate(zeilen, start=1):
            if not zeile.strip():
                continue
            try:
                laeufe.append(LlmRankingLauf.aus_json(zeile))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "LlmRankingLauf: kaputte Zeile %d in %s uebersprungen (%s)",
                    nr,
                    self._pfad,
                    exc,
                )
        return laeufe

    def protokolliere(self, lauf: LlmRankingLauf) -> None:
        """Haengt den Lauf an. Wirft nie; ein Schreibfehler ist eine WARNING."""
        self._laeufe.append(lauf)
        try:
            self._pfad.parent.mkdir(parents=True, exist_ok=True)
            with self._pfad.open("a", encoding="utf-8") as datei:
                datei.write(lauf.als_json() + "\n")
        except (
            Exception
        ) as exc:  # noqa: BLE001 — das Protokoll darf den Zyklus nie kosten
            logger.warning(
                "LlmRankingLauf: Lauf %s nicht in %s geschrieben (%s) → nur im Prozess gehalten",
                lauf.lauf_id,
                self._pfad,
                exc,
            )

    def letzter_erfolg(self) -> Optional[LlmRankingLauf]:
        for lauf in reversed(self._laeufe):
            if lauf.ok:
                return lauf
        return None

    def gueltige_liste(
        self, jetzt: datetime, hoechstdauer: timedelta
    ) -> Tuple[Optional[List[str]], str, Optional[timedelta]]:
        """(Rangliste | None, Status, Alter) des letzten erfolgreichen Laufs.

        Status: `frisch` (der juengste Lauf war erfolgreich), `veraltet_aber_gueltig` (danach
        schlug ein Lauf fehl, die Hoechstdauer ist nicht erreicht), `abgelaufen` (Alter >=
        Hoechstdauer, Liste wird nicht geliefert) oder `keiner` (nie ein Erfolg).
        """
        erfolg = self.letzter_erfolg()
        if erfolg is None:
            return None, KEINER, None
        alter = _utc(jetzt) - erfolg.zeitpunkt_utc
        if alter >= hoechstdauer:
            logger.warning(
                "LlmRankingLauf: letzte Liste (%s) ist %s alt, Hoechstdauer %s → abgelaufen "
                "(Rueckfall beim Aufrufer)",
                erfolg.lauf_id,
                alter,
                hoechstdauer,
            )
            return None, ABGELAUFEN, alter
        status = FRISCH if self._laeufe[-1] is erfolg else VERALTET_ABER_GUELTIG
        return list(erfolg.geliefert), status, alter
