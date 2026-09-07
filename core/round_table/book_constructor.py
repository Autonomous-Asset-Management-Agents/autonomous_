# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/round_table/book_constructor.py
# RTR-5/B1 (#2401, Epic RTR #1958) — Book-Constructor (DARK: flag default OFF).
#
# The Round Table as a SELECTOR: consume the cross-sectional ranking of the whole
# universe, build EXACTLY ONE Top-N target book, and compute the MINIMAL rebalance
# diff against the currently held positions — construction/diff logic ONLY.
#
# Evidence (#2401 §1): the only proven +55% (paper 100k→156k vs SPY +8.4%) came
# from selection + holding (6 concentrated picks, then 4 months of zero trades).
# The per-symbol ~30-min timing cadence produced 8017 signals / 11 micro-fills in
# 10 days with zero equity contribution. The comparative edge is cross-sectional
# RANKING (which names), not timing (when today).
#
# V-1 seams (verified 2026-07-22 against origin/main after the Phase-0/1 merges):
#   * PRIMARY  — LSTM cross-section: core/report/lstm_panel_store.py:156
#     ``cross_section_at`` on the process singleton (``get_store``, :222). Its only
#     writer is LSTMDynamic.update_lstm_rankings (lstm_strategy.py:484, gated on
#     REPORT_GENERATOR_V2_ENABLED; strategy-independent producer behind
#     LSTM_RANK_PANEL_STRATEGY_INDEPENDENT, trading_loop.py:543).
#   * FALLBACK — consensus scores: core/round_table/recent_decisions.py:117
#     ``get_recent_round_table_decisions`` (latest-per-symbol, entries carry
#     ``consensus_score``; single producer = runner.py:566 per round-table run).
#
# B1 boundaries (dark increment):
#   * NO order submission, NO broker/executor imports, NO loop wiring (that is B3).
#   * Flag OFF (default) -> ``maybe_construct_book`` returns None; nothing on the
#     trading path imports this module -> byte-identical behaviour (BORA).
#   * Anti-churn: sells are filtered through the caller-provided ``can_sell``
#     gate (PortfolioManager.can_sell_position, portfolio_manager.py:621 —
#     min-hold/consecutive-SELL bypass). Blocked sells are AUDITED, not silent.
#   * Top-N: N comes from ``portfolio_manager.max_positions`` (portfolio_manager
#     .py:106) at the call site — no separate N flag (flag minimalism, BORA).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First; WoW §2 TDD Red→Green.

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

RANKING_SOURCE_LSTM = "lstm_cross_section"
RANKING_SOURCE_CONSENSUS = "consensus_fallback"

_CanSell = Callable[[str], Tuple[bool, str]]


def _bc_config(name: str, default):
    """Edition-neutral config read (mirrors core/smart_exit.py:_smart_exit_config).

    Resolves on BOTH editions: config.py via the PEP-562 module ``__getattr__``
    proxy to ``RuntimeConfigState``; config.oss.py via plain module globals.
    """
    try:
        import config

        return getattr(config, name, default)
    except ImportError:
        return default


@dataclass(frozen=True)
class BookProposal:
    """ONE audited target-book proposal per rebalance window — a PROPOSAL record,
    never an order. Consumption (order construction, HITL, compliance gates) is
    B3; B1 stops at this datum by design."""

    as_of: str  # ISO-8601 construction time
    ranking_source: str  # RANKING_SOURCE_LSTM | RANKING_SOURCE_CONSENSUS
    universe_size: int  # symbols in the consumed ranking
    max_positions: int  # N (from portfolio_manager.max_positions)
    target_book: Tuple[str, ...]  # EXACTLY ONE Top-N book, rank order
    ranking: Tuple[Tuple[str, float], ...]  # full ranking, score desc (audit)
    buys: Tuple[str, ...] = ()  # in target, not held
    sells: Tuple[str, ...] = ()  # held, not in target, can_sell OK
    holds: Tuple[str, ...] = ()  # held and in target (no trade)
    blocked_sells: Tuple[Tuple[str, str], ...] = ()  # (symbol, anti-churn reason)
    reasoning: str = field(default="")  # human-readable audit reasoning

    def to_audit_record(self) -> Dict:
        """JSON-serializable audit record (lists instead of tuples)."""
        rec = asdict(self)
        rec["target_book"] = list(self.target_book)
        rec["ranking"] = [[s, sc] for s, sc in self.ranking]
        rec["buys"] = list(self.buys)
        rec["sells"] = list(self.sells)
        rec["holds"] = list(self.holds)
        rec["blocked_sells"] = [[s, r] for s, r in self.blocked_sells]
        return rec


def build_target_book(
    ranking: Mapping[str, float], max_positions: int
) -> Tuple[str, ...]:
    """EXACTLY ONE Top-N target book from a ``{symbol: score}`` ranking.

    Deterministic: score descending, alphabetical tie-break (sorted() is stable),
    so the same ranking always yields the same book (audit reproducibility). A
    universe smaller than N yields the whole ranked universe — never padded.
    """
    n = max(0, int(max_positions))
    ordered = sorted(ranking.items(), key=lambda kv: (-float(kv[1]), kv[0]))
    return tuple(sym for sym, _ in ordered[:n])


def construct_book(
    ranking: Mapping[str, float],
    held: Iterable[str],
    max_positions: int,
    can_sell: Optional[_CanSell] = None,
    as_of: Optional[datetime] = None,
    ranking_source: str = RANKING_SOURCE_LSTM,
) -> BookProposal:
    """Build the target book and the MINIMAL rebalance diff vs held positions.

    Minimal-diff invariant (plan §7 Gherkin): every involved symbol lands in
    EXACTLY ONE bucket (buys | sells | holds | blocked_sells) — at most one trade
    per symbol per rebalance window, and a held symbol that stays in the target
    book is never touched.

    Anti-churn: a held name outside the target book is only PROPOSED for sale if
    the ``can_sell`` gate (PortfolioManager.can_sell_position — min-hold /
    consecutive-SELL bypass) approves; a veto is recorded in ``blocked_sells``
    with its reason (audited, never silent). No gate provided -> sells pass
    (the caller owns the anti-churn policy; B3 wires the real PM gate).

    PURE construction: no order objects, no submission, no broker I/O.
    """
    now = as_of or datetime.now()
    held_syms = list(dict.fromkeys(str(s) for s in held))  # de-dup, keep order
    target = build_target_book(ranking, max_positions)
    target_set = set(target)

    holds = tuple(s for s in held_syms if s in target_set)
    buys = tuple(s for s in target if s not in held_syms)

    sells = []
    blocked = []
    for sym in held_syms:
        if sym in target_set:
            continue  # stays in the book — no trade (minimal diff)
        if can_sell is not None:
            ok, reason = can_sell(sym)
            if not ok:
                blocked.append((sym, str(reason)))
                continue
        sells.append(sym)

    full_ranking = tuple(
        sorted(
            ((str(s), float(v)) for s, v in ranking.items()),
            key=lambda kv: (-kv[1], kv[0]),
        )
    )
    reasoning = (
        f"Book-Constructor (#2401 B1): top-{len(target)} of {len(full_ranking)} "
        f"ranked names ({ranking_source}) -> target book {list(target)}; "
        f"diff vs {len(held_syms)} held: {len(buys)} buys, {len(sells)} sells, "
        f"{len(holds)} holds, {len(blocked)} sells blocked by anti-churn/min-hold."
    )
    return BookProposal(
        as_of=now.isoformat(),
        ranking_source=ranking_source,
        universe_size=len(full_ranking),
        max_positions=int(max_positions),
        target_book=target,
        ranking=full_ranking,
        buys=buys,
        sells=tuple(sells),
        holds=holds,
        blocked_sells=tuple(blocked),
        reasoning=reasoning,
    )


def gather_ranking(
    as_of: Optional[datetime] = None,
) -> Tuple[Dict[str, float], str]:
    """Collect the cross-sectional ranking from the verified V-1 seams.

    PRIMARY: the LSTM panel cross-section (lstm_panel_store.cross_section_at) —
    descendant of the 55% picker. FALLBACK (§5.6: WARNING, never silent): the
    latest-per-symbol Round-Table consensus scores (recent_decisions). Both reads
    are fail-safe copies off in-memory stores — no broker I/O, no decision-path
    mutation. Returns ``({}, source)`` when neither seam has data (caller must
    abstain, never guess — ZERO-GUESSING §5.0).
    """
    now = as_of or datetime.now()
    try:
        from core.report.lstm_panel_store import get_store

        xsec = get_store().cross_section_at(now)
    except Exception as exc:  # noqa: BLE001 — a ranking read must never raise
        logger.warning("book_constructor: LSTM panel read failed: %s", exc)
        xsec = {}
    if xsec:
        return {str(s): float(v) for s, v in xsec.items()}, RANKING_SOURCE_LSTM

    logger.warning(
        "book_constructor: LSTM cross-section empty -> consensus-score fallback "
        "(recent_decisions). Ranking quality is the round-table consensus, not "
        "the LSTM panel (§5.6 visible fallback)."
    )
    consensus: Dict[str, float] = {}
    try:
        from core.round_table.recent_decisions import get_recent_round_table_decisions

        for entry in get_recent_round_table_decisions():
            sym = str(entry.get("symbol") or "").strip().upper()
            score = entry.get("consensus_score")
            if sym and score is not None:
                try:
                    consensus[sym] = float(score)
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("book_constructor: consensus fallback read failed: %s", exc)
    if consensus:
        return consensus, RANKING_SOURCE_CONSENSUS

    logger.warning(
        "book_constructor: NO ranking source available (LSTM panel empty, no "
        "recent consensus decisions) — no target book this window (abstain)."
    )
    return {}, RANKING_SOURCE_CONSENSUS


def should_rebalance(
    last_rebalance_at: Optional[datetime],
    now: datetime,
    interval_hours: Optional[float] = None,
) -> bool:
    """Rebalance-cadence gate (ADR-RTR5-01, config.py): True when no rebalance has
    happened yet or the tunable interval (default 168h = weekly) has elapsed. The
    book is REBUILT only on this cadence — between windows the existing exits
    (smart_exit / stops) protect positions; ranking churn does not trade."""
    if last_rebalance_at is None:
        return True
    if interval_hours is None:
        interval_hours = float(
            _bc_config("BOOK_CONSTRUCTOR_REBALANCE_INTERVAL_HOURS", 168.0)
        )
    elapsed_h = (now - last_rebalance_at).total_seconds() / 3600.0
    return elapsed_h >= interval_hours


def is_book_constructor_enabled() -> bool:
    """Flag read at call time (BOOK_CONSTRUCTOR_ENABLED, default OFF = dark)."""
    return bool(_bc_config("BOOK_CONSTRUCTOR_ENABLED", False))


def maybe_construct_book(
    held: Iterable[str],
    max_positions: int,
    can_sell: Optional[_CanSell] = None,
    now: Optional[datetime] = None,
) -> Optional[BookProposal]:
    """Flag-gated entry point: None while BOOK_CONSTRUCTOR_ENABLED is OFF (dark,
    byte-identical), otherwise ONE audited proposal from the gathered ranking.
    Returns None as well when no ranking source has data (abstain, WARNING in
    gather_ranking). NOT wired into any loop in B1 — wiring/kadence/arm is B3."""
    if not is_book_constructor_enabled():
        return None
    moment = now or datetime.now()
    ranking, source = gather_ranking(as_of=moment)
    if not ranking:
        return None
    return construct_book(
        ranking=ranking,
        held=held,
        max_positions=max_positions,
        can_sell=can_sell,
        as_of=moment,
        ranking_source=source,
    )


__all__ = [
    "RANKING_SOURCE_LSTM",
    "RANKING_SOURCE_CONSENSUS",
    "BookProposal",
    "build_target_book",
    "construct_book",
    "gather_ranking",
    "should_rebalance",
    "is_book_constructor_enabled",
    "maybe_construct_book",
]
