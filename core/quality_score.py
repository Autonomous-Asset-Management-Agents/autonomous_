"""#3275 — Composite-Quality-Score: reine Rechen-Naht + Vorsession-Producer.

Der Quality-Faktor (Novy-Marx Gross-Profitability + Piotroski-Finanzstärke) wird
AUSSCHLIESSLICH aus dem vorhandenen PIT-Fundamentals-Feed abgeleitet — kein neuer
Datenbezug. Dieses Modul enthält:

* die **reine** Composite-Rechnung (netzfrei, deterministisch, unit-getestet):
  ``gross_profitability``, ``piotroski_score`` (voller 9-Punkte-F-Score aus
  current+prior), ``composite_quality`` (z-Blend beider), ``quality_percentile``;
* den **Producer** ``quality_state_keys`` + Store (Vortagsreferenz, AC-7), der exakt
  das ``implied_vol`` / ``risk_reversal``-Hausmuster spiegelt (flag-gegatet, best
  effort, nie Zyklus-Abbruch). OFF (Default) ⇒ alle Kanäle None ⇒ Agent enthält sich
  ⇒ byte-identisch.

Die Zentren/Skalen sind redaktionelle Startkalibrierung (ADR-RT-QUAL-01), KEINE
Modell-Ausgabe — Kandidaten für den Walk-forward bei Aktivierung.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
from pathlib import Path
from typing import Optional, Sequence

import config

logger = logging.getLogger(__name__)

# ADR-RT-QUAL-01: Novy-Marx Gross-Profitability (gross_profit/assets) — grobes
# Marktmittel ~0.33 als Neutralpunkt, Skala 0.33 hält den Score responsiv in (0,1).
_GP_NEUTRAL = 0.33
_GP_SCALE = 0.33

_CHANNELS = ("quality_score", "quality_reference", "quality_reference_date")
_LEER: dict = dict.fromkeys(_CHANNELS)


# ---------------------------------------------------------------------------
# Reine Rechen-Naht (netzfrei, deterministisch)
# ---------------------------------------------------------------------------


def _f(value) -> Optional[float]:
    """Best-effort finite float, sonst None (ein Datenloch darf nie werfen)."""
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ratio(num, den) -> Optional[float]:
    n, d = _f(num), _f(den)
    if n is None or not d:
        return None
    return n / d


def gross_profitability(fund: dict) -> Optional[float]:
    """Novy-Marx: gross_profit / total_assets. None, wenn nicht bildbar."""
    return _ratio(fund.get("gross_profit"), fund.get("total_assets"))


def piotroski_score(fund: dict) -> Optional[int]:
    """Voller Piotroski-F-Score (0–9) aus current+prior.

    Punkte — Profitabilität: ROA>0, CFO>0, ΔROA>0, Accrual (CFO>NI);
    Leverage/Liquidität: ΔLeverage<0 (total_liabilities/total_assets),
    ΔCurrent-Ratio>0, keine Aktien-Verwässerung; Effizienz: ΔGross-Margin>0,
    ΔAsset-Turnover>0.

    ADR-RT-QUAL-03: der F-Score ist per Definition ein VOLLSTÄNDIGER 9-Punkte-Test
    (0–9). Fehlt eine der current/prior-Größen (typisch: keine PIT-gültige
    Vorperiode), ist der Score nicht vergleichbar bildbar ⇒ None (Enthaltungs-
    Grundlage), statt einen verkürzten Teil-Score wie einen vollen auszugeben.
    """
    ni = _f(fund.get("net_income"))
    ta = _f(fund.get("total_assets"))
    cfo = _f(fund.get("operating_cash_flow"))
    tl = _f(fund.get("total_liabilities"))
    ca = _f(fund.get("current_assets"))
    cl = _f(fund.get("current_liabilities"))
    sh = _f(fund.get("diluted_shares"))
    gp = _f(fund.get("gross_profit"))
    rev = _f(fund.get("revenue"))

    p_ni = _f(fund.get("prior_net_income"))
    p_ta = _f(fund.get("prior_total_assets"))
    p_tl = _f(fund.get("prior_total_liabilities"))
    p_ca = _f(fund.get("prior_current_assets"))
    p_cl = _f(fund.get("prior_current_liabilities"))
    p_sh = _f(fund.get("prior_diluted_shares"))
    p_gp = _f(fund.get("prior_gross_profit"))
    p_rev = _f(fund.get("prior_revenue"))

    roa = _ratio(ni, ta)
    p_roa = _ratio(p_ni, p_ta)
    lev = _ratio(tl, ta)
    p_lev = _ratio(p_tl, p_ta)
    cr = _ratio(ca, cl)
    p_cr = _ratio(p_ca, p_cl)
    gm = _ratio(gp, rev)
    p_gm = _ratio(p_gp, p_rev)
    turn = _ratio(rev, ta)
    p_turn = _ratio(p_rev, p_ta)

    needed = (
        ni,
        cfo,
        roa,
        p_roa,
        lev,
        p_lev,
        cr,
        p_cr,
        sh,
        p_sh,
        gm,
        p_gm,
        turn,
        p_turn,
    )
    if any(v is None for v in needed):
        return None

    points = (
        roa > 0.0,  # 1: ROA positiv
        cfo > 0.0,  # 2: operativer Cashflow positiv
        roa > p_roa,  # 3: ΔROA > 0
        cfo > ni,  # 4: Accrual — Cashflow deckt die Gewinne
        lev < p_lev,  # 5: ΔLeverage < 0 (Verschuldung gesunken)
        cr > p_cr,  # 6: ΔCurrent-Ratio > 0 (Liquidität gestiegen)
        sh <= p_sh,  # 7: keine Aktien-Verwässerung
        gm > p_gm,  # 8: ΔGross-Margin > 0
        turn > p_turn,  # 9: ΔAsset-Turnover > 0
    )
    return int(sum(1 for p in points if p))


def _squash(value: float, neutral: float, scale: float) -> float:
    """Monotone, beschränkte Abbildung nach (0,1) mit 0.5 bei ``neutral``."""
    return 0.5 + 0.5 * math.tanh((value - neutral) / scale)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def composite_quality(fund: dict) -> Optional[float]:
    """Composite v2 = Gleichgewichts-z-Blend aus {Novy-Marx Gross-Profitability
    (kontinuierlich, gesquasht) und Piotroski-F/9}. Piotroski subsumiert
    ROA/Accruals/Margen-/Leverage-Δ — daher KEINE separaten v1-Terme mehr (keine
    Doppelzählung). None (Abstain-Grundlage) bei zu dünner Datenlage (fehlende GP
    oder kein voller Piotroski)."""
    gp = gross_profitability(fund)
    pio = piotroski_score(fund)
    if gp is None or pio is None:
        return None
    gp_score = _squash(gp, _GP_NEUTRAL, _GP_SCALE)
    pio_score = pio / 9.0
    return _clamp((gp_score + pio_score) / 2.0)


def quality_percentile(value: float, reference: Sequence[float]) -> Optional[float]:
    """Perzentilrang von ``value`` in ``reference`` in [0,1] (Anteil strikt darunter).

    None bei < 2 nutzbaren, DISTINKTEN Werten — kein Rang aus einer entarteten
    Verteilung (spiegelt ``options_skew.rr_percentile`` / AC-7)."""
    werte = [
        float(v) for v in reference if isinstance(v, (int, float)) and math.isfinite(v)
    ]
    if len(set(werte)) < 2:
        return None
    below = sum(1 for v in werte if v < value)
    return below / len(werte)


# ---------------------------------------------------------------------------
# Producer (Vortagsreferenz, AC-7) — spiegelt implied_vol / risk_reversal
# ---------------------------------------------------------------------------


def _enabled() -> bool:
    """Einzige Config-Naht (CODING_POLICY §2.10). Fail-safe OFF (dark)."""
    try:
        return bool(getattr(config.get_config(), "QUALITY_AGENT_ENABLED", False))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — eine kaputte Config darf nicht einschalten
        logger.exception("QualityScore: Flag nicht lesbar (%s) — bleibt AUS.", exc)
        return False


def _default_path() -> Path:
    base = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    return Path(base) / "quality_score.json" if base else Path("quality_score.json")


_STORE = None


def _default_store():
    """Value-agnostischer JSON-Store {date:{symbol:value}} — dieselbe Klasse wie IV/RR
    (kein Re-Implement, kein Alembic/BORA), nur eine eigene Datei."""
    global _STORE
    if _STORE is None:
        from core.implied_vol import ImpliedVolStore

        _STORE = ImpliedVolStore(path=_default_path())
    return _STORE


def _read_fundamentals(
    symbol: str, as_of: dt.date, close: Optional[float]
) -> Optional[dict]:
    """Denselben PIT-Reader-Pfad wie der FundamentalsAgent (kein neuer Datenbezug)."""
    try:
        from core.report.financials_feed import CachedFundamentalsReader

        lookup = {symbol: float(close)} if close and float(close) > 0 else None
        fund = CachedFundamentalsReader(close_lookup=lookup).get_fundamentals(
            symbol, as_of
        )
        return dict(fund) if fund else None
    except Exception as exc:  # noqa: BLE001 — ein Datenloch darf den Zyklus nie brechen
        logger.warning(
            "QualityScore: Fundamentals-Read für %s fehlgeschlagen (%s) — Agent enthält sich.",
            symbol,
            exc,
        )
        return None


def quality_state_keys(
    symbol: str,
    spot: Optional[float] = None,
    tag: Optional[dt.date] = None,
    store=None,
) -> dict:
    """Die drei ``SymbolEvalState``-Kanäle für ein Symbol (spiegelt
    ``implied_vol.implied_vol_state_keys``). Flag OFF ⇒ alle None, KEINE Arbeit.

    Anders als IV/RR ist der Composite rein FEED-abgeleitet (kein externer Abruf,
    kein Netz) — er funktioniert daher auch unter SIM (der Reader liest PIT AS OF
    ``tag``). Der heutige Composite wird in den Store gelegt, die Referenz ist der
    Querschnitt der letzten ABGESCHLOSSENEN Vorsession (AC-7 — kein Look-ahead).
    Best effort: jeder Fehler ⇒ Kanäle None (Agent enthält sich), nie ein Abbruch.
    """
    if not _enabled():
        return dict(_LEER)
    try:
        tag = tag or dt.date.today()
        st = store if store is not None else _default_store()
        score = st.get(symbol, tag)
        if score is None:
            fund = _read_fundamentals(symbol, tag, spot)
            if fund is not None:
                score = composite_quality(fund)
                if score is not None:
                    st.put(symbol, tag, score)
        referenz, referenz_datum = st.reference(tag)
        return {
            "quality_score": None if score is None else float(score),
            "quality_reference": referenz,
            "quality_reference_date": referenz_datum,
        }
    except Exception as exc:  # noqa: BLE001 — der Producer darf den Zyklus nie brechen
        logger.warning(
            "QualityScore: Producer für %s fehlgeschlagen (%s) — Kanäle None, "
            "QualityAgent enthält sich.",
            symbol,
            exc,
        )
        return dict(_LEER)
