"""Typen und Uhr des Portfolio-Kerns — die Wurzel des Schnitts (#4187, H-5c #4284).

Hierher sind aus ``core/portfolio_manager.py`` wortgleich umgezogen: die massgebliche Uhr
(``_now_utc``, ``_ensure_aware_utc``), der Schalter der Buch-Deckel-Invariante
(``_book_cap_enforced``) und die beiden Score-Dataclasses (``PositionScore``,
``OpportunityScore``). Der Kern fuehrt sie als Re-Export weiter.

Regel (Entscheidung §3, Zirkelimport): Dieses Modul importiert nichts aus
``core.portfolio_*`` — die Themen-Mixins importieren von hier, nie aus dem Kern.
Die spaeten Importe in den Ruempfen bleiben spaet.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional


def _now_utc() -> datetime:
    """Die massgebliche Zeit: unter SIM_MODE die VIRTUELLE Uhr, sonst die Wanduhr.

    ``days_held`` und die Churn-Sperren massen beide gegen ``datetime.now()``.
    Ein Sim-Lauf dauert Minuten Echtzeit, also blieb ``days_held`` dauerhaft 0
    und die Mindesthaltedauer lief NIE ab — jede im Lauf gekaufte Position wurde
    zum permanenten Verdraengungs-Blocker und das Buch fror nach den ersten
    Kaeufen ein. Gemessen im 33-Tage-Replay: 10 Fills statt real 188, davon
    0 SELL, und 22.095 von 23.769 Blockierungen mit
    "minimum holding period not met (0/20 days)".

    Fail-safe in BEIDE Richtungen zur Wanduhr: ohne SIM_MODE und bei jedem
    Lesefehler bleibt der Live-Pfad byte-identisch. Lokaler Import zur
    Aufrufzeit — das Muster dieses Moduls (nie in eine Modulkonstante frieren).
    """
    try:
        from config import get_config

        # STRIKT ``is True`` — nicht ``bool(...)``. Ein truthy Fremdwert
        # (String, 1, Objekt) darf den Live-Pfad NIEMALS auf die Sim-Uhr
        # schieben; fail-safe ist immer die Wanduhr. Gleiches Idiom wie
        # ``core/client_factory.py:27``, wo derselbe Schalter den Broker waehlt.
        if getattr(get_config(), "SIM_MODE", False) is not True:
            return datetime.now(timezone.utc)
    except Exception as exc:  # noqa: BLE001 — unlesbare Config => Live-Pfad
        logging.warning(
            "PortfolioManager: SIM_MODE nicht lesbar (%s) — es gilt die Wanduhr.", exc
        )
        return datetime.now(timezone.utc)
    try:
        from core.sim.clock import get_sim_clock

        now = get_sim_clock().current_time
        if now is not None:
            return now
    except Exception as exc:  # noqa: BLE001 — eine kaputte Sim-Uhr bricht nie den Lauf
        logging.warning(
            "PortfolioManager[SIM]: Sim-Uhr nicht lesbar (%s) — es gilt die Wanduhr. "
            "Die Haltefrist altert dann NICHT mit der simulierten Zeit.",
            exc,
        )
    return datetime.now(timezone.utc)


def _ensure_aware_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave aware datetimes unchanged.

    Defense-in-depth for POLICY-01: PortfolioManager now stamps all history in
    aware UTC, but a naive value could still survive a hot-reload. Normalising here
    guarantees aware-vs-aware arithmetic, so a stray naive stamp can never raise
    TypeError in a cooldown or date comparison.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _book_cap_enforced() -> bool:
    """#2886: is the book-cap invariante armed? Read via get_config() at CALL time
    (CODING_POLICY §2.10) so tests and env can flip it; missing attr ⇒ False
    (dark by default — OFF is byte-identical legacy behaviour)."""
    try:
        from config import get_config

        return bool(getattr(get_config(), "BOOK_CAP_ENFORCEMENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — a config failure must never crash the PM
        import logging

        logging.warning(
            "Failed to read BOOK_CAP_ENFORCEMENT_ENABLED config, falling back to False",
            exc_info=True,
        )
        return False


@dataclass
class PositionScore:
    """Comprehensive scoring for a position's worthiness to be held"""

    symbol: str
    qty: float
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float

    # Scoring components (0-100 each)
    momentum_score: float = 50.0  # Price trend strength
    conviction_score: float = 50.0  # Original trade conviction
    risk_adjusted_score: float = 50.0  # Return vs volatility
    holding_period_score: float = 50.0  # Time-based (avoid churning)

    # Final composite score
    total_score: float = 50.0

    # Metadata
    days_held: int = 0
    # #2935: `days_held` ist ein int aus `.days` — 0 heisst SOWOHL "heute gekauft" ALS AUCH
    # "Alter unbekannt" (keine Handelshistorie). Der Verdraengungs-Riegel darf nur den zweiten
    # Fall fail-open behandeln, sonst rutschen ausgerechnet die frischesten Positionen durch
    # (Live-Karussell 17./18.08.). `age_known` trennt die beiden Faelle. Default False =
    # Alter unbekannt ⇒ jeder andere Erzeuger von PositionScore verhaelt sich byte-identisch.
    age_known: bool = False
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OpportunityScore:
    """Scoring for a potential new position"""

    symbol: str
    current_price: float

    # Signal components
    rl_action: int = 0  # 0=HOLD, 1=BUY, 2=SELL
    model_confidence: float = 0.0

    # Technical scores (0-100)
    momentum_score: float = 50.0
    value_score: float = 50.0  # RSI oversold = high value
    trend_score: float = 50.0  # ADX strength

    # Final composite
    total_score: float = 50.0

    # Debate results
    arguments_for: List[str] = field(default_factory=list)
    arguments_against: List[str] = field(default_factory=list)
    debate_conclusion: str = ""

    # #3619: the SIZE inputs the sizer will see for this name (HAR-RV / IV forward vol,
    # 25-delta skew percentile, directional-vote coverage). None = not known at this
    # call site (=> the plain 1/N target). The top-up dead-band derives the SAME
    # clean-weight target from them that calculate_position_size uses, so a position
    # stops building at the vol-scaled target instead of the conviction target.
    forecast_vol: Optional[float] = None
    skew_percentile: Optional[float] = None
    vote_coverage: Optional[float] = None
