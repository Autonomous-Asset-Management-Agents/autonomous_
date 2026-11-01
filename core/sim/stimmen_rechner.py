"""Stimmen aus dem Kurs-Korpus berechnen (#4634, STW-8; ADR-027 Regel 8).

``StimmenRechner`` ist der Vertrag der Phase ``stimmen`` in ``core.sim.vorbereitung``: je
Handelstag ``(decisions, votes)`` im Format von ``ReplayVoteStore.from_parquet`` bzw.
``StimmenStore.from_verzeichnis`` (#3712). ``OfflineStimmenRechner`` rechnet ``MomentumAgent``,
``TrendAgent`` und ``DrawdownGuardAgent`` auf den Tagesbars, ``FundamentalsAgent``,
``ValuationAgent`` und ``QualityAgent`` auf dem ausgelieferten Fundamental-Abbild: deterministisch,
ohne Netz, ohne API-Kosten. LSTM, LLM und News bildet er nicht ab; was eine Strategie davon nutzt,
weist ``vergleich.lies_ergebnis`` je Ergebnis aus (ADR-027 Regel 4, #5071).

Gleiche Formeln wie live:
- Momentum = 12-1-Monats-Momentum, ``0.5 + 0.5*tanh(m / 0.60)`` (``agenten/momentum.py``,
  ``MOMENTUM_SCORE_SMOOTH_ENABLED`` Standard an), ab 40 Closes.
- Trend = ``0.5 + 0.5*trend_baz`` (``agenten/trend.py``), ab ``TREND_BAZ_MIN_BARS`` Bars.
- Drawdown = 30-Bar-Pfad von ``agenten/drawdown_guard.py``, ab 5 Closes.
- Fundamentals und Valuation: ``core.sim.fundament_stimmen``.
- Quality = Perzentilrang des Composites (``core.quality_score``) gegen den Querschnitt des
  Universums am Kalender-Vortag, ohne Zustand zwischen Tagen (#5083).
Fundamentaldaten kommen nur aus dem Abbild, nie aus ``financials_cache``: Die Ablage ist je
Installation anders, das braeche die Bitgleichheit zwischen Rechnern. Point-in-time ueber
``filing_date``. Zu kurze Historie = keine Zeile (Enthaltung, wie der Agent ohne Daten).

Der Stand (``stand()``) benennt, womit ein Korpus gerechnet ist: Aendern sich die Stimmen oder
das Abbild, gilt der Korpus nicht mehr und die Phase ``stimmen`` rechnet ihn neu.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

import numpy as np
import pandas as pd

from core.report.financials_feed import _as_date, _pit_select, derive_fundamentals
from core.report.financials_snapshot import DEFAULT_SNAPSHOT_PATH, load_snapshot
from core.sim.fundament_stimmen import fundamentals_score, valuation_score

# Konstanten aus agenten/momentum.py (_MOMENTUM_SCORE_SCALE) und dessen Fenstern; hier
# gespiegelt statt importiert, weil ``core.round_table`` beim Import Settings, Kill-Switch
# und Registry hochzieht — der Subprozess braucht davon nichts.
_MOMENTUM_SCORE_SCALE = 0.60
_MOMENTUM_MIN_CLOSES = 40
_MOMENTUM_SKIP = 20
_MOMENTUM_LOOKBACK = 252
# agenten/drawdown_guard.py, aus demselben Grund gespiegelt; ADR-R14 (Fenster) und der
# 7-%-Veto bei den Vorgaben DRAWDOWN_GUARD_VETO_ENABLED=true und
# DRAWDOWN_GUARD_CONDITIONER_ENABLED=false. ``test_5071_round_table_sim.py`` vergleicht beide.
_DRAWDOWN_FENSTER = 30
_DRAWDOWN_MIN_BARS = 5
_DRAWDOWN_VETO = 0.07
_DRAWDOWN_SKALA = 5.0
# Gespeichertes Ausgangsgewicht je Stimme. Muss > 0 sein: ``stimmen_sim.tausche_gewichte``
# behandelt Gewicht 0.0 als Enthaltung, die keine Strategie wieder aktiviert.
# Momentum 0.45 = Default MOMENTUM_AGENT_WEIGHT; Trend 0.30 = Startwert beim Einschalten
# (agenten/trend.py, Owner 30.09.2026). Fundamentals 0.25 = Default FUNDAMENTALS_AGENT_WEIGHT;
# Valuation 0.10 statt Default 0.0, weil > 0 noetig (s. o.). Quality 0.30 = dormantes Gewicht
# (agenten/quality.py). DrawdownGuard 0.60 = Token, er wirkt nur ueber das Veto
# (agenten/drawdown_guard.py, #3084). Jede Strategie ueberschreibt sie mit ihren Gewichten.
GEWICHTE: Dict[str, float] = {
    "MomentumAgent": 0.45,
    "TrendAgent": 0.30,
    "FundamentalsAgent": 0.25,
    "ValuationAgent": 0.10,
    "QualityAgent": 0.30,
    "DrawdownGuardAgent": 0.60,
}

DECISION_SPALTEN = ["did", "symbol", "ts"]
VOTE_SPALTEN = ["did", "agent", "score", "weight", "vetoed"]

#: Stand der Stimmen; hochzaehlen, wenn ein Korpus damit etwas anderes enthielte (#5071: 2 = mit
#: Waechter; #5076: 3 = mit Fundamentals und Valuation; #5083: 4 = mit Quality). Ein Korpus ohne
#: ``stand.json`` hat den alten Stand.
STIMMEN_STAND = 4
STAND_DATEI = "stand.json"
#: Das ausgelieferte Abbild, unabhaengig vom Arbeitsverzeichnis des Subprozesses.
ABBILD = Path(__file__).resolve().parents[2] / DEFAULT_SNAPSHOT_PATH


def stand(abbild: Optional[Path] = None) -> Dict[str, Any]:
    """Die Kennung eines Korpus, der jetzt gerechnet wuerde. Ohne Abbild ``OSError``."""
    inhalt = Path(ABBILD if abbild is None else abbild).read_bytes()
    return {
        "stimmen_stand": STIMMEN_STAND,
        "abbild_sha256": hashlib.sha256(inhalt).hexdigest(),
    }


def lies_stand(stimmen_dir: Path) -> Optional[Dict[str, Any]]:
    """``stimmen/stand.json`` eines Korpus; ``None``, wenn sie fehlt oder unlesbar ist."""
    try:
        return json.loads((Path(stimmen_dir) / STAND_DATEI).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def drawdown_stimme(closes: Sequence[float]) -> Optional[Tuple[float, bool]]:
    """``(score, vetoed)`` aus den Closes bis zum Tag; unter fuenf Bars keine Stimme.

    Der 1-Bar-Rueckfall des Agenten entfaellt: Der Korpus traegt hier nur Closes.
    """
    if len(closes) < _DRAWDOWN_MIN_BARS:
        return None
    gipfel = float(np.max(closes[-_DRAWDOWN_FENSTER:]))
    if gipfel <= 0:
        return None
    drawdown = (gipfel - float(closes[-1])) / gipfel
    return (
        max(0.0, min(1.0, 1.0 - drawdown * _DRAWDOWN_SKALA)),
        drawdown > _DRAWDOWN_VETO,
    )


@runtime_checkable
class StimmenRechner(Protocol):
    def berechne(
        self, korpus_dir: Path, tag: str, symbole: List[str]
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        """``(decisions[did, symbol, ts], votes[did, agent, score, weight, vetoed])``."""


def _trend_baz(close: pd.Series) -> pd.Series:
    # Spaeter Import: features.py selbst ist rein (numpy/pandas), das Paket core.round_table
    # zieht beim Import aber die Engine-Umgebung hoch — nur in der Phase stimmen noetig.
    from core.round_table.features import trend_baz_series

    return trend_baz_series(close)


class OfflineStimmenRechner:
    """Stimmen je Symbol und Tag aus ``<SYMBOL>.parquet`` im Korpus und dem Abbild."""

    def __init__(self, abbild: Path = ABBILD) -> None:
        self._abbild = Path(abbild)
        # (korpus_dir, symbol) -> (closes, trend_baz); einmal je Lauf gelesen und gerechnet.
        self._reihen: Dict[Tuple[str, str], Tuple[pd.Series, pd.Series]] = {}
        self._einreichungen: Optional[Dict[str, Tuple[List[Mapping], List[date]]]] = (
            None
        )
        # (symbol, Anzahl sichtbarer Einreichungen) -> Ableitung: Sie aendert sich nur mit
        # einer neuen Einreichung. Der Kurs geht erst in ``valuation_score`` ein.
        self._funde: Dict[Tuple[str, int], Optional[Dict[str, Any]]] = {}
        self._composites: Dict[Tuple[str, int], Optional[float]] = {}

    def stand(self) -> Dict[str, Any]:
        return stand(self._abbild)

    def _zeilen(self, symbol: str) -> Tuple[List[Mapping], List[date]]:
        """Roh-Zeilen eines Titels und ihre sortierten ``filing_date``. Ohne Abbild Abbruch."""
        if self._einreichungen is None:
            inhalt = load_snapshot(str(self._abbild))
            if not inhalt:
                raise FileNotFoundError(f"Fundamental-Abbild fehlt: {self._abbild}")
            self._einreichungen = {}
            for titel, eintrag in inhalt.items():
                zeilen = eintrag.get("results") or []
                daten = [
                    _as_date(z["filing_date"]) for z in _pit_select(zeilen, date.max)
                ]
                self._einreichungen[titel] = (zeilen, sorted(daten))
        return self._einreichungen.get(symbol, ([], []))

    def _fund(self, symbol: str, tag: date) -> Optional[Dict[str, Any]]:
        zeilen, daten = self._zeilen(symbol)
        anzahl = bisect.bisect_right(daten, tag)
        if anzahl == 0:
            return None
        schluessel = (symbol, anzahl)
        if schluessel not in self._funde:
            self._funde[schluessel] = derive_fundamentals(zeilen, tag)
        return self._funde[schluessel]

    def _composite(self, symbol: str, tag: date) -> Optional[float]:
        # Spaeter Import: quality_score zieht ``config`` hoch, wie _trend_baz nur hier noetig.
        from core.quality_score import composite_quality

        _, daten = self._zeilen(symbol)
        schluessel = (symbol, bisect.bisect_right(daten, tag))
        if schluessel not in self._composites:
            fund = self._fund(symbol, tag)
            self._composites[schluessel] = composite_quality(fund) if fund else None
        return self._composites[schluessel]

    def _referenz(self, symbole: List[str], tag: date) -> List[float]:
        """Querschnitt des Universums am Kalender-Vortag: kein Zustand, kein Look-ahead."""
        vortag = tag - timedelta(days=1)
        werte = (self._composite(s, vortag) for s in symbole)
        return [w for w in werte if w is not None]

    def _quality(
        self, symbol: str, tag: date, referenz: List[float]
    ) -> Optional[float]:
        from core.quality_score import quality_percentile

        eigen = self._composite(symbol, tag)
        return None if eigen is None else quality_percentile(eigen, referenz)

    def _reihe(self, korpus_dir: Path, symbol: str):
        schluessel = (str(korpus_dir), symbol)
        if schluessel not in self._reihen:
            pfad = Path(korpus_dir) / f"{symbol}.parquet"
            if not pfad.exists():
                self._reihen[schluessel] = None
            else:
                df = pd.read_parquet(pfad)
                spalte = "close" if "close" in df.columns else "Close"
                close = df[spalte].astype(float).dropna()
                close.index = pd.DatetimeIndex(close.index).normalize()
                close = close.sort_index()
                self._reihen[schluessel] = (close, _trend_baz(close))
        return self._reihen[schluessel]

    def berechne(
        self, korpus_dir: Path, tag: str, symbole: List[str]
    ) -> Tuple[pd.DataFrame, pd.DataFrame]:
        stichtag = pd.Timestamp(tag)
        referenz = self._referenz(symbole, stichtag.date())
        dec, vot = [], []
        for symbol in sorted(symbole):
            reihe = self._reihe(korpus_dir, symbol)
            if reihe is None:
                continue
            close, trend = reihe
            bis = close.index.searchsorted(stichtag, side="right")
            if bis == 0 or close.index[bis - 1] != stichtag:
                continue  # kein Bar an diesem Tag: keine Entscheidung
            stimmen = []
            closes = close.iloc[:bis]
            if len(closes) >= _MOMENTUM_MIN_CLOSES:
                p_end = float(closes.iloc[-_MOMENTUM_SKIP])
                p_start = float(
                    closes.iloc[-_MOMENTUM_LOOKBACK]
                    if len(closes) >= _MOMENTUM_LOOKBACK
                    else closes.iloc[0]
                )
                if p_start > 0:
                    m = (p_end - p_start) / p_start
                    score = 0.5 + 0.5 * math.tanh(m / _MOMENTUM_SCORE_SCALE)
                    stimmen.append(("MomentumAgent", score, False))
            t = float(trend.iloc[bis - 1])
            if math.isfinite(t):
                stimmen.append(("TrendAgent", min(1.0, max(0.0, 0.5 + 0.5 * t)), False))
            fund = self._fund(symbol, stichtag.date())
            for agent, score in (
                ("FundamentalsAgent", fundamentals_score(fund)),
                ("ValuationAgent", valuation_score(fund, float(closes.iloc[-1]))),
                ("QualityAgent", self._quality(symbol, stichtag.date(), referenz)),
            ):
                if score is not None:
                    stimmen.append((agent, score, False))
            waechter = drawdown_stimme(closes.to_numpy())
            if waechter is not None:
                stimmen.append(("DrawdownGuardAgent", *waechter))
            if not stimmen:
                continue
            did = len(dec)
            dec.append({"did": did, "symbol": symbol, "ts": f"{tag}T20:00:00+00:00"})
            for agent, score, vetoed in stimmen:
                vot.append(
                    {
                        "did": did,
                        "agent": agent,
                        "score": float(score),
                        "weight": GEWICHTE[agent],
                        "vetoed": bool(vetoed),
                    }
                )
        return (
            pd.DataFrame(dec, columns=DECISION_SPALTEN),
            pd.DataFrame(vot, columns=VOTE_SPALTEN),
        )
