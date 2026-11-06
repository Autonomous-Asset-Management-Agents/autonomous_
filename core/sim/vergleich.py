"""Drei Strategien per Simulation vergleichen (#4635, STW-9; ADR-027 Regeln 2-5, Punkte 8 und 12).

Reine Logik ohne FastAPI und bis #4917 (STW-9b) ohne Aufrufer; die Routen baut #4917.

- **Eingabe:** Zeitraum innerhalb der letzten drei Jahre, 1-3 bekannte Strategien,
  Startkapital. Die Editions-Grenze (``Entitlement.backtest_months``) **weist ab**, statt den
  Beginn still vorzuziehen wie ``simulation_runner._clamp_backtest_start``: Sonst gaelte das
  Ergebnis nicht fuer genau den angefragten Zeitraum.
- **Inhaltsadressiert:** ``lauf_id = sha256(kanonisch)[:16]``. Gleiche Eingabe und gleiche
  Strategie-Werte ergeben denselben Ablageordner ``<AAA_USER_DATA_DIR>/strategie_sim/<lauf_id>/``.
- **Isolation (Epic-NFR, ADR-027 Punkt 8):** gerechnet wird nie im Engine-Prozess. ``starte``
  ruft ``python -m core.sim.stimmen_sim`` (#3712) als Subprozess; dieses Modul importiert
  ``stimmen_sim`` nicht (``test_3712_stimmen_sim_isolation.py``).
- **Kennzahlen:** je Strategie nur TWR und maximaler Drawdown, dazu SPY im selben Zeitraum
  (Regel 2). Ohne Ein- und Auszahlungen ist die TWR die Gesamtrendite.
- Kein Zeitstempel, keine PID und kein Pfad im Ergebnis — zwei Laeufe bleiben bitgleich.

Plan: ``docs/4635-stw-9-feat-console-drei-strategien-per-simulatio/implementation_plan.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from core import trading_settings as ts
from core.performance_metrics import compute_performance_metrics

logger = logging.getLogger(__name__)

#: Ordner der Laeufe im Datenverzeichnis.
ABLAGE = "strategie_sim"
#: Korpus aus #4634 im Datenverzeichnis (``python -m core.sim.vorbereitung --korpus <hier>``).
KORPUS = "strategie_korpus"
#: Einstieg der Stimmen-Simulation (#3712).
_SIM_MODUL = "core.sim.stimmen_sim"
#: Vergleichszeitraum hoechstens drei Jahre zurueck (ADR-027 Punkt 12, Korpus #4634).
JAHRE = 3
MAX_STRATEGIEN = 3
#: Reservierter Bezeichner des Lieferzustands (#5126, ADR-027 Regel 1, UX-Konzept T20, E2):
#: Auslieferungswerte statt einer Strategie, zaehlt nicht gegen ``MAX_STRATEGIEN``. Er steht
#: nur auf der Leitung und in der Lauf-Ablage, angezeigt wird T20.
#: ``strategie_profile.pruefe_name`` vergibt ihn nie.
LIEFERZUSTAND = "@delivery-state"
#: Dateien eines Laufs: die kanonische Eingabe und die Ausgabe der Simulation.
EINGABE, ERGEBNIS = "eingabe.json", "ergebnis.json"
_LAUF_ID = re.compile(r"[0-9a-f]{16}")
#: Benchmark im selben Zeitraum; #4634 laedt ihn als Kalender in den Korpus.
BENCHMARK = "SPY"
#: Stand der Rechnung, Teil von ``kanonisch``: Aendert sich, was die Simulation aus derselben
#: Eingabe rechnet, darf ein alter Lauf nicht weiter als fertig gelten (#5044: 2 = Korpus ohne
#: LSTM-Stimme handelt; #5071: 3 = Korpus mit Waechter-Stimme; #5076: 4 = mit Fundamentals und
#: Valuation; #5083: 5 = mit Quality).
RECHENSTAND = 5

# Registry-Key → Feld von ``stimmen_sim.StimmenSimStrategie``. Was hier nicht steht, rechnet
# die Stimmen-Simulation nicht nach; es erscheint unter „nicht abgebildet" (Regel 4).
_FELDER = {
    "SIGNAL_BUY_THRESHOLD": ("buy_threshold", float),
    "SIGNAL_SELL_THRESHOLD": ("sell_threshold", float),
    "GLOBAL_BUY_COOLDOWN_MINUTES": ("buy_cooldown_minuten", float),
    "MAX_BUYS_PER_HOUR": ("max_buys_pro_stunde", int),
}
# Stimmen des Offline-Korpus (``stimmen_rechner.GEWICHTE``): Agent → (Gewicht, Schalter).
# Der DrawdownGuard hat keinen Gewichts-Key: Er wirkt nur ueber das Veto (#3084).
_STIMMEN: Dict[str, Tuple[Optional[str], str]] = {
    "MomentumAgent": ("MOMENTUM_AGENT_WEIGHT", "MOMENTUM_AGENT_ENABLED"),
    "TrendAgent": ("TREND_AGENT_WEIGHT", "TREND_AGENT_ENABLED"),
    "FundamentalsAgent": ("FUNDAMENTALS_AGENT_WEIGHT", "FUNDAMENTALS_AGENT_ENABLED"),
    "ValuationAgent": ("VALUATION_AGENT_WEIGHT", "VALUATION_AGENT_ENABLED"),
    "QualityAgent": ("QUALITY_AGENT_WEIGHT", "QUALITY_AGENT_ENABLED"),
    "DrawdownGuardAgent": (None, "DRAWDOWN_GUARD_AGENT_ENABLED"),
}
_ABGEBILDET = frozenset(_FELDER) | {k for paar in _STIMMEN.values() for k in paar if k}
# Nicht gerechnet (Plan #5071 §1): Agent → (Gewicht, Schalter). Keine Stimme im Korpus; eine
# Strategie, die sie nutzt, sagt es im Ergebnis (Regel 4). Das LSTM fehlt bewusst, es steht als
# ``ohne_lstm`` im Ergebnis (Owner-Festlegung im Epic #4625).
_NICHT_RECHENBAR: Dict[str, Tuple[Optional[str], str]] = {
    "NewsSentimentAgent": ("NEWS_SENTIMENT_WEIGHT", "NEWS_SENTIMENT_AGENT_ENABLED"),
    "SpecialistAlphaAgent": (
        "SPECIALIST_ALPHA_WEIGHT",
        "SPECIALIST_ALPHA_AGENT_ENABLED",
    ),
    "RegimeDetectionAgent": (None, "REGIME_DETECTION_AGENT_ENABLED"),
    "VIXAwareRiskAgent": ("VIX_RISK_WEIGHT", "VIX_RISK_AGENT_ENABLED"),
    "RLConfidenceAgent": ("RL_CONFIDENCE_WEIGHT", "RL_CONFIDENCE_AGENT_ENABLED"),
    "UpsideSkewAgent": ("UPSIDE_SKEW_WEIGHT", "UPSIDE_SKEW_AGENT_ENABLED"),
    "VolumeConfirmationAgent": (
        "VOLUME_CONFIRM_AGENT_WEIGHT",
        "VOLUME_CONFIRM_AGENT_ENABLED",
    ),
}
# Fehlt eine dieser Stimmen, fehlen dem Titel Fundamentaldaten (#5083). Valuation zaehlt nicht:
# Sie enthaelt sich auch mit Daten im PEG-Band (``fundament_stimmen.valuation_score``).
_FUNDAMENTAL_STIMMEN = ("FundamentalsAgent", "QualityAgent")


class EingabeUngueltig(ValueError):
    """Zeitraum oder Strategie-Auswahl ungueltig (422)."""


class ZeitraumUeberEditionsGrenze(ValueError):
    """Der Zeitraum reicht ueber ``backtest_months`` der Edition hinaus (403)."""


class VergleichsEingabe(BaseModel):
    """Eingabe eines Vergleichs (kuenftig Body der Route aus #4917) — lokal wie in #4629."""

    beginn: date
    ende: date
    # +2: vier eigene plus Lieferzustand bleiben ein 422 aus ``pruefe_eingabe`` (#5126).
    strategien: List[str] = Field(max_length=MAX_STRATEGIEN + 2)
    startkapital: float = Field(gt=0, le=1e12)


def _minus_monate(tag: date, monate: int) -> date:
    jahr, monat = divmod(tag.year * 12 + tag.month - 1 - monate, 12)
    for t in (tag.day, 30, 29, 28):
        try:
            return date(jahr, monat + 1, t)
        except ValueError:
            continue
    raise AssertionError("unerreichbar")


def pruefe_eingabe(
    eingabe: VergleichsEingabe,
    heute: date,
    backtest_months: Optional[int],
    bekannte: Sequence[str],
) -> None:
    """Prueft, ohne zu klemmen. ``EingabeUngueltig`` bzw. ``ZeitraumUeberEditionsGrenze``."""
    if eingabe.ende > heute:
        raise EingabeUngueltig("ende liegt in der Zukunft")
    if eingabe.beginn >= eingabe.ende:
        raise EingabeUngueltig("beginn muss vor ende liegen")
    if eingabe.beginn < _minus_monate(heute, 12 * JAHRE):
        raise EingabeUngueltig(f"beginn liegt mehr als {JAHRE} Jahre zurueck")
    namen = eingabe.strategien
    # Der Lieferzustand zaehlt nicht gegen die drei (#5126); allein reicht er nicht.
    eigene = [n for n in namen if n != LIEFERZUSTAND]
    if not 1 <= len(eigene) <= MAX_STRATEGIEN:
        raise EingabeUngueltig(f"1 bis {MAX_STRATEGIEN} Strategien")
    if len(set(namen)) != len(namen):
        raise EingabeUngueltig("jede Strategie hoechstens einmal")
    if LIEFERZUSTAND in namen and LIEFERZUSTAND in bekannte:
        logger.warning(
            "strategie_sim: Strategie %r im Bestand → gilt als Lieferzustand (#5126)",
            LIEFERZUSTAND,
        )
    unbekannt = [n for n in eigene if n not in bekannte]
    if unbekannt:
        raise EingabeUngueltig("unbekannte Strategie: " + ", ".join(unbekannt))
    if backtest_months is not None and eingabe.beginn < _minus_monate(
        heute, backtest_months
    ):
        raise ZeitraumUeberEditionsGrenze(
            f"die Edition erlaubt hoechstens {backtest_months} Monate"
        )


def kanonisch(
    eingabe: VergleichsEingabe, werte_je_strategie: Mapping[str, Mapping[str, str]]
) -> bytes:
    """Die Eingabe samt Strategie-Werten als feste Bytefolge; Strategien in Eingabe-Reihenfolge."""
    inhalt = {
        "beginn": eingabe.beginn.isoformat(),
        "ende": eingabe.ende.isoformat(),
        "rechenstand": RECHENSTAND,
        "startkapital": float(eingabe.startkapital),
        "strategien": [
            {"name": n, "werte": dict(werte_je_strategie[n])}
            for n in eingabe.strategien
        ],
    }
    return json.dumps(
        inhalt, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def werte_lieferzustand() -> Dict[str, str]:
    """Auslieferungswerte der Keys, die die Stimmen-Simulation nachrechnet (#5126).

    Nur ``REGISTRY[...].default`` — weder ``ts.current_settings()`` noch die Strategie-Ablage.
    Nicht alle Registry-Keys: Sonst stuenden unter „nicht abgebildet" Auslieferungswerte, die
    der Nutzer nie gesetzt hat. Aendert ein Release einen dieser Werte, aendert sich die
    ``lauf_id``.
    """
    return {
        k: ts.canonical_text(k, ts.REGISTRY[k].default) for k in sorted(_ABGEBILDET)
    }


def werte_fuer(
    namen: Sequence[str], bestand: Mapping[str, Mapping[str, str]]
) -> Dict[str, Mapping[str, str]]:
    """Werte je Name fuer ``kanonisch``: der Lieferzustand aus der Registry, sonst ``bestand``."""
    return {
        n: werte_lieferzustand() if n == LIEFERZUSTAND else bestand[n] for n in namen
    }


def lauf_id(kanonische_eingabe: bytes) -> str:
    return hashlib.sha256(kanonische_eingabe).hexdigest()[:16]


def _wert(werte: Mapping[str, str], key: str) -> str:
    if key in werte:
        return werte[key]
    return ts.canonical_text(key, ts.REGISTRY[key].default)


def sim_strategie(
    name: str, werte: Mapping[str, str], startkapital: float
) -> Tuple[Dict[str, Any], List[str]]:
    """Felder fuer ``StimmenSimStrategie`` und die Keys, die die Simulation nicht nachrechnet.

    Basis sind die Registry-Defaults, wie bei ``strategie_profile.pruefe_strategie``. Ein
    abgeschalteter Agent stimmt mit Gewicht 0.0 — eine Enthaltung, die auch nicht vetoet. Ein
    eingeschalteter Agent ohne Gewichts-Key behaelt das Gewicht aus dem Korpus.
    """
    felder: Dict[str, Any] = {"name": name, "startkapital": float(startkapital)}
    for key, (feld, art) in _FELDER.items():
        felder[feld] = art(float(_wert(werte, key)))
    gewichte: Dict[str, float] = {}
    for agent, (gew, an) in _STIMMEN.items():
        if _wert(werte, an) != "true":
            gewichte[agent] = 0.0
        elif gew is not None:
            gewichte[agent] = float(_wert(werte, gew))
    felder["gewichte"] = gewichte
    return felder, sorted(k for k in werte if k not in _ABGEBILDET)


def nicht_abgebildete_agenten(werte: Mapping[str, str]) -> List[str]:
    """Agenten, die in dieser Strategie live mitwirken, deren Stimme die Simulation nicht rechnet.

    Es zaehlen die wirksamen Werte einschliesslich Registry-Vorgaben, nicht nur die gesetzten
    Keys: Wer nichts einstellt, hat trotzdem News und Specialist im Gremium.
    """
    return sorted(
        agent
        for agent, (gew, an) in _NICHT_RECHENBAR.items()
        if _wert(werte, an) == "true" and (gew is None or float(_wert(werte, gew)) > 0)
    )


def _kurve(reihe: Sequence[Mapping[str, Any]], kapital: float) -> Dict[str, float]:
    m = compute_performance_metrics(list(reihe), [], kapital)
    return {"twr": m["total_return_pct"], "dd": m["max_drawdown_pct"]}


def kennzahlen(
    tagesreihe: Sequence[Mapping[str, Any]],
    spy_reihe: Mapping[str, float],
    startkapital: float,
) -> Dict[str, Optional[float]]:
    """TWR und maximaler Drawdown der Strategie, dieselben zwei Werte fuer SPY im Zeitraum.

    Kein Vergleichswert zwischen Strategien (Regel 2). SPY ohne zwei Kurse im Zeitraum: ``None``.
    """
    s = _kurve(tagesreihe, startkapital)
    out: Dict[str, Optional[float]] = {
        "twr_pct": s["twr"],
        "max_drawdown_pct": s["dd"],
        "spy_twr_pct": None,
        "spy_max_drawdown_pct": None,
    }
    if tagesreihe:
        von, bis = str(tagesreihe[0]["date"]), str(tagesreihe[-1]["date"])
        spy = [
            {"date": t, "equity": float(spy_reihe[t])}
            for t in sorted(spy_reihe)
            if von <= t <= bis
        ]
        if len(spy) >= 2:
            b = _kurve(spy, spy[0]["equity"])
            out["spy_twr_pct"], out["spy_max_drawdown_pct"] = b["twr"], b["dd"]
    return out


def _datenverzeichnis() -> Path:
    verzeichnis = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    if not verzeichnis:
        logger.warning(
            "strategie_sim: AAA_USER_DATA_DIR fehlt → Ablage im Arbeitsverzeichnis %s",
            os.getcwd(),
        )
        return Path.cwd()
    return Path(verzeichnis)


def ablage_verzeichnis() -> Path:
    return _datenverzeichnis() / ABLAGE


def korpus_verzeichnis() -> Path:
    return _datenverzeichnis() / KORPUS


# ── Subprozess und Ergebnis ──────────────────────────────────────────────────

#: Gestartete Prozesse dieser Engine je ``lauf_id``. Nach einem Neustart der Engine leer; ein
#: Ordner ohne Ergebnis gilt dann als ``fehler`` und darf neu gestartet werden.
#: ``subprocess.Popen`` statt ``asyncio.create_subprocess_exec``: ``poll()`` liest den Status
#: unabhaengig von der Event-Loop, die den Prozess gestartet hat (eine asyncio-Prozess-Handle
#: erfaehrt das Ende nur, solange genau diese Loop laeuft).
_LAEUFE: Dict[str, subprocess.Popen] = {}
_PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/


def laeuft(lid: str) -> bool:
    proc = _LAEUFE.get(lid)
    return proc is not None and proc.poll() is None


def ist_fertig(lid: str) -> bool:
    return (lies_ergebnis(lid) or {}).get("status") == "fertig"


async def starte(lid: str, eingabe_bytes: bytes) -> subprocess.Popen:
    """Schreibt Eingabe und Strategien in den Lauf-Ordner und startet ``_SIM_MODUL``."""
    inhalt = json.loads(eingabe_bytes)
    ordner = ablage_verzeichnis() / lid
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / EINGABE).write_bytes(eingabe_bytes)
    (ordner / ERGEBNIS).unlink(missing_ok=True)
    dateien = []
    for i, s in enumerate(inhalt["strategien"]):
        felder, _ = sim_strategie(s["name"], s["werte"], inhalt["startkapital"])
        datei = ordner / f"strategie_{i}.json"
        datei.write_text(json.dumps(felder, sort_keys=True), encoding="utf-8")
        dateien.append(str(datei))

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(_PAKET), env.get("PYTHONPATH", "")) if p
    )
    extra: Dict[str, Any] = {}
    if os.name == "nt":
        extra["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with open(ordner / "protokoll.log", "wb") as protokoll:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                _SIM_MODUL,
                "--store",
                str(korpus_verzeichnis() / "stimmen"),
                "--von",
                inhalt["beginn"],
                "--bis",
                inhalt["ende"],
                "--out",
                str(ordner / ERGEBNIS),
                "--strategien",
                *dateien,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=protokoll,
            cwd=str(_PAKET),
            env=env,
            **extra,
        )
    _LAEUFE[lid] = proc
    return proc


def _spy_reihe() -> Dict[str, float]:
    """Tages-Schlusskurse von SPY aus dem Korpus; fehlt die Datei, leer (WARNING)."""
    pfad = korpus_verzeichnis() / f"{BENCHMARK}.parquet"
    try:
        import pandas as pd

        df = pd.read_parquet(pfad)
    except Exception as exc:  # noqa: BLE001 — ohne SPY bleibt die Spalte leer
        logger.warning(
            "strategie_sim: %s nicht lesbar (%s) → SPY ohne Wert", pfad.name, exc
        )
        return {}
    spalte = "close" if "close" in df.columns else "Close"
    tage = pd.DatetimeIndex(df.index).strftime("%Y-%m-%d")
    return {t: float(c) for t, c in zip(tage, df[spalte])}


def _korpus_grenzen() -> List[Dict[str, str]]:
    from core.sim import fetch

    grenzen = fetch.lies_manifest(korpus_verzeichnis()).get("grenzen", [])
    return [{"art": str(g["art"]), "text": str(g["text"])} for g in grenzen]


def titel_ohne_fundamentaldaten(beginn: str, ende: str) -> List[str]:
    """Titel, denen im Zeitraum an mindestens einem Tag eine Fundamental-Stimme fehlt (#5083).

    Owner-Bestaetigung an #5083: T29 nennt Fundamentaldaten nicht mehr, also nennt das Ergebnis
    diese Titel. Der Benchmark hat keine Fundamentaldaten und zaehlt nicht.
    """
    stimmen = korpus_verzeichnis() / "stimmen"
    try:
        import pandas as pd

        dec = pd.read_parquet(stimmen / "decisions.parquet")
        vot = pd.read_parquet(
            stimmen / "votes.parquet",
            columns=["did", "agent"],
            filters=[("agent", "in", list(_FUNDAMENTAL_STIMMEN))],
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 — wie _spy_reihe: das Ergebnis bleibt lesbar
        logger.warning(
            "strategie_sim: Stimmen nicht lesbar (%s) → Titel ohne Fundamentaldaten unbekannt",
            exc,
        )
        return []
    tage = dec["ts"].str[:10]
    zeitraum = dec[(tage >= beginn) & (tage <= ende) & (dec["symbol"] != BENCHMARK)]
    anzahl = zeitraum["did"].map(vot.groupby("did").size()).fillna(0)
    fehlt = zeitraum.loc[anzahl < len(_FUNDAMENTAL_STIMMEN), "symbol"]
    return sorted(fehlt.unique().tolist())


def lies_ergebnis(lid: str) -> Optional[Dict[str, Any]]:
    """Status ``laeuft`` / ``fertig`` / ``fehler`` eines Laufs; ``None`` bei unbekannter ID."""
    if not _LAUF_ID.fullmatch(lid or ""):
        return None
    ordner = ablage_verzeichnis() / lid
    if not ordner.is_dir():
        return None
    if laeuft(lid):
        return {"lauf_id": lid, "status": "laeuft"}
    fehler = {"lauf_id": lid, "status": "fehler"}
    proc = _LAEUFE.get(lid)
    if proc is not None and proc.poll() != 0:
        logger.warning("strategie_sim: Lauf %s endete mit Code %s", lid, proc.poll())
        return fehler
    try:
        eingabe = json.loads((ordner / EINGABE).read_text(encoding="utf-8"))
        sim = json.loads((ordner / ERGEBNIS).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("strategie_sim: Lauf %s ohne lesbares Ergebnis (%s)", lid, exc)
        return fehler

    kurven = {s["strategie"]: s["ergebnis"]["daily_equity"] for s in sim["strategien"]}
    spy = _spy_reihe()
    zeilen, nicht = [], _korpus_grenzen()
    for s in eingabe["strategien"]:
        zahlen = kennzahlen(kurven[s["name"]], spy, eingabe["startkapital"])
        zeilen.append({"name": s["name"], **zahlen})
        _, keys = sim_strategie(s["name"], s["werte"], eingabe["startkapital"])
        if keys:
            nicht.append({"art": "einstellungen", "strategie": s["name"], "keys": keys})
        agenten = nicht_abgebildete_agenten(s["werte"])
        if agenten:
            nicht.append({"art": "agenten", "strategie": s["name"], "agenten": agenten})
    # Der Store gilt fuer alle Strategien eines Laufs, der Hinweis steht also einmal (#5044).
    if any(s.get("ohne_lstm") for s in sim["strategien"]):
        nicht.append({"art": "ohne_lstm"})
    titel = titel_ohne_fundamentaldaten(eingabe["beginn"], eingabe["ende"])
    if titel:
        nicht.append({"art": "ohne_fundamentaldaten", "titel": titel})
    erste = kurven[eingabe["strategien"][0]["name"]]
    return {
        "lauf_id": lid,
        "status": "fertig",
        "ergebnis": {
            "zeitraum": {"beginn": eingabe["beginn"], "ende": eingabe["ende"]},
            "strategien": zeilen,
            # Ein historischer Pfad, keine Spanne (UX-Konzept T28; E6 offen).
            "unsicherheit": {"pfade": 1, "handelstage": len(erste)},
            "nicht_abgebildet": nicht,
        },
    }


# ── Korpus (#4634) ───────────────────────────────────────────────────────────


class KeinKorpus(RuntimeError):
    """Kein Korpus deckt den Zeitraum ab (409). ``grund``: ``keine_keys`` | ``kein_korpus``."""

    def __init__(self, grund: str):
        super().__init__(grund)
        self.grund = grund


def _hat_keys() -> bool:
    """Wie ``vorbereitung.lade_keys``, ohne den Schluesselbund erneut zu lesen: Die Engine hat
    ihn beim Start in die Umgebung geladen."""
    k = os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
    s = os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")
    return bool(k and s)


def _stand_passt(stimmen_dir: Path) -> bool:
    """Ist der Korpus mit den Stimmen dieser Installation gerechnet (#5071)?"""
    from core.sim import stimmen_rechner

    try:
        aktuell = stimmen_rechner.stand()
    except OSError as exc:
        logger.warning("strategie_sim: Fundamental-Abbild nicht lesbar (%s)", exc)
        return False
    vorhanden = stimmen_rechner.lies_stand(stimmen_dir)
    if vorhanden != aktuell:
        logger.warning(
            "strategie_sim: Korpus vom Stand %s, gerechnet wird %s → gilt nicht",
            vorhanden,
            aktuell,
        )
        return False
    return True


def pruefe_korpus(beginn: date, ende: date) -> None:
    """Deckt der Korpus ``[beginn, ende]`` ab, samt Stimmen vom aktuellen Stand?

    Sonst ``KeinKorpus``: Ein alter Korpus zeigt „Daten fehlen" und liefert kein Rumpf-Ergebnis.
    """
    from core.sim import fetch

    korpus = korpus_verzeichnis()
    try:
        zeitraum = fetch.lies_manifest(korpus).get("zeitraum") or {}
    except (OSError, ValueError) as exc:
        logger.warning("strategie_sim: korpus.json nicht lesbar (%s)", exc)
        zeitraum = {}
    stimmen = all(
        (korpus / "stimmen" / f"{n}.parquet").is_file()
        for n in ("decisions", "votes", "kurse")
    )
    deckt = (
        stimmen
        and str(zeitraum.get("von", "9999")) <= beginn.isoformat()
        and ende.isoformat() <= str(zeitraum.get("bis", ""))
        and _stand_passt(korpus / "stimmen")
    )
    if not deckt:
        raise KeinKorpus("kein_korpus" if _hat_keys() else "keine_keys")
