"""Implizite Volatilität je Titel besorgen und aufbewahren (#3038, TRD-10).

`VIXAwareRiskAgent` stimmt — bei eingeschaltetem Flag — über die erwartete
Schwankung **des einzelnen Titels** ab, ausgedrückt als Rang gegen die Verteilung
des Vortages. Der Agent holt sich dafür selbst nichts: Er liest ausschließlich
drei Kanäle aus dem ``SymbolEvalState``. Dieses Modul füllt sie.

Die Trennung ist Absicht. Ein Agent, der im Abstimmungspfad Netzverkehr macht,
ist im Nachhinein nicht auditierbar (welcher Wert lag zum Zeitpunkt der
Entscheidung vor?) und hält den Zyklus an, wenn die Gegenstelle hängt.

DER ABRUF IST GEMESSEN, NICHT GESCHÄTZT
---------------------------------------
``docs/3038-vixaware-implied-vol/results/RESULTS_ABRUFKOSTEN.md``, 25.08.2026,
30 Titel bei offenem Markt:

    volle Kette, ungefiltert   Median 1,08 s  ->  32,4 s je Zyklus  =  3,6 %
    serverseitig gefiltert     Median 0,15 s  ->   4,5 s je Zyklus  =  0,5 %

Die Filterung ist der ganze Unterschied: sie reduziert die Antwort von 1.028 bis
6.663 Kontrakten auf 20 bis 82. Deshalb gehen Verfall- UND Strike-Grenzen mit an
den Server; ohne sie kostet derselbe Wert das Siebenfache.

Alpaca liefert ``implied_volatility`` samt Greeks fertig in der Kette mit. Die
Black-Scholes-Inversion aus ``research/build_iv_history.py`` ist hier NICHT
nötig — sie bleibt nur für die Historie relevant, wo lediglich Kontrakt-Bars
vorliegen. Damit entfallen auch deren Annahmen (Zinssatz, Call-Seite,
Mindestvolumen) aus dem Handelspfad.

WARUM EIN TAGES-CACHE, OBWOHL DIE ZEIT REICHT
----------------------------------------------
Nach der Messung ist er für die Zykluszeit keine Notwendigkeit. Zwei andere
Gründe tragen ihn trotzdem:

1. **Rate-Limits.** 30 Kandidaten × 26 Zyklen = 780 Abrufe je Handelstag gegen
   30 mit Cache.
2. **Fachlich richtig.** Die Referenz stammt aus dem Vortag. Ein Wert, der sich
   über den Tag bewegt, gegen eine feste Referenz gemessen, ließe den Rang eines
   Titels schwanken, ohne dass sich die Vergleichsbasis bewegt — der Agent
   stimmte um 10:00 anders ab als um 15:00, ohne dass sich etwas Vergleichbares
   geändert hätte.

Der Cache liegt auf Platte, nicht im Speicher: Die Engine startet täglich neu.
Ein Speicher-Cache wäre nach jedem Neustart leer, und die Vortagsverteilung —
die einzige zulässige Referenz (AC-7) — gäbe es dann nie.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import statistics
import threading
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

# Fenster der ATM-30-Tage-IV. Identisch zu research/build_iv_history.py, damit
# der Live-Wert und die Korpus-Historie dieselbe Größe meinen — sonst würde der
# Sim etwas anderes messen als der Betrieb später rechnet.
REST_MIN_TAGE = 20
REST_MAX_TAGE = 45
ATM_BAND = 0.05

# Plausibilitätsband. Werte darunter/darüber stammen erfahrungsgemäß aus
# Fehlquoten oder toten Strikes, nicht aus dem Markt.
IV_MIN, IV_MAX = 0.01, 5.0

# Wie viele Handelstage aufbewahrt werden. Gebraucht wird der Vortag; ein paar
# Tage Reserve überbrücken Feiertage und Wochenenden, ohne dass die Datei wächst.
BEHALTENE_TAGE = 5

# Schlüssel für vorbefüllte Tage. Die Vorbefüllung legt HEUTIGE Werte unter dem
# Datum des letzten Handelstages ab, damit der Agent beim Einschalten nicht einen
# ganzen Tag lang enthält (siehe research/seed_implied_vol.py). Das ist eine
# Krücke — und sie muss erkennbar bleiben: Das Reasoning eines Agenten landet im
# Entscheidungslog (MiFID II). Stünde dort ein Datum, an dem diese Werte nie
# galten, behauptete der Audit-Trail etwas Falsches.
_SEEDED = "_vorbefuellt"

_CHANNELS = ("implied_vol", "implied_vol_reference", "implied_vol_reference_date")
_LEER = dict.fromkeys(_CHANNELS)


def _enabled() -> bool:
    """Flag über die einzige Config-Naht des Finance-Core (CODING_POLICY §2.10)."""
    try:
        return bool(getattr(config.get_config(), "VIXAWARE_IMPLIED_VOL_ENABLED", False))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — eine kaputte Config darf nicht einschalten
        # Review #3055 POLICY-01: fail-safe ist richtig, stillschweigend ist es
        # nicht. Eine kaputte Config-Naht wuerde den Agenten dauerhaft auf den
        # alten Pfad zwingen, ohne dass es irgendwo auffiele.
        logger.warning(
            "ImpliedVol: Flag nicht lesbar (%s) — es bleibt beim alten "
            "VIX-Pfad (fail-safe).",
            exc,
        )
        return False


def _default_path() -> Path:
    """Dauerhafter Ablageort, gespiegelt von ``core/audit_paths`` (INV-02/INV-29).

    Unter einer paketierten Desktop-Installation ist das Arbeitsverzeichnis das
    App-Bundle, das der Bootstrap bei **jedem** Update ersetzt. Läge die Datei
    dort, wäre nach jedem Update die Vortagsreferenz weg und der Agent enthielte
    sich einen Tag lang — ohne erkennbaren Grund im Log.
    """
    base = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    return Path(base) / "implied_vol.json" if base else Path("implied_vol.json")


class ImpliedVolStore:
    """Tages-Cache und Vortagsreferenz, als eine kleine JSON-Datei.

    Aufbau ``{"YYYY-MM-DD": {"AAPL": 0.2490, ...}, ...}``.

    Bewusst kein DB-Tabelle: Die Enterprise-Edition bräuchte dafür eine
    Alembic-Migration, die Desktop-Edition nicht (BORA) — eine Datei ist in
    beiden Editionen dasselbe und ohne Migrationspfad.

    Alle Lese- und Schreibwege sind **best effort**: Eine kaputte oder halb
    geschriebene Datei darf den Zyklus nicht anhalten. Schlimmstenfalls fehlt die
    Referenz, und der Agent enthält sich (AC-8) — der konservative Ausgang.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else _default_path()
        self._lock = threading.Lock()

    # -- Platte ---------------------------------------------------------

    def _laden(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as fh:
                daten = json.load(fh)
            return daten if isinstance(daten, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ImpliedVolStore: %s nicht lesbar (%s) — ohne Referenz weiter, "
                "der Agent enthält sich.",
                self.path,
                exc,
            )
            return {}

    def _schreiben(self, daten: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Über eine Nebendatei und dann umbenennen: ein Abbruch mitten im
            # Schreiben hinterlässt sonst genau die halbe Datei, die _laden()
            # anschließend verwerfen muss.
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(daten, fh)
            os.replace(tmp, self.path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ImpliedVolStore: %s nicht schreibbar (%s).", self.path, exc)

    # -- Nutzung --------------------------------------------------------

    def get(self, symbol: str, tag: dt.date) -> Optional[float]:
        wert = self._laden().get(tag.isoformat(), {}).get(symbol)
        return float(wert) if isinstance(wert, (int, float)) else None

    def put(self, symbol: str, tag: dt.date, iv: float) -> None:
        with self._lock:
            daten = self._laden()
            daten.setdefault(tag.isoformat(), {})[symbol] = float(iv)
            for alt in sorted(daten)[:-BEHALTENE_TAGE]:
                daten.pop(alt, None)
            self._schreiben(daten)

    def put_seeded_day(self, tag: dt.date, werte: dict) -> None:
        """Einen ganzen Tag auf einmal ablegen und als **vorbefüllt** markieren."""
        with self._lock:
            daten = self._laden()
            daten[tag.isoformat()] = {k: float(v) for k, v in werte.items()}
            daten.setdefault(_SEEDED, [])
            if tag.isoformat() not in daten[_SEEDED]:
                daten[_SEEDED].append(tag.isoformat())
            for alt in sorted(d for d in daten if d != _SEEDED)[:-BEHALTENE_TAGE]:
                daten.pop(alt, None)
            self._schreiben(daten)

    def reference(self, tag: dt.date):
        """Verteilung des letzten **abgeschlossenen** Tages vor ``tag``.

        Der jüngste Tag *vor* heute, nicht „heute minus eins": Wochenenden und
        Feiertage hätten sonst montags nie eine Referenz, und der Agent enthielte
        sich an einem von fünf Handelstagen.

        Der laufende Tag bleibt ausgeschlossen (AC-7) — sonst würde ein Titel
        gegen eine Verteilung bewertet, die ihn selbst schon enthält.

        Weniger als zwei Werte sind keine Verteilung: daraus ließe sich kein Rang
        bilden, nur ein Münzwurf mit Nachkommastellen. Dann ``(None, None)``.
        """
        daten = self._laden()
        heute = tag.isoformat()
        vorbefuellt = set(daten.get(_SEEDED) or [])
        # Der Marker liegt in derselben Datei und darf weder als Handelstag noch
        # als IV-Wert durchgehen.
        tage = (d for d in daten if d != _SEEDED and d < heute)
        for datum in sorted(tage, reverse=True):
            werte = [
                float(v) for v in daten[datum].values() if isinstance(v, (int, float))
            ]
            if len(werte) >= 2:
                if datum in vorbefuellt:
                    # Sichtbar bis ins Entscheidungslog: der Agent schreibt das
                    # Referenzdatum wörtlich in sein Reasoning.
                    return werte, f"{datum} (vorbefüllt)"
                return werte, datum
        return None, None


# ---------------------------------------------------------------------------
# Sim-Weiche
# ---------------------------------------------------------------------------
#
# Der Sim bootet die ECHTE Engine (core/sim/runner.py:284), der Erzeuger läuft
# dort also mit. Ohne eigene Quelle würde er die Optionskette von HEUTE abrufen,
# während der Sim einen Tag im Januar nachspielt. Das wäre ein Vorgriff auf die
# Zukunft — und einer, der das Ergebnis BESSER aussehen ließe als die
# Wirklichkeit, also die gefährlichste Sorte.
#
# Im Sim kommt die IV deshalb aus ``sim_corpus/iv/atm30_implied_vol.parquet``,
# derselben Reihe, auf der die Machbarkeitsstudie steht.

_SIM_TABELLE: Optional[dict] = None


def _sim_mode() -> bool:
    try:
        return bool(getattr(config.get_config(), "SIM_MODE", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ImpliedVol: SIM_MODE nicht lesbar (%s) — es wird der Live-Pfad "
            "angenommen.",
            exc,
        )
        return False


def _sim_heute() -> Optional[dt.date]:
    """Der simulierte Tag, nicht der Kalendertag."""
    try:
        from core.sim.clock import get_sim_clock

        return get_sim_clock().current_time.date()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ImpliedVol[SIM]: Sim-Uhr nicht lesbar (%s) — der Agent enthält " "sich.",
            exc,
        )
        return None


def _sim_tabelle() -> dict:
    """``{"YYYY-MM-DD": {"AAPL": 0.2490, ...}}`` aus der Korpus-Reihe.

    Einmal je Prozess geladen — 71.581 Zeilen sind klein genug für den Speicher
    und ein erneutes Lesen je Zyklus wäre bei 300 Zyklen spürbar.
    """
    global _SIM_TABELLE
    if _SIM_TABELLE is not None:
        return _SIM_TABELLE
    _SIM_TABELLE = {}
    pfad = None
    try:
        import pandas as pd

        basis = str(getattr(config.get_config(), "SIM_CORPUS_DIR", "sim_corpus"))
        pfad = Path(basis) / "iv" / "atm30_implied_vol.parquet"
        if not pfad.is_file():
            logger.warning(
                "ImpliedVol[SIM]: %s fehlt — VIXAwareRiskAgent enthält sich auf "
                "ALLEN Titeln. Die Reihe baut research/build_iv_history.py. "
                "Ohne diesen Hinweis sähe der Lauf wie ein sauberes Nullergebnis aus.",
                pfad,
            )
            return _SIM_TABELLE
        df = pd.read_parquet(pfad)
        for sym, tag, iv in zip(df["sym"], df["tag"], df["iv"]):
            _SIM_TABELLE.setdefault(pd.Timestamp(tag).date().isoformat(), {})[
                str(sym)
            ] = float(iv)
        logger.info(
            "ImpliedVol[SIM]: %d Handelstage aus %s geladen.", len(_SIM_TABELLE), pfad
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ImpliedVol[SIM]: %s nicht lesbar (%s) — der Agent enthält sich auf "
            "allen Titeln.",
            pfad,
            exc,
            exc_info=True,
        )
    return _SIM_TABELLE


def _sim_state_keys(symbol: str) -> dict:
    tag = _sim_heute()
    if tag is None:
        return dict(_LEER)
    tabelle = _sim_tabelle()
    heute = tag.isoformat()

    referenz = referenz_datum = None
    for datum in sorted((d for d in tabelle if d < heute), reverse=True):
        werte = list(tabelle[datum].values())
        if len(werte) >= 2:
            referenz, referenz_datum = werte, datum
            break

    return {
        "implied_vol": tabelle.get(heute, {}).get(symbol),
        "implied_vol_reference": referenz,
        "implied_vol_reference_date": referenz_datum,
    }


_STORE: Optional[ImpliedVolStore] = None


def _default_store() -> ImpliedVolStore:
    global _STORE
    if _STORE is None:
        _STORE = ImpliedVolStore()
    return _STORE


def _default_client():
    from core.keychain import load_secrets_from_keychain

    load_secrets_from_keychain()
    from alpaca.data.historical.option import OptionHistoricalDataClient

    return OptionHistoricalDataClient(
        os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    )


def fetch_atm30_iv(
    symbol: str, spot: float, tag: dt.date, client=None
) -> Optional[float]:
    """ATM-30-Tage-IV eines Titels — ein gefilterter Kettenabruf.

    Gibt ``None`` zurück, wo immer etwas fehlt oder schiefgeht. Das ist der
    konservative Ausgang: Der Agent enthält sich dann, statt eine geratene Stimme
    abzugeben (Plan §9.5). Eine externe Abhängigkeit im Handelspfad darf den
    Zyklus nie abbrechen.
    """
    if not spot or spot <= 0 or not math.isfinite(spot):
        # Ohne Kurs gibt es kein ATM-Band. Ein ungefilterter Abruf kostete das
        # Siebenfache und lieferte trotzdem nichts Vergleichbares.
        return None
    try:
        from alpaca.data.requests import OptionChainRequest

        cl = client if client is not None else _default_client()
        kette = cl.get_option_chain(
            OptionChainRequest(
                underlying_symbol=symbol,
                expiration_date_gte=tag + dt.timedelta(days=REST_MIN_TAGE),
                expiration_date_lte=tag + dt.timedelta(days=REST_MAX_TAGE),
                strike_price_gte=round(spot * (1 - ATM_BAND), 2),
                strike_price_lte=round(spot * (1 + ATM_BAND), 2),
            )
        )
    except Exception as exc:  # noqa: BLE001 — Ausfall ⇒ Enthaltung, kein Abbruch
        logger.warning(
            "ImpliedVol: Kettenabruf für %s fehlgeschlagen (%s) — "
            "der Agent enthält sich.",
            symbol,
            exc,
        )
        return None

    werte = []
    for occ, snap in (kette or {}).items():
        iv = getattr(snap, "implied_volatility", None)
        if iv is None:
            continue
        try:
            iv = float(iv)
            strike = int(str(occ)[-8:]) / 1000.0
        except (TypeError, ValueError) as exc:
            logger.warning(
                "ImpliedVol: Konnte IV-Daten nicht parsen (%s) - ueberspringe", exc
            )
            continue
        if not (IV_MIN < iv < IV_MAX):
            continue
        # Clientseitig nochmals aufs Band: die serverseitige Filterung ist nicht
        # als exakt zugesichert, und ein Strike weit neben dem Kurs trägt wegen
        # des Volatilitäts-Smiles eine ganz andere IV. Ihn mitzurechnen hieße,
        # Titel über verschiedene Punkte derselben Kurve zu vergleichen.
        if abs(strike - spot) / spot <= ATM_BAND:
            werte.append(iv)

    if not werte:
        return None
    # Median, nicht Mittelwert: ein einzelner Ausreißer (Fehlquote, illiquider
    # Strike) verschöbe einen Mittelwert und damit den Rang des Titels.
    return float(statistics.median(werte))


def implied_vol_state_keys(
    symbol: str,
    spot: float,
    tag: Optional[dt.date] = None,
    store: Optional[ImpliedVolStore] = None,
    client=None,
) -> dict:
    """Die drei ``SymbolEvalState``-Kanäle für einen Titel.

    Hausmuster #1949 (``_regime_conditioner_state_keys``): Flag AUS ⇒ alle Kanäle
    ``None`` **und kein Netzverkehr**. Die Kanäle sind deklariert
    (``graph.py``), langgraph verwirft sonst nicht deklarierte Schlüssel.

    Der Flag-Test steht bewusst VOR jeder Client-Erzeugung: Diese liest Schlüssel
    aus dem Keychain und kostet einen Handshake. Bei 30 Kandidaten und 26 Zyklen
    wären das 780 Abrufe für einen Zustand, der byte-identisch zum Ist sein soll.
    """
    if not _enabled():
        return dict(_LEER)

    if _sim_mode():
        # Kein Netzverkehr, kein Cache, kein Kalendertag: der Sim liest den Korpus
        # zum SIMULIERTEN Datum. Die Vortagsreferenz steht dort ohnehin schon.
        return _sim_state_keys(symbol)

    tag = tag or dt.date.today()
    st = store if store is not None else _default_store()

    iv = st.get(symbol, tag)
    if iv is None:
        iv = fetch_atm30_iv(symbol, spot, tag, client=client)
        if iv is not None:
            st.put(symbol, tag, iv)

    referenz, referenz_datum = st.reference(tag)
    return {
        "implied_vol": iv,
        "implied_vol_reference": referenz,
        "implied_vol_reference_date": referenz_datum,
    }
