# core/engine/ausstieg_hebel.py
# ARC-E6 G-2a (#3823) — die Hebel der Ausstiegsrunde, ausgezogen aus
# TradingLoopMixin._run_deconcentration_and_rotation_exits (core/engine/trading_loop.py).
"""Rotation (Rangverlust, Ueberhang) und Trim der Ausstiegsrunde.

Der Dirigent ``_run_deconcentration_and_rotation_exits``, die Vorbereitung
``_ausstieg_vorbereiten`` und der Rahmen der Rotation ``_rotation_ausstiege`` (Panel-
Frische, Deckel, Fehlerfang) bleiben in ``trading_loop.py``. Hierher gezogen sind die
drei Schritte, die Verkaeufe absenden. Keiner liest die Wanduhr, ruft eine mutierende
Broker-Methode oder einen der im Vertrag eingefrorenen ``getattr``-Rueckfallwerte des
Handelsschleifen-Moduls — dessen Vertragszeilen bleiben damit unberuehrt.

Warum ueberhaupt ein zweites Modul: Eine Extraktion in dieselbe Datei verlaengert sie
(Signaturen, Docstrings, Zustandszugriffe; gemessen 3205 -> 3297 Zeilen), die Dateizahl
im Vertrag darf aber nur sinken (``vertrag.toml``) — wie in G-1a und G-1b.

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau; jeder Schritt bindet am Anfang
die Namen, die er aus ``_AusstiegsLage`` und ``_Rotationsrunde`` liest.

Die Importrichtung ist erzwungen: ``trading_loop`` importiert dieses Modul, nie
umgekehrt. ``TradingLoopMixin`` erbt ``AusstiegsHebelMixin``, damit die Ausstiegsrunde
auch am blossen Mixin vollstaendig ist (Tests bauen es per ``__new__``).

Plan: ``docs/3823-*/implementation_plan.md``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from core.cloud_logger import DecisionContext
from core.events import SignalEvent


@dataclass(frozen=True)
class _AusstiegsLage:
    """#3823: was die Vorbereitung der Ausstiegsrunde beiden Hebeln uebergibt.

    Die Felder sind per AST gemessen: genau die Lokalen, die die Vorbereitung schreibt
    und Rotation oder Trim lesen (``already_acted``/``exited`` gehen als Argument).
    """

    cfg: Any
    now: datetime
    rotation_on: bool
    trim_on: bool
    strat: Any
    pm: Any
    consensus_retention_veto: Callable[[str, str, Any], bool]


@dataclass
class _Rotationsrunde:
    """#3823: was Rangverlust und Ueberhang der Rotation je Zyklus teilen.

    ``rot_exits`` zaehlt die Rangverlust-Ausstiege dieses Zyklus; der Ueberhang danach
    verbraucht nur den Rest des Zyklus-Deckels.
    """

    xsec: Any
    cross_section_standing: Callable
    top_n: int
    min_hold_days: float
    hysteresis: float
    rot_cap: int
    session_cap: int
    rot_exits: int = 0


class AusstiegsHebelMixin:
    """#3823: die Verkaufsschritte der Ausstiegsrunde (Rotation, Ueberhang, Trim)."""

    async def _rotation_rangverlust(
        self,
        lage: _AusstiegsLage,
        runde: _Rotationsrunde,
        already_acted: set,
        exited: set,
    ) -> None:
        """#3823 Lever A: wer aus den Top-N gefallen ist, wird ganz verkauft.

        Traegt in ``exited`` ein und zaehlt ``runde.rot_exits`` - der Ueberhang danach
        teilt dieses Budget. Fehler steigen zu ``_rotation_ausstiege`` auf.
        """
        cfg, pm = lage.cfg, lage.pm
        consensus_retention_veto = lage.consensus_retention_veto
        xsec, cross_section_standing = runde.xsec, runde.cross_section_standing
        LSTM_DYNAMIC_TOP_N = runde.top_n
        min_hold_days, hysteresis = runde.min_hold_days, runde.hysteresis
        rot_cap, session_cap = runde.rot_cap, runde.session_cap
        rot_exits = runde.rot_exits
        for sym, score in list(getattr(pm, "_position_scores", {}).items()):
            if sym in already_acted or sym in exited:
                continue
            _pct, rank, n = cross_section_standing(xsec, sym)
            if rank is None:
                continue  # fail-safe HOLD — never a false rotation sell
            in_top_n = rank <= LSTM_DYNAMIC_TOP_N
            days_held = float(getattr(score, "days_held", 0) or 0)
            min_hold_ok = days_held >= min_hold_days
            rank_collapsed = rank > LSTM_DYNAMIC_TOP_N * hysteresis
            # #2711 hard gate: rank collapse may not override min-hold.
            _hard_gate = bool(getattr(cfg, "ROTATION_MIN_HOLD_HARD_GATE", True))
            if (
                not in_top_n
                and rank > LSTM_DYNAMIC_TOP_N
                and (min_hold_ok or (rank_collapsed and not _hard_gate))
            ):
                # #3180: retain a name the live round table still rates highly.
                # Checked BEFORE the cap so a retained name never consumes a slot.
                if consensus_retention_veto(sym, "rotation", pm):
                    logging.info(
                        "[Rotation] %s: exit SUPPRESSED — live round-table "
                        "consensus still retains the name (#3180 gate)",
                        sym,
                    )
                    continue
                if rot_cap > 0 and rot_exits >= rot_cap:
                    logging.warning(
                        "[Rotation] per-cycle cap %d reached — deferring "
                        "further eligible exits to the next cycle.",
                        rot_cap,
                    )
                    break
                if session_cap > 0 and (
                    int(getattr(self, "_rotation_exits_session", 0) or 0) >= session_cap
                ):
                    logging.warning(
                        "[Rotation] per-session cap %d reached — no further "
                        "rotation exits today (resets next trading day). "
                        "Protective stop-losses are unaffected.",
                        session_cap,
                    )
                    break
                ctx = DecisionContext(
                    symbol=sym,
                    action="SELL",
                    current_price=float(getattr(score, "current_price", 0.0) or 0.0),
                    in_position=True,
                    position_qty=float(getattr(score, "qty", 0.0) or 0.0),
                    position_avg_price=float(getattr(score, "avg_entry", 0.0) or 0.0),
                    triggered_by_stop=False,
                    portfolio_reason=(
                        f"rotation: dropped to rank {rank}/{n} "
                        f"(out of top-{LSTM_DYNAMIC_TOP_N})"
                    ),
                )
                event = SignalEvent(
                    symbol=sym,
                    action="SELL",
                    decision_context=ctx,
                    suggested_quantity=0.0,  # full exit
                )
                await self._process_signal_event(event)
                exited.add(sym)
                rot_exits += 1
                runde.rot_exits = rot_exits
                self._rotation_exits_session = (
                    int(getattr(self, "_rotation_exits_session", 0) or 0) + 1
                )
                try:
                    pm.record_trade(sym, "sell")
                except Exception:  # noqa: BLE001 — bookkeeping only
                    # CLAUDE.md §5.6: never swallow silently. This is
                    # cooldown/freshness bookkeeping AFTER the SELL was
                    # already dispatched — the exit stands; only the
                    # anti-churn cooldown may be missed, so log + continue.
                    logging.warning(
                        "[Rotation] failed to record trade for %s "
                        "(cooldown bookkeeping; SELL already dispatched)",
                        sym,
                        exc_info=True,
                    )
                logging.info(
                    "[Rotation] %s: rank %s/%s out of top-%s → " "full SELL dispatched",
                    sym,
                    rank,
                    n,
                    LSTM_DYNAMIC_TOP_N,
                )

    async def _rotation_ueberhang(
        self,
        lage: _AusstiegsLage,
        runde: _Rotationsrunde,
        already_acted: set,
        exited: set,
    ) -> None:
        """#3823 Lever A, #2886: Ueberhang ueber dem Buch-Deckel abbauen.

        Schwaechster Rang zuerst, Mindesthaltedauer bindend, im Rest des Zyklus- und
        Sitzungsbudgets der Rotation. Fehler steigen zu ``_rotation_ausstiege`` auf.
        """
        pm = lage.pm
        consensus_retention_veto = lage.consensus_retention_veto
        xsec, cross_section_standing = runde.xsec, runde.cross_section_standing
        min_hold_days = runde.min_hold_days
        rot_cap, session_cap = runde.rot_cap, runde.session_cap
        rot_exits = runde.rot_exits
        from core.engine.book_overflow import select_overflow_unwind

        _held = dict(getattr(pm, "_position_scores", {}) or {})
        _cap = int(getattr(pm, "max_positions", 10) or 10)
        if len(_held) > _cap:
            _cycle_left = max(0, rot_cap - rot_exits)
            _sess_used = int(getattr(self, "_rotation_exits_session", 0) or 0)
            _sess_left = (
                max(0, session_cap - _sess_used) if session_cap > 0 else _cycle_left
            )
            _budget = min(_cycle_left, _sess_left)
            for sym in select_overflow_unwind(
                {
                    s: sc
                    for s, sc in _held.items()
                    if s not in already_acted and s not in exited
                },
                rank_of=lambda s: cross_section_standing(xsec, s)[1],
                max_positions=_cap,
                min_hold_days=min_hold_days,
                budget=_budget,
            ):
                score = _held[sym]
                # #3180: overflow unwind is an OPINION rank exit — retain a
                # name the live round table still rates highly.
                if consensus_retention_veto(sym, "book_overflow", pm):
                    logging.info(
                        "[BookOverflow] %s: unwind SUPPRESSED — live "
                        "round-table consensus retains the name (#3180)",
                        sym,
                    )
                    continue
                ctx = DecisionContext(
                    symbol=sym,
                    action="SELL",
                    current_price=float(getattr(score, "current_price", 0.0) or 0.0),
                    in_position=True,
                    position_qty=float(getattr(score, "qty", 0.0) or 0.0),
                    position_avg_price=float(getattr(score, "avg_entry", 0.0) or 0.0),
                    triggered_by_stop=False,
                    portfolio_reason=(
                        f"book_overflow_unwind: {len(_held)}/{_cap} "
                        f"positions held — orderly wind-down (#2886)"
                    ),
                )
                await self._process_signal_event(
                    SignalEvent(
                        symbol=sym,
                        action="SELL",
                        decision_context=ctx,
                        suggested_quantity=0.0,  # full exit
                    )
                )
                exited.add(sym)
                self._rotation_exits_session = (
                    int(getattr(self, "_rotation_exits_session", 0) or 0) + 1
                )
                try:
                    pm.record_trade(sym, "sell")
                except Exception:  # noqa: BLE001 — bookkeeping only
                    logging.warning(
                        "[BookOverflow] failed to record trade for %s "
                        "(cooldown bookkeeping; SELL already "
                        "dispatched)",
                        sym,
                        exc_info=True,
                    )
                logging.warning(
                    "[BookOverflow] %s: unwind SELL dispatched " "(%d/%d held) — #2886",
                    sym,
                    len(_held),
                    _cap,
                )

    async def _trim_ausstiege(self, lage: _AusstiegsLage, acted: set) -> set:
        """#3823 Lever B: TRIM (de-concentrate what remains).

        ``acted`` enthaelt die Namen, die diese Runde nicht mehr anfasst - die des
        Aufrufers und die Rotations-Ausstiege. Gibt nur die eigenen Trims zurueck.
        """
        exited: set = set()
        cfg, now, pm = lage.cfg, lage.now, lage.pm
        consensus_retention_veto = lage.consensus_retention_veto
        try:
            # #2714: day-scoped session cap for the TRIM lever (rotation had one).
            _trim_cap = int(getattr(cfg, "TRIM_MAX_EXITS_PER_SESSION", 3) or 0)
            _t_today = now.date()
            if getattr(self, "_trim_session_date", None) != _t_today:
                self._trim_session_date = _t_today
                self._trim_exits_session = 0
            # Fallback MUST equal the config default (#2784): a $50 fallback would
            # re-impose the floor that blocked every small account, and it fires
            # silently whenever cfg is a partial namespace / test double.
            min_order_value = float(getattr(cfg, "MIN_ORDER_VALUE_USD", 1.0))
            recs = await asyncio.to_thread(pm.get_rebalance_recommendations)
            scores = getattr(pm, "_position_scores", {})
            for rec in recs or ():
                if (
                    _trim_cap > 0
                    and int(getattr(self, "_trim_exits_session", 0) or 0) >= _trim_cap
                ):
                    logging.warning(
                        "[Trim] per-session cap %d reached - no further trims "
                        "today (resets next trading day). Protective stops are "
                        "unaffected (#2714).",
                        _trim_cap,
                    )
                    break
                if rec.get("action") != "REDUCE":
                    continue
                sym = rec.get("symbol")
                if sym in acted or sym in exited:
                    continue
                mv = float(rec.get("market_value", 0.0) or 0.0)
                if mv <= 0:
                    continue  # no price division; skip on non-positive market value
                score = scores.get(sym)
                held_qty = float(getattr(score, "qty", 0.0) or 0.0) if score else 0.0
                if held_qty <= 0:
                    continue
                adjustment = abs(float(rec.get("adjustment_value", 0.0) or 0.0))
                # clamp to held → can never oversell (executor honors a partial verbatim)
                trim_qty = held_qty * min(1.0, adjustment / mv)
                if trim_qty <= 0:
                    continue
                # Dust-skip only where a price is safely available.
                price = (
                    float(getattr(score, "current_price", 0.0) or 0.0) if score else 0.0
                )
                if price > 0 and trim_qty * price < min_order_value:
                    continue
                # #3180: trim must be consensus-aware like take-profit (owner directive)
                # — retain a name the live round table still rates highly.
                if consensus_retention_veto(sym, "trim", pm):
                    logging.info(
                        "[Trim] %s: trim SUPPRESSED — live round-table consensus "
                        "still retains the name (#3180 gate)",
                        sym,
                    )
                    continue
                ctx = DecisionContext(
                    symbol=sym,
                    action="SELL",
                    current_price=price,
                    in_position=True,
                    position_qty=held_qty,
                    position_avg_price=(
                        float(getattr(score, "avg_entry", 0.0) or 0.0) if score else 0.0
                    ),
                    triggered_by_stop=False,
                    portfolio_reason=(
                        f"deconcentration_trim: drift "
                        f"{rec.get('drift_pct', 0.0):+.1f}% → target"
                    ),
                )
                event = SignalEvent(
                    symbol=sym,
                    action="SELL",
                    decision_context=ctx,
                    suggested_quantity=trim_qty,  # partial
                )
                await self._process_signal_event(event)
                exited.add(sym)
                # #3604: a trim is a PARTIAL exit - never a re-entry lockout.
                self._reentry_partial_exits = getattr(
                    self, "_reentry_partial_exits", set()
                ) | {sym}
                try:
                    self._trim_exits_session = (
                        int(getattr(self, "_trim_exits_session", 0) or 0) + 1
                    )
                    pm.record_trade(sym, "sell")
                except Exception:  # noqa: BLE001 — bookkeeping only
                    # CLAUDE.md §5.6: never swallow silently. Cooldown/freshness
                    # bookkeeping AFTER the SELL was already dispatched — the exit
                    # stands; only the anti-churn cooldown may be missed → log.
                    logging.warning(
                        "[Trim] failed to record trade for %s "
                        "(cooldown bookkeeping; SELL already dispatched)",
                        sym,
                        exc_info=True,
                    )
                logging.info(
                    "[Trim] %s: trim %.4f of %.4f held (mv=%.2f adj=%.2f) → "
                    "partial SELL",
                    sym,
                    trim_qty,
                    held_qty,
                    mv,
                    adjustment,
                )
        except Exception as e:  # noqa: BLE001 — a lever failure must not kill the loop
            logging.warning(
                "[Trim] de-concentration lever failed: %s", e, exc_info=True
            )
        return exited
