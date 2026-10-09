# core/round_table/agenten/_fundamentaldaten.py
# #4086 (ARC-E6 G-6d): die Fundamentaldaten-Helfer, die FundamentalsAgent und ValuationAgent
# teilen, unveraendert aus core/round_table/agents.py umgezogen. agents.py importiert jeden
# Namen zurueck. Beide Agenten rufen die Helfer ueber das Modulobjekt
# (``from core.round_table.agenten import _fundamentaldaten as _fd`` → ``_fd._read_pit_fundamentals``),
# damit ein Patch auf ``core.round_table.agenten._fundamentaldaten._read_pit_fundamentals``
# beide erreicht.
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
from typing import Optional

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


# ---------------------------------------------------------------------------
# Fundamentaldaten-Stimmen (Phase B) — die Lücke im Gremium.
#
# Der Round Table urteilte bisher ausschliesslich auf Kurs-/ML-/Stimmungs-Signalen:
# KEIN Modul sah je eine Bilanz. Die Daten liegen laengst vor — der auditierbare
# Report (core/report) baut auf genau diesem PIT-Fundamentals-Cache auf — sie
# erreichten das Gremium nur nie. Diese beiden Module schliessen die Luecke und
# lesen dieselbe Quelle wie der Report, damit Report und Entscheidung nie
# auseinanderlaufen koennen.
#
# DORMANT: default_weight = 0.0 -> sie stimmen mit und begruenden sichtbar
# (RoundTableView/Audit-Trail), bewegen den Konsens aber nicht, bis ihr Gewicht
# nach einem Walk-forward-Beweis angehoben wird (validate-before-activate,
# ConsensusEngine zaehlt nur weight > 0). Kein neues Flag noetig.
# ---------------------------------------------------------------------------
def _read_pit_fundamentals(
    symbol: str, close: Optional[float] = None, as_of: Optional[str] = None
) -> Optional[dict]:
    """Read the SAME point-in-time fundamentals the auditable report is built on.

    ``close`` is the symbol's current price. It is NOT optional in practice: the feed
    derives ``pe`` (and ``ps``) from price ÷ per-share earnings, so without it every
    price-based ratio comes back None and any agent reading them abstains forever.
    Verified against the real reader — `CachedFundamentalsReader()` with no
    close_lookup returns pe=None; supplying {symbol: 180.0} returns pe=61.22.

    Blocking (file-backed cache) -> callers must use ``asyncio.to_thread``.
    Returns None on a cold cache or any read error: an honest gap, never a guess.
    """
    try:
        from datetime import datetime as _dt
        from datetime import timezone as _tz

        from core.report.financials_feed import CachedFundamentalsReader

        # #3145 (3): read fundamentals AS OF the evaluation date — state["current_time"]
        # during a backtest — NOT wall-clock now. Otherwise a walk-forward sees filings
        # that did not exist yet (look-ahead). Fall back to today when absent (live),
        # mirroring the graph/runner current_time handling.
        as_of_date = _dt.now(_tz.utc).date()
        if as_of:
            try:
                as_of_date = _dt.fromisoformat(str(as_of)).date()
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid as_of format '%s' for symbol %s, falling back to today",
                    as_of,
                    symbol,
                )
        lookup = {symbol: float(close)} if close and float(close) > 0 else None
        fund = CachedFundamentalsReader(close_lookup=lookup).get_fundamentals(
            symbol, as_of_date
        )
        return dict(fund) if fund else None
    except Exception as exc:  # noqa: BLE001 — a data gap must never break the vote
        logger.warning(
            "Fundamentals read failed for %s (%s) — agent abstains (weight 0).",
            symbol,
            exc,
        )
        return None


def _pct(fraction: Optional[float]) -> Optional[float]:
    """Feed fractions -> percent.

    ⚠ The feed emits ratios as FRACTIONS (`net_margin` = 0.5585, not 55.85), while
    every threshold below is stated in percent because that is how the numbers are
    read and reasoned about. Converting at the boundary keeps the ADR thresholds
    legible and stops a 100x unit error from silently inverting a verdict.
    """
    return None if fraction is None else float(fraction) * 100.0


def _debt_to_equity(fund: dict) -> Optional[float]:
    """Derive D/E — the feed does not emit it, but it emits both sides.

    Verified against the real reader: `total_liabilities` and `total_equity` are
    present (NVDA: 32.274e9 / 79.327e9 -> 0.41). Reading a `debt_to_equity` key
    instead would read None forever, because no such key exists.
    """
    liabilities = fund.get("total_liabilities")
    equity = fund.get("total_equity")
    if liabilities is None or not equity:
        return None
    try:
        return float(liabilities) / float(equity)
    except (TypeError, ZeroDivisionError):
        return None
