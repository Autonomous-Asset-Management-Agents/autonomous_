# core/engine/ausstieg_hebel.py
# ARC-E6 G-2a (#3823) — die Hebel der Ausstiegsrunde, ausgezogen aus
# TradingLoopMixin._run_deconcentration_and_rotation_exits (core/engine/trading_loop.py).
"""Die Ausstiegsrunde: Dirigent, Vorbereitung, Rotation (Rangverlust, Ueberhang) und Trim.

#3823 hat die drei Schritte, die Verkaeufe absenden, hierher gezogen. #4245 (H-2d) holt
den Dirigenten ``_run_deconcentration_and_rotation_exits``, die Vorbereitung
``_ausstieg_vorbereiten`` und den Rahmen der Rotation ``_rotation_ausstiege`` (Panel-
Frische, Deckel, Fehlerfang) nach — die Runde liegt damit als ein Thema bei ihren Hebeln
(Schnitt-Entscheidung #4184, ``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md``).
Die drei ``getattr``-Rueckfallwerte des Rotationsrahmens (``SMART_EXIT_MIN_HOLD_DAYS``,
``SMART_EXIT_EXIT_RANK_HYSTERESIS``, ``BOOK_CAP_ENFORCEMENT_ENABLED``) sind im Vertrag
unter diesem Modul eingefroren (``vertrag.toml``, ``getattr_widersprueche``).

Warum ueberhaupt ein zweites Modul: Eine Extraktion in dieselbe Datei verlaengert sie
(Signaturen, Docstrings, Zustandszugriffe; gemessen 3205 -> 3297 Zeilen), die Dateizahl
im Vertrag darf aber nur sinken (``vertrag.toml``) — wie in G-1a und G-1b.

Die Ruempfe sind wortgleich mit dem Stand vor dem Umbau; jeder Schritt bindet am Anfang
die Namen, die er aus ``_AusstiegsLage`` und ``_Rotationsrunde`` liest. Einzige
Abweichung (H-2d): ``get_config`` und ``CompositionRoot`` liest die Vorbereitung als
``_tl.<name>`` am Kernmodul, damit Patches auf ``core.engine.trading_loop`` wirken
(#4184 §3). Der ``_tl``-Import steht am Dateiende, damit der Kreis Kern <-> Hebel in
beiden Ladereihenfolgen traegt. ``TradingLoopMixin`` erbt ``AusstiegsHebelMixin``, damit
die Ausstiegsrunde auch am blossen Mixin vollstaendig ist (Tests bauen es per ``__new__``).

Plan: ``docs/3823-*/implementation_plan.md``, ``docs/4245-*/implementation_plan.md``.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

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
    """#3823/#4245: die Ausstiegsrunde — Dirigent, Vorbereitung, Rotation, Ueberhang, Trim."""

    async def _run_deconcentration_and_rotation_exits(
        self, already_acted: set, now=None
    ) -> set:
        """#sell-decisions: flag-gated per-cycle exit hook — PRODUCES SELL decisions.

        A pre-consensus pass (right after ``_run_position_stop_checks``) with two
        independent, flag-gated levers that both dispatch through the verified-sound
        ``_process_signal_event`` seam and only ever REDUCE exposure:

        * **ROTATION** (``ROTATION_EXIT_ENABLED``): a held name that dropped out of the
          live LSTM top-N past hysteresis / min-hold is FULLY exited (qty 0 → the SELL
          branch fetches the whole open position). Fail-safe: rank ``None`` (symbol absent
          from the panel) → HOLD; a STALE panel (older than ``ROTATION_PANEL_MAX_AGE_DAYS``)
          skips the lever entirely (date-normalized — never ``date − datetime``).
        * **TRIM** (``DECONCENTRATION_TRIM_ENABLED``): an overweight name is PARTIALLY
          reduced by a dollar-fraction of its market value
          (``held_qty * min(1, |adjustment|/market_value)`` — no price division,
          ``market_value <= 0`` skipped, clamped to held so it can never oversell).

        Both SELLs carry ``triggered_by_stop=False`` (a rotation/trim is NOT a price
        stop → no WORM-audit contamination); execution is unaffected (that flag gates
        no order path — churn is BUY-only, the compliance exit-exemption keys on
        side + held_qty). Rotation runs first, then trim on what remains; a symbol is
        acted on at most once per cycle (shared ``already_acted`` set, unioned with this
        hook's own exits). Returns the exited-symbols set so the caller excludes them
        from the consensus (the round table cannot re-buy a just-sold name).

        Flags OFF ⇒ byte-identical no-op (early return; no panel/recommender call, no
        SELL). Fail-safe: each lever is wrapped so a lever error is logged and swallowed
        — a hook failure never kills the loop.
        """
        # #3823: Vorbereitung, dann Lever A (Rotation) und Lever B (Trim) als benannte
        # Schritte. Trim sieht die Rotations-Ausstiege in seiner acted-Menge - ein in
        # Lever A verkaufter Name wird in Lever B nicht erneut verarbeitet.
        lage = self._ausstieg_vorbereiten(now)
        if lage is None:
            return set()  # byte-identical no-op

        already_acted = set(already_acted or set())
        exited: set = set()
        if lage.rotation_on:
            exited |= await self._rotation_ausstiege(lage, already_acted)
        if lage.trim_on:
            exited |= await self._trim_ausstiege(lage, already_acted | exited)
        return exited

    def _ausstieg_vorbereiten(self, now) -> Optional[_AusstiegsLage]:
        """#3823: Konfiguration, Flags, Retention-Gate (#3180), ``now``-Default, PM.

        ``None`` heisst: nichts zu tun (beide Flags aus, oder kein PortfolioManager) -
        der Dirigent gibt dann die leere Menge zurueck, ohne die Uhr zu fragen.
        """
        cfg = _tl.get_config()
        # #3180 Consensus Retention Gate — an OPINION exit (rotation / trim / overflow) is
        # SUPPRESSED when the live round-table blend-consensus still rates the held name
        # highly. Default OFF (CONSENSUS_RETENTION_THRESHOLD<=0) ⇒ byte-identical; fail-open.
        from core.consensus_retention import consensus_retention_veto

        rotation_on = bool(getattr(cfg, "ROTATION_EXIT_ENABLED", False))
        trim_on = bool(getattr(cfg, "DECONCENTRATION_TRIM_ENABLED", False))
        if not rotation_on and not trim_on:
            return None  # byte-identical no-op

        if now is None:
            now = _tl.CompositionRoot.get_instance().clock_port.now()

        strat = getattr(self, "active_strategy", None)
        pm = getattr(strat, "portfolio_manager", None)
        if pm is None:
            return None
        return _AusstiegsLage(
            cfg=cfg,
            now=now,
            rotation_on=rotation_on,
            trim_on=trim_on,
            strat=strat,
            pm=pm,
            consensus_retention_veto=consensus_retention_veto,
        )

    async def _rotation_ausstiege(
        self, lage: _AusstiegsLage, already_acted: set
    ) -> set:
        """#3823 Lever A: ROTATION (thesis-invalidation first), dann Ueberhang (#2886).

        Gibt nur die eigenen Ausstiege zurueck. Ein Fehler wird protokolliert und
        geschluckt; was vorher schon verkauft war, bleibt in der Rueckgabe.
        """
        exited: set = set()
        cfg, now, pm = lage.cfg, lage.now, lage.pm
        try:
            from core.report.lstm_panel_store import (
                active_cross_section,
                cross_section_standing,
                get_store,
            )

            store = get_store()
            snap = store.latest_snapshot_date()  # a datetime.date (or None)
            max_age = int(getattr(cfg, "ROTATION_PANEL_MAX_AGE_DAYS", 3))
            # Refuse a STALE / empty panel — date-normalized (never date − datetime).
            if snap is not None and (now.date() - snap).days <= max_age:
                try:
                    from core.strategies.lstm_strategy import LSTM_DYNAMIC_TOP_N
                except Exception:  # pragma: no cover — constant fallback
                    LSTM_DYNAMIC_TOP_N = 10
                min_hold_days = float(getattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 5.0))
                hysteresis = float(getattr(cfg, "SMART_EXIT_EXIT_RANK_HYSTERESIS", 3.0))
                # #2680: the ONE ranking seam — the SELL trigger must read the SAME
                # table the BUY vote reads. This lever was the second half of the
                # 2026-08-05 IVZ churn: the raw point-in-time rank collapsed from
                # top-10 to 242/501 within three hours and rotated the name straight
                # back out. Flag off (default) → cross_section_at, byte-identical.
                xsec = active_cross_section(store, now)
                await asyncio.to_thread(pm.refresh_positions)
                # Per-cycle cap (2026-07-28 incident): bound how many FULL exits this
                # lever fires in ONE cycle so a ranking shift cannot flush the whole
                # book at once. FLOORS to 1 (owner directive 2026-08-03): a value <=0 is
                # NOT "unlimited" — that convention was a footgun (zeroing the cap re-opened
                # the single-cycle flush). Disable rotation via ROTATION_EXIT_ENABLED, never
                # by zeroing the cap.
                rot_cap = int(getattr(cfg, "ROTATION_MAX_EXITS_PER_CYCLE", 1) or 1)
                if rot_cap < 1:
                    rot_cap = 1
                # Per-SESSION ceiling (2026-08-03): the per-cycle cap alone does NOT bound
                # a whole-book flush — rotation exits are risk-reducing SELLs (exempt from
                # the daily-trades cap) and the loop runs ~390 cycles/session, so a sustained
                # ranking shift would drain the book one name per cycle over the day. This
                # day-scoped counter caps CUMULATIVE rotation exits per trading day; it
                # resets on date rollover. Protective stop-losses are unaffected. <=0 =
                # unlimited (byte-identical rollback). Instance-attr (lazy: the mixin may be
                # built via __new__, so read defensively and never assume __init__ ran).
                # (#2886 audit note: the getattr fallback previously said 3 — stale
                # since #2840 moved the config default to 2; the literal only ever
                # fires if the config field is missing, but it must not contradict it.)
                session_cap = int(
                    getattr(cfg, "ROTATION_MAX_EXITS_PER_SESSION", 2) or 0
                )
                _today = now.date()
                if getattr(self, "_rotation_session_date", None) != _today:
                    self._rotation_session_date = _today
                    self._rotation_exits_session = 0
                runde = _Rotationsrunde(
                    xsec=xsec,
                    cross_section_standing=cross_section_standing,
                    top_n=LSTM_DYNAMIC_TOP_N,
                    min_hold_days=min_hold_days,
                    hysteresis=hysteresis,
                    rot_cap=rot_cap,
                    session_cap=session_cap,
                )
                await self._rotation_rangverlust(lage, runde, already_acted, exited)

                # #2886 (flag-gated): book_overflow unwind — if the book holds MORE
                # than the cap (account-switch incident 14.08.: 12/10), wind the
                # excess down through THIS same lever: weakest rank first, min-hold
                # binding, sharing the per-cycle and per-session budgets above so
                # rotation + unwind together can never exceed the incident caps.
                # Panel freshness is already guaranteed here (same guarded block).
                if bool(getattr(cfg, "BOOK_CAP_ENFORCEMENT_ENABLED", False)):
                    await self._rotation_ueberhang(lage, runde, already_acted, exited)
        except Exception as e:  # noqa: BLE001 — a lever failure must not kill the loop
            logging.warning("[Rotation] exit lever failed: %s", e, exc_info=True)
        return exited

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


# #4245 (H-2d): Kern-Namen (get_config, CompositionRoot) liest die Runde ueber _tl, damit
# die Patch-Ziele der Tests wirken (#4184 §3). Am Dateiende: der Kreis Kern <-> Hebel
# traegt so in beiden Ladereihenfolgen (Muster core/specialist/quellen_edgar.py).
from core.engine import trading_loop as _tl  # noqa: E402
