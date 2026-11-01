# core/round_table/agenten/vix_aware.py
# #4085 (ARC-E6 G-6c): VIXAwareRiskAgent samt IV-Gate, Perzentil-Rang und Gewicht, unveraendert aus core/round_table/agents.py umgezogen.
# agents.py importiert jeden Namen zurueck (neuladetreu ueber _frisch).
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Optional

import config
from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agenten._basis import (
    _agent_enabled,
    _consensus_weight,
    _disabled_abstain,
)
from core.round_table.base_agent import VotingAgent

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Bewusst der Loggername von agents.py (wie _basis.py): Die Warnungen erscheinen nach dem
# Umzug unter demselben Namen wie vorher (verhaltensneutral).
logger = logging.getLogger("core.round_table.agents")


# ---------------------------------------------------------------------------
# 5. VIXAwareRiskAgent (w:0.45) — die erwartete Schwankung des Titels
# ---------------------------------------------------------------------------
#
# #3038 (TRD-10) removed the last remnants of the original volume-as-stress-proxy
# idea (`_NORMAL_VOLUME_REF`, `_VIX_VOLUME_THRESHOLD` — dead constants, and a
# heading that had claimed "Volume-Inverse" long after the input became the VIX).
# Measured, the old idea does not carry: per-symbol volume against excess return is
# r = +0.040 (10 d) and +0.002 (20 d).


def _vixaware_implied_vol() -> bool:
    """#3038 (TRD-10): config-gated per-symbol input for VIXAwareRiskAgent.

    Measured over 29,395 live decisions the agent's score spans 0.754..0.773, is
    identical for every symbol on 17 of 23 days, and is **bit-identical to
    RegimeDetectionAgent in 26,755 of 26,755 cases** — same sigmoid (`:891` vs
    `:577`), same market-wide input. The consensus compares the symbols of one day
    against each other, so a quantity equal for all of them separates nobody; it
    only shifts the level, and blocks 0.3 % of decisions while carrying 22 % weight.

    ON substitutes the symbol's own ATM-30d implied volatility, ranked against the
    PREVIOUS day's cross-section. Evidence: 71,559 symbol-days, IV against maximum
    drawdown r = -0.330, negative in ten of ten quarters, with an accelerating
    decile profile (research/iv_study.py). The criterion is DRAWDOWN, not return —
    the return correlation flips sign between periods, which is why an earlier,
    return-based version of this finding was discarded (VISION_AND_GOALS §3).

    **Seit #3044 ist der Flag default AN** (``config.py``: ``VIXAWARE_IMPLIED_VOL_ENABLED``
    default ``"true"``) — der per-Symbol-IV-Pfad ist damit das Live-Verhalten; der marktweite
    VIX-Zweig (und dessen bit-identische Deckung mit RegimeDetection) ist nur noch der
    Fallback, falls der Flag explizit aus ist.

    Read through ``config.get_config()`` on every call: the single config seam of
    the finance core (CODING_POLICY §2.10), and the only form a sweep can flip
    between two runs of the same process.
    """
    try:
        return bool(getattr(config.get_config(), "VIXAWARE_IMPLIED_VOL_ENABLED", False))
    except Exception:  # pragma: no cover - config must never break the vote path
        return False


def _iv_percentile(value: float, reference: list) -> Optional[float]:
    """Rank ``value`` inside ``reference`` — strictly monotone, no ties, in (0,1).

    Why not a plain step-function percentile: with a fixed reference, two symbols
    falling between the same two reference points would receive the SAME rank. That
    is precisely the defect #3004 fixed for momentum (48.7 % of votes carried an
    identical 1.00), just moved to a different agent. AC-3 pins it.

    Inside the reference range: linear interpolation over mid-rank plotting
    positions ``(below + 0.5*equal) / n``. Mid-rank rather than ``i/(n-1)`` so the
    extremes map to ``0.5/n`` and ``1 - 0.5/n`` instead of hard 0.0 and 1.0 — the
    saturation the sim criterion caps at 2 %.

    Outside it: an algebraic tail rather than a clamp. A volatility jump is exactly
    the case where several symbols sit above everything yesterday knew. If they all
    snapped to the boundary, the voice would separate least in stress — when it
    matters most. The tail is continuous at the boundary, strictly monotone, and
    stays inside (0,1) for every finite input.

    Algebraic and not exponential for a concrete reason: ``exp(-t)`` underflows to
    0.0 in float64 around t = 745, and the first version of this function did snap
    to exactly 1.0 for an IV of 1.20 against a reference of 0.10..0.60 — the very
    tie AC-3 forbids, reintroduced by rounding. ``1/(1+t)`` decays slowly enough to
    stay representable out to t ≈ 1e16.

    Returns None when the reference cannot support a rank (fewer than two distinct
    values) — the caller abstains rather than guessing.
    """
    werte = sorted(
        float(v) for v in reference if v is not None and math.isfinite(float(v))
    )
    n = len(werte)
    if n < 2:
        return None

    # Mid-rank plotting positions over the DISTINCT values. Building them over
    # distinct values keeps the interpolation strictly increasing even when the
    # reference carries repeated quotes.
    einzeln: list = []
    stellen: list = []
    i = 0
    while i < n:
        j = i
        while j < n and werte[j] == werte[i]:
            j += 1
        einzeln.append(werte[i])
        stellen.append((i + 0.5 * (j - i)) / n)
        i = j
    if len(einzeln) < 2:
        return None

    lo, hi = einzeln[0], einzeln[-1]
    if lo <= value <= hi:
        for k in range(1, len(einzeln)):
            if value <= einzeln[k]:
                x0, x1 = einzeln[k - 1], einzeln[k]
                p0, p1 = stellen[k - 1], stellen[k]
                if x1 == x0:
                    return p1
                return p0 + (p1 - p0) * (value - x0) / (x1 - x0)
        return stellen[-1]

    # Tail scale: the average spacing of the reference. Small enough that a genuine
    # outlier is recognisably outside, large enough that the tail does not collapse
    # to the boundary within one step.
    spanne = max((hi - lo) / max(len(einzeln) - 1, 1), 1e-9)
    if value < lo:
        return stellen[0] / (1.0 + (lo - value) / spanne)
    return 1.0 - (1.0 - stellen[-1]) / (1.0 + (value - hi) / spanne)


# Resolved once at import, immediately before the class that consumes it.
_VIX_RISK_WEIGHT = _consensus_weight("VIX_RISK_WEIGHT", 0.45)


class VIXAwareRiskAgent(VotingAgent):
    """Bringt das Risiko ein, das die richtungsschätzenden Stimmen nicht sehen.

    Momentum, LSTM und NewsSentiment schätzen die *Richtung*; diese Stimme steuert die
    *Schwankung* bei — als Gegengewicht zur Vorrangregel „Kapitalerhalt vor
    kurzfristigem und risikobehaftetem Gewinn" (VISION_AND_GOALS §3).

    ⚠️ **ROLLE (seit #3094/#3115, Default):** Bei ``IMPLIED_VOL_FORECAST_ENABLED`` (default
    ``True``) ist diese Stimme über ``consensus_exclusions()`` **aus dem direktionalen
    Konsens-Mittel AUSGESCHLOSSEN** (per Name, nicht über das Gewicht) — sie ist ein
    **SIZING-Input** (per-Titel-IV → ``forecast_vol`` im Vol-Targeting-Sizer #1953), **kein
    Richtungs-Votum**. Sie zählt nur dann wieder im Richtungs-Mittel, wenn der Flag aus ist.

    Zwei Eingänge für den (Sizing-)Score, per Flag ``VIXAWARE_IMPLIED_VOL_ENABLED`` (#3038/#3044):

    * **AN (Default, seit #3044 — ``VIXAWARE_IMPLIED_VOL_ENABLED`` default True):** die
      implizite Volatilität des TITELS, als Rang gegen die Verteilung des Vortages:
      ``score = 1 − Perzentil``. Ruhig heißt hoher Score. **Per-Symbol, cross-sektional
      trennend** — NICHT marktweit.
    * **AUS (Fallback, nur wenn Flag off):** marktweiter VIX durch eine Sigmoid um 25 —
      für alle Titel eines Tages derselbe Wert. **Nur in diesem Fallback** deckt es sich
      mit RegimeDetectionAgent; die historische „26.755/26.755 bit-identisch"-Messung
      galt diesem alten Default, NICHT dem heutigen IV-Pfad.

    Fehlt die Datengrundlage, wird **enthalten** statt geraten — Gewicht 0.0 direkt
    im VoteResult, nicht über ``self.weight`` (``min_weight = 0.10`` würde sonst
    greifen, siehe ``vote``).
    """

    default_weight: float = (
        _VIX_RISK_WEIGHT  # #2815: config-gated, unset == historical literal
    )
    min_weight: float = 0.10
    max_weight: float = 1.50

    def _abstain(self, symbol: str, grund: str) -> SignalCandidate:
        """Enthaltung mit Gewicht 0.0 **direkt im VoteResult**.

        Nicht über ``self.weight``: ``base_agent.py:104`` zieht jedes Gewicht auf
        ``[min_weight, max_weight]`` hoch, und ``min_weight`` ist hier 0.10. Eine
        Stimme ohne Datengrundlage bekäme damit rund 6 % Gewicht im Konsens — eine
        geratene Stimme, die im Log wie eine echte aussieht. Die Klemme ist eine
        Sicherung gegen Redis-Manipulation und kann nicht unterscheiden, ob die Null
        vom Angreifer oder vom Agenten kommt; deshalb der Weg am Gewicht vorbei.

        Der Grund steht im Reasoning, weil im Log sonst nicht unterscheidbar ist, ob
        die Optionskette oder die Referenzverteilung gefehlt hat (AC-8).
        """
        logger.warning("VIXAwareRiskAgent enthält sich für %s: %s", symbol, grund)
        return SignalCandidate(
            agent_name="VIXAwareRiskAgent",
            symbol=symbol,
            score=None,
            weight=0.0,
            abstain_reason=AbstainReason.NO_DATA,
            reasoning=f"EXCLUDED — {grund}",
        )

    async def vote(self, state: "SymbolEvalState") -> SignalCandidate:
        symbol = state["symbol"]

        # #3154: per-Agent Enable-Gate (Option B) — vor jeder Arbeit.
        if not _agent_enabled("VIX_RISK_AGENT_ENABLED"):
            return _disabled_abstain("VIXAwareRiskAgent", symbol)

        if _vixaware_implied_vol():
            return self._vote_implied_vol(state, symbol)

        vix = state.get("vix")
        if vix is None:
            vix = state.get("ohlc", {}).get("vix")

        if vix is None or float(vix) <= 0:
            logger.warning(
                "VIXAwareRiskAgent inaktiv: VIX-Wert fehlt (oder <= 0). "
                "Agent abstiniert vom Konsens."
            )
            return SignalCandidate(
                agent_name="VIXAwareRiskAgent",
                symbol=symbol,
                score=None,
                weight=0.0,
                abstain_reason=AbstainReason.NO_DATA,
                reasoning="EXCLUDED — VIX volatility data not available right now",
            )

        vix_val = float(vix)
        # Continuous risk score: Normal VIX (15) -> ~0.73, Panic VIX (45) -> ~0.12
        score = self._clamp(1.0 / (1.0 + math.exp((vix_val - 25.0) / 10.0)))

        reasoning = (
            f"Market-wide fear gauge (VIX) at {vix_val:.1f} — lower means calmer "
            f"markets (score {score:.3f}). This reads the whole market's risk mood, "
            f"identical for every symbol today — not this stock. "
            f"[src: VIX quote]"
        )
        return SignalCandidate(
            agent_name="VIXAwareRiskAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )

    def _vote_implied_vol(
        self, state: "SymbolEvalState", symbol: str
    ) -> SignalCandidate:
        """#3038: Rang der erwarteten Schwankung gegen die Verteilung des Vortages.

        Ausschließlich aus abgeschlossenen Zyklen (AC-7): gelesen wird nur
        ``implied_vol_reference``, das der Erzeuger aus dem Vortag füllt. Werte des
        laufenden Zyklus rührt der Agent nicht an — sonst bewertete er einen Titel
        gegen eine Verteilung, die ihn selbst schon enthält, und der Sim zeigte
        einen Vorteil, den es live nicht gibt.
        """
        iv = state.get("implied_vol")
        if iv is None:
            return self._abstain(symbol, "no implied volatility for this symbol today")
        iv = float(iv)
        if not math.isfinite(iv) or iv <= 0:
            return self._abstain(symbol, f"implied volatility not usable ({iv!r})")

        referenz = state.get("implied_vol_reference")
        if not referenz or len(referenz) < 2:
            return self._abstain(
                symbol, "no reference distribution from a completed cycle yet"
            )

        perzentil = _iv_percentile(iv, list(referenz))
        if perzentil is None:
            return self._abstain(
                symbol, "reference distribution carries no distinct values"
            )

        score = self._clamp(1.0 - perzentil)
        datum = state.get("implied_vol_reference_date") or "unknown"
        reasoning = (
            f"Expected swing for this stock (30-day implied volatility) at "
            f"{iv:.4f} — rank {perzentil:.3f} against the {len(referenz)} symbols "
            f"of the previous session ({datum}), so calmer than "
            f"{100.0 * (1.0 - perzentil):.0f} % of them (score {score:.3f}). "
            f"Unlike the market-wide VIX this is about THIS stock. "
            f"[src: ATM-30d implied volatility, reference {datum}]"
        )
        return SignalCandidate(
            agent_name="VIXAwareRiskAgent",
            symbol=symbol,
            score=score,
            weight=self.weight,
            abstain_reason=AbstainReason.NO_DATA if score is None else None,
            reasoning=reasoning,
        )
