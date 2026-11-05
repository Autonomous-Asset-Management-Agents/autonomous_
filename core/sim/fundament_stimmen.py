"""Fundamental-Stimmen der Simulation als reine Funktionen (#5076, ADR-027).

Spiegel von ``FundamentalsAgent.vote`` und dem PEG-Pfad von ``ValuationAgent.vote``, aus demselben
Grund wie die Momentum-Konstanten in ``stimmen_rechner``: ``core.round_table`` zieht beim Import
die Engine-Umgebung hoch, und die Agenten lesen Flags, Ablage und Redis, also den Rechner statt
des Korpus. Die Formeln bewacht ``test_5076_fundament_stimmen.py`` gegen die echten Agenten.

``None`` heisst keine Stimme, wie die Enthaltung des Agenten.
"""

from __future__ import annotations

from typing import Mapping, Optional

# agenten/fundamentals.py (ADR-RT-FUND-01)
_GROWTH_STRONG_PCT = 15.0
_GROWTH_SHRINKING_PCT = 0.0
_MARGIN_STRONG_PCT = 20.0
_SCHRITT = 0.2
# agenten/valuation.py (ADR-RT-VAL-01)
_PEG_CHEAP = 1.0
_PEG_STRETCHED = 3.0
_DEBT_HEALTHY = 1.0
_SCORE_CHEAP = 0.7
_SCORE_STRETCHED = 0.3


def _pct(anteil: Optional[float]) -> Optional[float]:
    return None if anteil is None else float(anteil) * 100.0


def fundamentals_score(fund: Optional[Mapping]) -> Optional[float]:
    """Score von ``FundamentalsAgent``; ``None`` ohne Umsatzwachstum und Marge."""
    if not fund:
        return None
    wachstum = _pct(fund.get("revenue_yoy"))
    marge = _pct(fund.get("net_margin"))
    if wachstum is None and marge is None:
        return None
    score = 0.5
    if wachstum is not None:
        if wachstum >= _GROWTH_STRONG_PCT:
            score += _SCHRITT
        elif wachstum <= _GROWTH_SHRINKING_PCT:
            score -= _SCHRITT
    if marge is not None:
        if marge >= _MARGIN_STRONG_PCT:
            score += _SCHRITT
        elif marge <= 0:
            score -= _SCHRITT
    return max(0.0, min(1.0, score))


def _gewinn_je_aktie(fund: Mapping) -> Optional[float]:
    verwaessert = fund.get("eps_diluted")
    return verwaessert if verwaessert is not None else fund.get("eps_basic")


def _verschuldung(fund: Mapping) -> Optional[float]:
    schulden, eigen = fund.get("total_liabilities"), fund.get("total_equity")
    if schulden is None or not eigen:
        return None
    return float(schulden) / float(eigen)


def valuation_score(fund: Optional[Mapping], close: float) -> Optional[float]:
    """PEG-Pfad von ``ValuationAgent`` zum Kurs des Tages.

    ``None`` auch im Band „neither cheap nor stretched": Dort wirft der Agent und der Round
    Table verwirft die Stimme (Plan #5076 §6 Punkt 5).
    """
    if not fund:
        return None
    eps = _gewinn_je_aktie(fund)
    wachstum = _pct(fund.get("eps_yoy"))
    if eps in (None, 0) or wachstum is None or wachstum <= 0:
        return None
    peg = (float(close) / float(eps)) / wachstum
    schuld = _verschuldung(fund)
    if peg < _PEG_CHEAP and (schuld is None or schuld < _DEBT_HEALTHY):
        return _SCORE_CHEAP
    if peg > _PEG_STRETCHED:
        return _SCORE_STRETCHED
    return None
