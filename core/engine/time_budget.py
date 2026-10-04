"""Zeitbudget der Entscheidung — Kursalter und die Staffelung der drei Timeouts (#3381).

Zwei reine Funktionen, bewusst ohne Zustand und ohne Konfigurationszugriff, damit sie
sich einzeln pruefen lassen und in Sim wie Live identisch arbeiten (BORA):

``is_stale_quote``
    Beantwortet die einzige Frage, die der Entscheidungspfad heute nicht stellen kann:
    *Wie alt ist der Preis, auf dem ich gleich entscheide?* Ein unbekanntes Alter gilt
    als zu alt — die konservative Richtung ist die Enthaltung, nicht der Handel.

``ordered_time_budget``
    Erzwingt ``Agent < Symbol < Zyklus``. Heute ist die Staffelung invertiert (Agent
    60,0 s in ``core/round_table/runner.py:164`` gegen Symbol 45,0 s in
    ``core/engine/trading_loop.py``), die innere Schicht gibt also nach der aeusseren
    auf und hinterlaesst einen abgebrochenen Teilzyklus. Korrigiert wird nicht still:
    jede Klemmung schreibt eine WARNING (CODING_POLICY §5.6).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Die Notstaffelung, wenn eine Grenze nicht-positiv (also unbrauchbar) geladen wurde.
# Bewusst identisch mit den Standardwerten in config.py / config.oss.py.
_FALLBACK_AGENT_S = 60.0
_FALLBACK_SYMBOL_S = 120.0
_FALLBACK_CYCLE_S = 1800.0


def _as_utc(value: datetime) -> datetime:
    """Naive Zeitstempel als UTC lesen.

    Alpaca liefert tz-aware, der Sim-Pfad (``core/sim/data_client.py:96``) kann naiv
    liefern. Ein ``TypeError`` beim Vergleich waere hier der schlechteste Ausgang: er
    wuerde eine Alterspruefung zum Absturz machen, die gerade den Schutz darstellt.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def is_stale_quote(quote_ts, now: datetime, max_age_seconds: float) -> bool:
    """True, wenn auf diesem Kurs nicht mehr entschieden werden darf.

    Args:
        quote_ts: Zeitstempel der Preisquelle (``latest_trade`` bzw. ``daily_bar``),
            ``None`` wenn keine Quelle ein Alter mitgefuehrt hat.
        now: Aktuelle Engine-Zeit — im Live-Pfad ``engine_now()``, damit Sim und Live
            auf derselben Uhr liegen (#3317).
        max_age_seconds: Altersgrenze. Nicht-positiv schaltet die Pruefung ab; das ist
            der Rollback-Weg ohne Revert (Plan #3381 §6).
    """
    if max_age_seconds <= 0:
        return False
    if quote_ts is None:
        # Alter unbekannt heisst nicht "frisch". Vor #3381 war genau dieser Zustand der
        # Normalfall — die Extraktion hat den Zeitstempel verworfen.
        return True
    try:
        age = (_as_utc(now) - _as_utc(quote_ts)).total_seconds()
    except (TypeError, ValueError, AttributeError):
        logger.warning(
            "is_stale_quote: Zeitstempel nicht vergleichbar (%r) → Enthaltung", quote_ts
        )
        return True
    return age > max_age_seconds


def ordered_time_budget(
    agent_seconds: float, symbol_seconds: float, cycle_seconds: float
) -> tuple[float, float, float]:
    """Gibt die drei Zeitgrenzen in gueltiger Staffelung zurueck.

    Anker ist die **innerste** Grenze: geklemmt wird nur nach aussen, nie nach innen.
    Ein Agent, der laenger arbeiten darf als sein Symbol, ist der heutige Defekt —
    einen Agenten kuerzer zu machen, um die Ordnung herzustellen, waere derselbe
    Defekt mit umgekehrtem Vorzeichen (Arbeit wird abgeschnitten, die heute laeuft).
    """
    agent_s = float(agent_seconds)
    symbol_s = float(symbol_seconds)
    cycle_s = float(cycle_seconds)

    if agent_s <= 0:
        logger.warning(
            "AGENT_VOTE_TIMEOUT_SECONDS=%s ist nicht positiv → %.1f s",
            agent_seconds,
            _FALLBACK_AGENT_S,
        )
        agent_s = _FALLBACK_AGENT_S

    if symbol_s <= 0:
        logger.warning(
            "SYMBOL_EVAL_TIMEOUT_SECONDS=%s ist nicht positiv → %.1f s",
            symbol_seconds,
            _FALLBACK_SYMBOL_S,
        )
        symbol_s = max(_FALLBACK_SYMBOL_S, agent_s * 2.0)
    elif symbol_s <= agent_s:
        symbol_s = agent_s * 2.0
        logger.warning(
            "SYMBOL_EVAL_TIMEOUT_SECONDS=%s liegt nicht ueber der Agenten-Grenze %.1f s "
            "— die innere Schicht wuerde nach der aeusseren aufgeben. Geklemmt auf %.1f s.",
            symbol_seconds,
            agent_s,
            symbol_s,
        )

    if cycle_s <= 0:
        logger.warning(
            "CYCLE_TIMEOUT_SECONDS=%s ist nicht positiv → %.1f s",
            cycle_seconds,
            _FALLBACK_CYCLE_S,
        )
        cycle_s = max(_FALLBACK_CYCLE_S, symbol_s * 2.0)
    elif cycle_s <= symbol_s:
        cycle_s = symbol_s * 2.0
        logger.warning(
            "CYCLE_TIMEOUT_SECONDS=%s liegt nicht ueber der Symbol-Grenze %.1f s. "
            "Geklemmt auf %.1f s.",
            cycle_seconds,
            symbol_s,
            cycle_s,
        )

    return agent_s, symbol_s, cycle_s


_BUDGET_CACHE: (
    "tuple[tuple[float, float, float], tuple[float, float, float]] | None"
) = None


def current_time_budget() -> tuple[float, float, float]:
    """Die geladene, geordnete Staffelung ``(agent, symbol, cycle)``.

    Einziger Lesepunkt fuer alle drei Schichten, damit sie nicht auseinanderlaufen
    koennen. Das Ergebnis wird gecacht, solange die Konfiguration unveraendert ist —
    andernfalls schriebe eine verdrehte Konfiguration pro Symbol eine WARNING und die
    Meldung, die auf ein Problem zeigt, wuerde selbst zum Rauschen.
    """
    global _BUDGET_CACHE
    from config import get_config

    cfg = get_config()
    raw = (
        float(getattr(cfg, "AGENT_VOTE_TIMEOUT_SECONDS", _FALLBACK_AGENT_S)),
        float(getattr(cfg, "SYMBOL_EVAL_TIMEOUT_SECONDS", _FALLBACK_SYMBOL_S)),
        float(getattr(cfg, "CYCLE_TIMEOUT_SECONDS", _FALLBACK_CYCLE_S)),
    )
    if _BUDGET_CACHE is None or _BUDGET_CACHE[0] != raw:
        _BUDGET_CACHE = (raw, ordered_time_budget(*raw))
    return _BUDGET_CACHE[1]
