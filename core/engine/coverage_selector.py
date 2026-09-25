"""Pure, side-effect-free selector for dynamic specialist-report coverage (Epic #1998 RPT-GOLD, sub #2630).

High-priority target = base_watchlist U held positions U top-N symbols by round-table consensus_score.
No I/O, no engine state — the loop owns the registry mutation. Mirrors the seam style of
core/engine/closed_market_mode.py. Bounded and deterministic so a cycle never bursts a 500-symbol sweep.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _norm(sym: str) -> str:
    return sym.upper().strip()


def select_specialist_coverage(
    position_symbols: List[str],
    round_table_scores: List[Dict[str, Any]],
    base_watchlist: List[str],
    top_n: int,
    cover_positions: bool = True,
) -> List[str]:
    """Return the deduped, upper-normalised high-priority symbol list.

    Composition = base_watchlist U (positions if cover_positions) U top-N by consensus_score.
    Deterministic order: base, then positions, then top-N. Entries missing ``symbol`` or a
    numeric ``consensus_score`` are skipped (never crash the caller).
    """
    ordered: List[str] = []
    seen: set[str] = set()

    def _add(sym: Any) -> None:
        if not isinstance(sym, str) or not sym.strip():
            return
        s = _norm(sym)
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    for s in base_watchlist or []:
        _add(s)
    if cover_positions:
        for s in position_symbols or []:
            _add(s)

    ranked = [
        e
        for e in (round_table_scores or [])
        if isinstance(e, dict)
        and isinstance(e.get("symbol"), str)
        and isinstance(e.get("consensus_score"), (int, float))
    ]
    ranked.sort(key=lambda e: e["consensus_score"], reverse=True)
    for e in ranked[: max(0, int(top_n))]:
        _add(e["symbol"])

    return ordered
