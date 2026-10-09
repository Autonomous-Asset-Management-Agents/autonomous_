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

"""Signalbau des Round Table: Konsens-Score → ``SignalEvent`` mit MiFID-``DecisionContext``.

#4277 (H-4d), Teil von ARC-E6 (#3738): ``REASONING_TRACE_MAX_CHARS`` (samt #3251-Block),
``_position_fields_from_state`` und ``_score_to_signal`` wortgleich aus
``core/round_table/runner.py`` hierher umgezogen (Schnitt-Entscheidung #4186 §2, §5 „H-4d“).
Der Kern importiert ``_score_to_signal`` zurück (Phase 4) und führt
``_position_fields_from_state`` wieder aus. Dieses Modul importiert den Kern nicht
(rückimport-frei, §3); Tests patchen die Schwellen deshalb hier, an
``core.round_table.signal_bau`` (Weg (c)).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from core.round_table.consensus import SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD

if TYPE_CHECKING:
    from core.orchestration.graph import SymbolEvalState

# Entscheidung #4186 §2: dasselbe Logger-Objekt wie der Kern (H-4b Regel 4).
logger = logging.getLogger("core.round_table.runner")

# #3251 (MiFID-Audit-Integrität): the reasoning_summary is a SHORT human digest
# (top-3 agents, [:200]). It was ALSO inherited as reasoning_trace (cloud_logger:
# `if not reasoning_trace: reasoning_trace = reasoning_summary`), so only ~2 of the
# voting agents' reasonings ever reached the WORM decision record — the rest of the
# pre-trade rationale was silently lost. reasoning_trace now carries EVERY voting
# agent's reasoning, untruncated up to this generous documented cap. The DB column
# is TEXT (unbounded); this cap only guards against pathological LLM-output bloat in
# the audit record — it never cuts below the agent count in practice (~9 agents ×
# ~200 chars ≈ 1.8k << 8000).
REASONING_TRACE_MAX_CHARS = 8000


def _position_fields_from_state(state) -> tuple:
    """#2672: map the five position-context channels into DecisionContext kwargs.

    Returns ``(fields, reasoning_prefix)``:
    - all channels None (flag OFF / dark ship) → ``({}, "")`` — the context keeps
      today's defaults, byte-identical;
    - confirmed book → real ``in_position``/``position_qty``/``position_avg_price``/
      ``unrealized_pnl`` + ``position_context_confirmed=True``;
    - UNCONFIRMED book (broker outage) → neutral defaults + ``position_context_confirmed=False``
      and the self-describing audit marker ``"[position context UNCONFIRMED] "`` as
      reasoning prefix (fail-closed: the record must never silently claim "flat").
    """
    confirmed = state.get("position_context_confirmed")
    if confirmed is None:
        return {}, ""
    if confirmed is False:
        return {"position_context_confirmed": False}, "[position context UNCONFIRMED] "
    fields = {
        "in_position": bool(state.get("in_position") or False),
        "position_qty": float(state.get("position_qty") or 0.0),
        "position_avg_price": float(state.get("position_avg_price") or 0.0),
        "unrealized_pnl": float(state.get("unrealized_pnl") or 0.0),
        "position_context_confirmed": True,
    }
    return fields, ""


def _aktion_aus_schwellen(score: float) -> str:
    """H-4e Schritt 1: BUY/SELL/HOLD aus dem Konsens-Score.

    Die Schwellen werden zur Laufzeit als Modulglobale gelesen (nicht als Default-Argument
    gebunden), damit ein Patch auf ``signal_bau`` Aktion und Dämpfungs-Tor gleichermaßen trifft.
    """
    # Use imported thresholds (ADR-SEC-01: single source of truth in consensus.py)
    if score > SIGNAL_BUY_THRESHOLD:
        return "BUY"
    if score < SIGNAL_SELL_THRESHOLD:
        return "SELL"
    return "HOLD"


def _begruendungen(votes: list) -> tuple:
    """H-4e Schritt 2: ``(reasoning, full_reasoning_trace)`` — Kurzdigest und volle Spur (#3251)."""
    # Reasoning aus Top-3 Agents (nach Gewicht) — kurzer Digest für reasoning_summary.
    top_votes = sorted(
        [v for v in votes if v is not None],
        key=lambda v: getattr(v, "weight", 0.0),
        reverse=True,
    )[:3]
    reasoning = " | ".join(
        v.reasoning for v in top_votes if not getattr(v, "vetoed", False)
    )

    # #3251 (MiFID-Audit-Integrität): the FULL per-agent reasoning trace — EVERY
    # voting agent (highest weight first), not the top-3/[:200] digest above.
    # Vetoed votes are kept AND marked (an auditor must see why a veto fired).
    # Set explicitly so cloud_logger no longer inherits the truncated summary
    # into reasoning_trace. Untruncated up to the documented safety cap.
    _all_votes = sorted(
        [v for v in votes if v is not None],
        key=lambda v: getattr(v, "weight", 0.0),
        reverse=True,
    )
    full_reasoning_trace = " | ".join(
        f"{v.agent_name}{' [VETO]' if getattr(v, 'vetoed', False) else ''}: {v.reasoning}"
        for v in _all_votes
        if getattr(v, "reasoning", None)
    )[:REASONING_TRACE_MAX_CHARS]
    return reasoning, full_reasoning_trace


def _drawdown_daempfung(
    state: "SymbolEvalState", score: float, action: str, votes: list
) -> tuple:
    """H-4e Schritt 3: ``(action, conviction, damp_note)`` nach der DrawdownGuard-Dämpfung."""
    # #1951 / plan #2205 (Hybrid B+C) — DrawdownGuard as a CONDITIONER on BUYs:
    # the DrawdownGuard's severity DAMPENS the conviction (→ position size), and
    # the DAMPED conviction gates the action: if it falls below the buy threshold
    # the BUY flips to HOLD (audited). Live forensics (#1951, 13.–22.07.2026)
    # proved the previous raw-score-only direction was cosmetic — 708 BUYs at
    # ~0.1x damped conviction still executed on risk_manager's 2% MIN_POSITION
    # floor. SELL/HOLD are never touched (risk-reducing SELLs must not freeze).
    # Default OFF ⇒ conviction == score (byte-identical old hard-veto posture).
    conviction = score
    damp_note = ""
    if action == "BUY":
        from config import get_config

        cfg = get_config()
        if getattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False):
            dg = next(
                (
                    v
                    for v in votes
                    if getattr(v, "agent_name", "") == "DrawdownGuardAgent"
                ),
                None,
            )
            if dg is None:
                # Archon R3: fail-OPEN (no damping — damping only ever REDUCES size,
                # so its absence never over-sizes) + WARNING, never DEBUG (§5.6).
                logger.warning(
                    "DrawdownGuard conditioner ON but no DrawdownGuardAgent vote for "
                    "%s — BUY conviction NOT dampened (fail-open).",
                    state.get("symbol"),
                )
            else:
                try:
                    damp_max = float(getattr(cfg, "DRAWDOWN_CONVICTION_DAMP_MAX", 0.8))
                except (TypeError, ValueError):
                    damp_max = 0.8
                severity = max(0.0, min(1.0, 1.0 - float(dg.score)))
                conviction = score * (1.0 - damp_max * severity)
                # Archon R2 (MiFID pre-trade transparency): preserve BOTH the raw
                # consensus AND the damped conviction in the audit trail — the raw
                # consensus is NEVER overwritten by the damped value.
                dd_pct = severity / 5.0 * 100.0  # approx; DG score clamps at ~20% DD
                if conviction < SIGNAL_BUY_THRESHOLD:
                    # #1951 conviction gate — the damped conviction is below the
                    # buy threshold: no trade. The raw consensus stays visible in
                    # the audit trail (§5.4 MiFID pre-trade transparency); only
                    # the action is downgraded, never the recorded scores.
                    action = "HOLD"
                    damp_note = (
                        f" (damped to {conviction:.3f} → HOLD: "
                        f"{state.get('symbol')} ~{dd_pct:.0f}% drawdown "
                        f"below buy gate)"
                    )
                else:
                    damp_note = (
                        f" (damped to {conviction:.3f} due to "
                        f"{state.get('symbol')} ~{dd_pct:.0f}% drawdown)"
                    )
    return action, conviction, damp_note


def _ta_felder(feats: dict) -> dict:
    """H-4e Schritt 4: die TA-Felder des ``DecisionContext`` aus den Feature-Skalaren (#2389).

    Ein fehlendes oder kaputtes Feature behält den Default des ``DecisionContext``.
    """

    def _feat(key: str, default: float) -> float:
        value = feats.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    return {
        "rsi_14": _feat("rsi_14", 50.0),
        # features.py exposes the MACD histogram (macd - signal line) — the
        # standard momentum reading; the raw lines are not computed there.
        "macd": _feat("macd_hist", 0.0),
        # bb_position ∈ ~[-1, +1] (lower..upper band) → %B ∈ ~[0, 1].
        "bb_pct": 0.5 + _feat("bb_position", 0.0) / 2.0,
        "volume_ratio": _feat("vol_anomaly", 1.0),
        "atr_14d": _feat("atr_14d", 0.0),
    }


def _forecast_vol(state: "SymbolEvalState") -> Optional[float]:
    """H-4e Schritt 5: ``forecast_vol`` aus HAR-RV, bei IV-Flag mit IV-Override (#1953, #3094)."""
    # #1953 (RC-2): plumb the HAR-RV forward-vol from the specialist scalars
    # (graph.py _attach_specialist_scalars -> state["ml"]) into the decision
    # record — the docking input for the flag-gated vol-targeting sizing.
    # state["ml"] is None whenever no registry/report/history exists; the
    # context then keeps its None default (fail-safe, byte-identical).
    _ml = state.get("ml")
    _forecast_vol = _ml.get("forecast_vol") if isinstance(_ml, dict) else None

    # #3094 (a): when IV drives the sizer, override the HAR-RV forecast_vol with
    # the VRP-de-biased per-name implied vol (annualized -> daily). Missing IV or
    # any error keeps the HAR-RV value — fail-safe, byte-identical when flag OFF.
    try:
        from config import get_config as _get_cfg

        _cfg_iv = _get_cfg()
        if getattr(_cfg_iv, "IMPLIED_VOL_FORECAST_ENABLED", False):
            from core.ml.vol_model import debiased_daily_vol_from_iv

            _iv_daily = debiased_daily_vol_from_iv(
                state.get("implied_vol"),
                vrp_factor=float(getattr(_cfg_iv, "IV_VRP_DEBIAS_FACTOR", 0.85)),
            )
            if _iv_daily is not None:
                _forecast_vol = _iv_daily
    except Exception:  # noqa: BLE001 — a sizing input must never break the cycle
        logger.exception("Fehler beim Laden oder Berechnen der IV Forecast Vol")
    return _forecast_vol


def _skew_perzentil(state: "SymbolEvalState") -> Optional[float]:
    """H-4e Schritt 6: das 25Δ-RR-Skew-Perzentil des Namens oder ``None`` (#3199)."""
    # #3199: per-name 25Δ RR skew percentile from the completed-cycle options
    # channels (the SAME producer the UpsideSkewAgent reads: risk_reversal +
    # risk_reversal_reference). The input to the flag-gated skew size tilt
    # (risk_manager.skew_size_tilt). Missing RR/reference => None => fail-open
    # no-op (byte-identical). A sizing input, never a gate: must not break the cycle.
    _skew_percentile = None
    try:
        from core.options_skew import rr_percentile as _rr_pct

        _rr = state.get("risk_reversal")
        _rr_ref = state.get("risk_reversal_reference")
        if _rr is not None and _rr_ref:
            _skew_percentile = _rr_pct(_rr, list(_rr_ref))
    except Exception:  # noqa: BLE001 — a sizing input must never break the cycle
        logger.exception("Fehler beim Berechnen des RR-Skew-Perzentils (#3199)")
    return _skew_percentile


def _regime_felder(state: "SymbolEvalState") -> tuple:
    """H-4e Schritt 7: ``(fields, vix_prefix)`` — VIX- und Regime-Felder (#2958, #2980)."""
    # #2958: the regime conditioner's declared channels carry the REAL
    # volatility reading (trading_loop._regime_conditioner_state_keys).
    # Without them every audit row froze the 20.0/"normal" model defaults
    # and the executor fed that constant into the ADR-R08 sizing ladder
    # (vix_risk_scaler pinned to 0.9). None/absent/garbage channels keep
    # the defaults — a logging gap must never cost the trade signal.
    # #2980 (ADR-R09): resolve VIX through the central fail-closed helper so
    # the WORM audit never fabricates an observed "calm 20.0" when the gauge
    # was blind. Confirmed → the real value lands + vix_confirmed=True.
    # Missing/invalid → vix_level keeps its neutral default (byte-identical to
    # #2958) BUT vix_confirmed=False and the "[VIX UNCONFIRMED]" marker are
    # stamped, so an auditor can tell the 20.0 was substituted, not observed.
    from core.risk_manager import VIX_UNCONFIRMED_MARKER, resolve_vix

    _regime_fields = {}
    _vix_value, _vix_confirmed, _ = resolve_vix({"vix": state.get("vix")})
    _vix_prefix = ""
    if _vix_confirmed:
        _regime_fields["vix_level"] = _vix_value
        _regime_fields["vix_confirmed"] = True
    else:
        _regime_fields["vix_confirmed"] = False
        _vix_prefix = VIX_UNCONFIRMED_MARKER
    _state_regime = state.get("regime")
    if isinstance(_state_regime, str) and _state_regime:
        _regime_fields["market_regime"] = _state_regime
    return _regime_fields, _vix_prefix


def _score_to_signal(
    state: "SymbolEvalState",
    score: float,
    votes: list,
) -> Optional[object]:
    """
    Konvertiert den Konsens-Score in ein Signal-Event-ähnliches Objekt.
    Verwendet dasselbe Interface wie SignalEvent aus core/events.py.

    Thresholds:
        score > 0.65 → BUY
        score < 0.35 → SELL
        else         → HOLD

    #4277 (H-4e): ruft die benannten Schritte in der Reihenfolge des früheren Rumpfs und
    baut nur noch ``DecisionContext`` und ``SignalEvent``. Jeder Schritt läuft im äußeren
    ``try`` — ein Fehler irgendwo liefert wie bisher ``None`` (kein Signal).
    """
    try:
        from core.events import SignalEvent

        action = _aktion_aus_schwellen(score)
        reasoning, full_reasoning_trace = _begruendungen(votes)
        action, conviction, damp_note = _drawdown_daempfung(state, score, action, votes)

        curr_price = state.get("ohlc", {}).get("close", 0.0)
        from core.cloud_logger import DecisionContext

        # #2389: real TA snapshot for the MiFID II Art. 25 decision record. The
        # flag-gated _compute_features_node (graph.py) puts the last-row TA SCALARS
        # into state["features"]; missing/partial features (flag off, no history,
        # node fail-open) keep today's DecisionContext defaults — field-by-field.
        feats = state.get("features") or {}

        _forecast_vol_wert = _forecast_vol(state)
        _skew_percentile = _skew_perzentil(state)

        # #2672: flag-gated position context from the declared state channels —
        # {} + "" while the producer ships dark (all-None channels) = today's
        # defaults, byte-identical. UNCONFIRMED book → audit marker prefix.
        _position_fields, _position_prefix = _position_fields_from_state(state)

        _regime_fields, _vix_prefix = _regime_felder(state)

        ctx = DecisionContext(
            symbol=state["symbol"],
            action=action,
            conviction_score=conviction,
            current_price=curr_price,
            **_ta_felder(feats),
            forecast_vol=_forecast_vol_wert,
            skew_percentile=_skew_percentile,
            # #3618 audit: applied risk-off factor and the undamped consensus.
            regime_damp_factor=((state.get("regime_conditioning") or {}).get("factor")),
            consensus_undamped=(
                (state.get("regime_conditioning") or {}).get("undamped_consensus")
            ),
            reasoning_summary=(
                f"{_vix_prefix}{_position_prefix}RoundTableV2 "
                f"consensus={score:.3f}{damp_note}: {reasoning[:200]}"
            ),
            # #3251: full multi-agent trace (empty → None so cloud_logger keeps its
            # summary fallback, byte-identical when no vote carried a reasoning).
            reasoning_trace=full_reasoning_trace or None,
            **_position_fields,
            **_regime_fields,
        )
        return SignalEvent(
            symbol=state["symbol"],
            action=action,
            decision_context=ctx,
        )
    except Exception as exc:
        logger.warning(
            "run_round_table: Signal-Erstellung fehlgeschlagen: %s", exc, exc_info=True
        )
        return None
