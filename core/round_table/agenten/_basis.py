# core/round_table/agenten/_basis.py
# #3831 (ARC-E6 G-6a): gemeinsamer Unterbau der Round-Table-Agenten, unveraendert aus
# core/round_table/agents.py umgezogen. agents.py importiert jeden Namen zurueck.
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate

# Bewusst der Loggername von agents.py: Die Fail-Closed-WARNING von _agent_enabled
# erscheint nach dem Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


# ---------------------------------------------------------------------------
# Custom Exceptions — Fail-Fast Architecture (Anti-Watermelon)
# ---------------------------------------------------------------------------


class DependencyLostException(RuntimeError):
    """
    Raised when a critical runtime dependency (e.g. RL Registry, LLM API)
    is unavailable. The trading_loop catches this and triggers the Kill Switch.
    """


class SuspectDataException(ValueError):
    """
    Raised when OHLC data fails sanity checks (e.g. flat candle H=L=O=C).
    The trading_loop catches this and triggers the Kill Switch.
    """


def _consensus_weight(name: str, default: float) -> float:
    """#2815 (TRD-8 T1): config-gated vote weight, clamped to [0, 1].

    Same shape and reasons as ``agents._specialist_alpha_weight``: the read goes through
    ``get_config()`` (CODING_POLICY §2.10 — no raw os.environ in the finance-core) and is
    resolved ONCE at import (the #1346 monkeypatch-race argument). The clamp is the safety
    half: a sweep typo like "7" must yield 1.0, never a 7x mega-vote; garbage falls back
    to the historical default (config._env_float already guards the parse). Unset env ⇒
    byte-identical to the pre-#2815 hardcoded literal — pinned by
    tests/unit/test_consensus_weight_seam.py.
    """
    try:
        return max(0.0, min(1.0, float(getattr(config.get_config(), name, default))))
    except (TypeError, ValueError):
        return default


def _agent_enabled(flag_name: str) -> bool:
    """#3154 (UXC-1 S1): parametrisierter Master-Gate-Helper für die per-Agent
    Enable-Flags der direktionalen Voter (Option B des Rev.-2-Dual-Designs —
    Hausmuster ``_upside_skew_enabled``, ein Helper statt fünf Kopien).

    Drei Zustände:
    - Flag ``true``/unset (getattr-Default) → Agent stimmt wie heute (byte-
      identischer Normalfall, kein Log).
    - Flag ``false`` → Aufrufer liefert einen Abstain-Record ("EXCLUDED — …").
    - ``get_config()`` wirft → **FAIL-CLOSED**: False (Abstain) + WARNING.

    Die Fail-Closed-Regel ist eine BEWUSSTE Abweichung vom Fail-open-Hausmuster
    (``_upside_skew_enabled``: Lesefehler ⇒ Default) — dort ist der Default
    ``false``/dark, hier wäre "Default zurückgeben" bei Default-``true``-Flags
    fail-open (Archon-Audit #3154: ein per Konfiguration deaktivierter Agent
    darf bei kaputtem Config-Read nicht still weiterstimmen). Abstain statt
    Exception, weil eine Exception im Runner nur als maskierter Modellausfall
    landet (runner.py return_exceptions-Pfad + MLWatchdog-Eskalation) — gleiche
    Konsens-Wirkung, schlechtere Observability. WARNING, nie DEBUG (§5.6).
    """
    try:
        return bool(getattr(config.get_config(), flag_name, True))
    except Exception:  # noqa: BLE001 — fail CLOSED: unreadable flag ⇒ abstain
        logger.warning(
            "Enable-Flag %s unlesbar — FAIL-CLOSED: Agent enthält sich diese "
            "Runde (Abstain statt Stimme).",
            flag_name,
            exc_info=True,
        )
        return False


def _disabled_abstain(agent_name: str, symbol: str) -> SignalCandidate:
    """#3154: Abstain-Record eines per Konfiguration deaktivierten Voters.
    weight 0.0 DIREKT auf dem SignalCandidate."""
    return SignalCandidate(
        agent_name=agent_name,
        symbol=symbol,
        score=None,
        weight=0.0,
        abstain_reason=AbstainReason.DISABLED,
        reasoning=f"EXCLUDED — {agent_name} deactivated by configuration",
    )


# INC (ML-gate outage): sentinel distinguishing "the runner shared no signal" (key
# ABSENT → each voice resolves + evaluates its own active, byte-identical to before)
# from "the shared eval genuinely produced None" (key PRESENT, value None → both voices
# abstain CONSISTENTLY). A bare ``None`` cannot carry that distinction; a unique object
# can. Written by runner._maybe_share_active_signal, read by both ML voices
# (agenten/lstm_signal.py, agenten/rl_confidence.py) — #4084 (G-6b): hier, damit beide
# dasselbe Objekt sehen.
_SHARED_UNSET = object()
