"""#3180 Consensus Retention Gate — pure decision helper (plan #3189, Archon [APPROVE]).

Given a HELD symbol's OPINION exit (rotation / book-overflow / take-profit / momentum-fade /
model-signal), decide whether to SUPPRESS it because the live round-table blend-consensus still
rates the name highly (retention). The design is deliberately minimal and safe:

* **RISK exits are NEVER gated.** Hard stop-loss / trailing / panic (`ExitAnalysis.tier == 'risk'`,
  or any dispatch tagged `exit_kind == 'risk'`) fire regardless of consensus. Belt-and-suspenders:
  this helper is only ever called from OPINION seams, and it hard-guards the risk case anyway.
* **Default OFF ⇒ byte-identical.** `CONSENSUS_RETENTION_THRESHOLD <= 0` (default 0.0) → the gate
  never vetoes, so a build with the flag at its default is bit-for-bit the pre-#3180 behaviour.
* **Fail-open on any uncertainty.** Missing/None/garbage consensus, a config read error, or a PM
  without the accessor → return False (no veto). A missing signal must never suppress an exit.
* **Output ⊆ baseline.** The gate can only REMOVE an opinion sell, never create or reorder one.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _threshold() -> float:
    """The live gate threshold; 0.0 (OFF) on any config-read problem — never raise into trading."""
    try:
        from config import get_config

        return float(getattr(get_config(), "CONSENSUS_RETENTION_THRESHOLD", 0.0) or 0.0)
    except Exception:  # noqa: BLE001 — a config read must never break an exit decision
        logger.warning(
            "Failed to read CONSENSUS_RETENTION_THRESHOLD config", exc_info=True
        )
        return 0.0


def consensus_retention_veto(symbol: str, exit_kind: str, pm: Any) -> bool:
    """Return True to SUPPRESS an OPINION exit for ``symbol`` (retention), else False.

    ``exit_kind`` is the classified reason (e.g. 'rotation', 'model', 'momentum_fade', or 'risk').
    ``pm`` is the PortfolioManager exposing ``get_live_consensus(symbol) -> Optional[float]``.
    """
    # Belt-and-suspenders: a risk exit is never gate-eligible (the caller already filters, but a
    # future miswire must fail SAFE — hold-a-crash is catastrophic, missing a veto is benign).
    if exit_kind == "risk":
        return False

    threshold = _threshold()
    if threshold <= 0.0:  # gate OFF ⇒ byte-identical
        return False

    getter = getattr(pm, "get_live_consensus", None)
    if getter is None:
        return False
    consensus = getter(symbol)
    if consensus is None:  # fail-open: no live consensus ⇒ never suppress
        return False
    try:
        veto = float(consensus) > threshold
    except (TypeError, ValueError):
        return False

    if veto:
        logger.info(
            "[ConsensusRetention] %s: OPINION exit (%s) SUPPRESSED — live consensus %.3f > %.3f",
            symbol,
            exit_kind,
            float(consensus),
            threshold,
        )
    return veto
