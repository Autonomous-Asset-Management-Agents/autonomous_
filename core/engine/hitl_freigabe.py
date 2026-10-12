# core/engine/hitl_freigabe.py
# ARC-E6 H-1e (#4234) — HITL-Freigabe und Markt-Tor, ausgezogen aus OrderExecutorMixin
# (core/engine/order_executor.py).
"""Die Ausfuehrung menschlich freigegebener Orders, das HITL-Tor und das Markt-Tor.

Hier wohnen ``execute_approved_order`` (HITL-Drain, Art. 14), ihr Schritt
``_freigabe_unter_materialitaet`` (Materialitaets-Riegel, Issue 2935),
``_market_closed_blocks_order`` (INC-6) und ``_hitl_holds_order`` (#2704). Gerufen werden
sie ueber ``self.``: ``execute_approved_order`` aus dem Trading-Loop, die beiden Tore aus
dem Kern (``_process_signal_event``, ``_schritt_mandant_portfolio``).

Die Ruempfe sind wortgleich mit dem Stand im Executor, mit derselben angemeldeten
Abbildung wie in G-1a, G-1b und H-1d (Entscheidung #4183 §3, Weg b): Namen, die Tests am
Modul ``order_executor`` neu binden, lesen die Ruempfe als ``order_executor.<name>`` —
sonst griffe der Patch ins Leere. Das sind ``config``, ``logging``,
``create_trading_client``, ``_rec_outcome`` und ``_capture_outcome``. Einzige weitere
Aenderung: Der Materialitaets-Block von ``execute_approved_order`` ist der Schritt
``_freigabe_unter_materialitaet`` mit Abbruch-Ergebnis (Entscheidung §2, „Funktionen ueber
150“). Die funktionslokalen Importe bleiben funktionslokal.

Die Importrichtung ist erzwungen: Dieses Modul importiert den Executor, nie umgekehrt.
Zusammengesetzt wird in ``AusfuehrungMixin`` (``ausfuehrung.py``).

Plan: ``docs/4234-h-1e-a-hitl-freigabe-und-markt-tor-a-hitl-freiga/implementation_plan.md``.
"""

import asyncio
from typing import Any, Dict, Optional

from core.composition.root import CompositionRoot
from core.events import SignalEvent
from core.risk_manager import is_immaterial_entry

from . import order_executor
from .order_executor import _capture_enabled


class HitlFreigabeMixin:
    """HITL-Freigabe und Markt-Tor; ueber ``AusfuehrungMixin`` in ``BotEngine`` zusammengesetzt."""

    async def execute_approved_order(
        self, payload: Dict[str, Any], source: str = "human_approved"
    ) -> bool:
        """Execute a human-approved order drained from the HITL queue (PR-0a-ii-5a, Art. 14).

        Bypasses the HITL gate — routing an approved order back through _process_signal_event
        would re-evaluate the threshold and re-queue it (N1). Resolves the tenant by
        payload["user_id"], builds a synthetic SignalEvent (N8), and delegates to
        _execute_tenant_order(source="human_approved") — which skips ONLY the daily-cap gate
        + the autonomous-budget increment; RiskManager / PortfolioManager / check_order /
        kill-switch still apply. The outcome is audited on the Art-14 hash chain.

        Decision-2 Option B (N11): on an OSS engine with no matching OAuth tenant, HOLD + warn
        + audit "rejected" — never reuse the inline global path. Returns True iff submitted.
        """
        from core import hitl_gate
        from core.cloud_logger import DecisionContext
        from core.round_table.senate_log import HITLExecutionEvent

        user_id = payload.get("user_id", "")
        symbol = payload.get("symbol", "")
        action = payload.get("action", "")
        qty = float(payload.get("qty", 0.0) or 0.0)
        price = float(payload.get("price", 0.0) or 0.0)
        order_value = abs(qty) * price
        if await self._freigabe_unter_materialitaet(
            payload, user_id, symbol, action, order_value
        ):
            return False
        pol_hash = hitl_gate.policy_hash(hitl_gate.policy_snapshot())

        async def _audit(branch: str, reason: Optional[str] = None) -> None:
            await hitl_gate.log_execution_event(
                HITLExecutionEvent(
                    timestamp=CompositionRoot.get_instance()
                    .clock_port.now()
                    .isoformat(),
                    symbol=symbol,
                    action=action,
                    branch=branch,
                    policy_hash=pol_hash,
                    order_value=order_value,
                    approval_id=payload.get("approval_id"),
                    reason=reason,
                )
            )

        # SHOULD-FIX (review): a malformed payload must never reach the broker path that would
        # silently re-derive a size. An unvaluable order — no price, or a BUY with no quantity —
        # is rejected + audited, not executed on a guess.
        if price <= 0 or (action == "BUY" and qty <= 0):
            order_executor.logging.warning(
                "[HITL] approved order %s %s: malformed payload (price=%.4f qty=%.4f) — refusing.",
                action,
                symbol,
                price,
                qty,
            )
            await _audit("rejected", reason="malformed_payload")
            return False

        tenant, ablehnungsgrund = await self._broker_zugang_fuer(user_id)
        if tenant is None:
            if ablehnungsgrund == "no_oauth_tenant":
                order_executor.logging.warning(
                    "[HITL] approved order %s %s: no OAuth tenant for user_id=%s — "
                    "refusing to execute. Es GIBT eine Mandanten-Aufstellung, dieser "
                    "Nutzer steht nur nicht darin.",
                    action,
                    symbol,
                    user_id,
                )
            else:
                order_executor.logging.warning(
                    "[HITL] approved order %s %s: kein Broker-Zugang hinterlegt "
                    "(user_id=%s) — die Freigabe kann nicht ausgefuehrt werden. Ein "
                    "Mensch hat sie erteilt; ohne Zugang bleibt sie wirkungslos (#3391).",
                    action,
                    symbol,
                    user_id,
                )
            await _audit("rejected", reason=ablehnungsgrund)
            return False

        context = DecisionContext(
            symbol=symbol,
            action=action,
            current_price=price,
            conviction_score=float(payload.get("conviction", 0.0) or 0.0),
            risk_approved=True,
            portfolio_approved=True,
            intelligence_approved=True,
        )
        # Broker-side idempotency (DD F3): stamp the approval_id as the deterministic
        # client_order_id, so a re-submission of the SAME approved order — an accidental
        # double-drain, or a future auto-recovery of an orphaned in-flight approval — is
        # rejected by the broker as a duplicate client_order_id. A single human approval can
        # therefore never execute twice, even if the same payload reaches the broker path more
        # than once. (Default factory would otherwise mint a fresh uuid4 per call.)
        _approval_cid = str(payload.get("approval_id") or "").strip()
        if _approval_cid:
            # Cap at Alpaca's client_order_id limit (128). The real source is always a uuid4
            # (~41 chars incl. the prefix) so this never truncates in practice; it is a
            # defensive floor so a malformed/overlong approval_id can never produce a
            # broker-rejected order id that would block a legitimate human-approved order.
            context.client_order_id = f"hitl-{_approval_cid}"[:128]
        event = SignalEvent(
            symbol=symbol,
            action=action,
            suggested_quantity=qty,
            decision_context=context,
            is_simulation=False,
        )
        # BLOCKER (review): record the human-approval decision on the immutable chain BEFORE the
        # order can reach the broker — capital must never move without a prior Art-14 record (the
        # audit logger can suspend on disk-full; pop_approved already deleted the queue key). The
        # pinned ceiling (ADR-016) makes the audited order_value == what the human authorised.
        await _audit("approved")
        submitted = await self._execute_tenant_order(tenant, event, source=source)
        if not submitted:
            # The approved order was blocked at execution (Iron Dome / risk / position guard —
            # the specific guard is logged by _execute_tenant_order). Never silently dropped (P3).
            await _audit("iron_dome_rejected", reason="execution_blocked")
        return bool(submitted)

    async def _freigabe_unter_materialitaet(
        self,
        payload: Dict[str, Any],
        user_id: str,
        symbol: str,
        action: str,
        order_value: float,
    ) -> bool:
        """Materialitaets-Riegel der HITL-Freigabe (Issue 2935).

        Schritt von ``execute_approved_order``. Liefert ``True`` (Abbruch), wenn die
        freigegebene Kauforder unter der Materialitaetsschwelle liegt; dann ist
        ``skipped:immaterial_entry`` auditiert und die Freigabe endet ohne Absendung.
        Sonst ``False``. Ein Fehler im Riegel wird wie zuvor geschluckt, und die
        Freigabe laeuft weiter (#4234, Entscheidung #4183 §2).
        """
        # Issue 2935: Materialitaets-Riegel (Option A) - HITL-Drain
        import config as _cfg
        from core import hitl_gate
        from core.round_table.senate_log import HITLExecutionEvent

        _cfg_obj = _cfg.get_config()
        _mat_pct = float(getattr(_cfg_obj, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", 0.0))
        if _mat_pct > 0 and action == "BUY" and order_value > 0:
            try:
                active_tenants = await self.get_active_tenant_clients()
                tenant = next(
                    (t for t in active_tenants if t.get("user_id") == user_id), None
                )
                if tenant:
                    client = order_executor.create_trading_client(tenant)
                    acct = await asyncio.to_thread(client.get_account)
                    # #2981: shared Materiality-Helper, Divisor equity (unveraendert)
                    if is_immaterial_entry(order_value, float(acct.equity), _mat_pct):
                        pol_hash = hitl_gate.policy_hash(hitl_gate.policy_snapshot())
                        await hitl_gate.log_execution_event(
                            HITLExecutionEvent(
                                timestamp=CompositionRoot.get_instance()
                                .clock_port.now()
                                .isoformat(),
                                symbol=symbol,
                                action=action,
                                branch="skipped:immaterial_entry",
                                policy_hash=pol_hash,
                                order_value=order_value,
                                approval_id=payload.get("approval_id"),
                                reason=f"Order size  is < {_mat_pct*100:.1f}% of target allocation ",
                            )
                        )
                        return True
            except Exception:
                pass
        return False

    async def _market_closed_blocks_order(self, symbol: str, action: str) -> bool:
        """INC-6 (continuous operation): the explicit market-open gate at the ORDER step.

        Research/analysis/report-evaluation runs CONTINUOUSLY even when the market
        is closed (the trading loop no longer sleep-skips a closed cycle), but order
        PLACEMENT stays market-gated: we must NOT submit to the broker while the
        market is closed unless ``BYPASS_MARKET_HOURS`` is set (test override).

        Returns ``True`` — and records the ``blocked:market_closed`` execution
        outcome plus a telemetry log — when the order MUST be blocked. Fail-OPEN on
        any clock error: mirrors the trading loop, which proceeds to trade when the
        clock call fails, so a transient clock outage can never freeze an open book.
        """
        try:
            if order_executor.config.get_config().BYPASS_MARKET_HOURS:
                return False
        except Exception:  # noqa: BLE001 — an unreadable bypass flag must not gate
            pass
        try:
            clock = await asyncio.to_thread(self.api.get_clock)
            is_open = bool(clock.is_open)
        except Exception as e:  # noqa: BLE001 — fail-open (mirror the trading loop)
            order_executor.logging.warning(
                "[%s] INC-6 order market-gate: clock check failed (%s) — allowing "
                "order (fail-open, mirrors trading loop).",
                symbol,
                e,
            )
            return False
        # Pure observation: keep /engine-diagnostics' cached flag fresh.
        try:
            self._last_market_open = is_open
        except Exception:  # noqa: BLE001
            pass
        if is_open:
            return False
        order_executor.logging.warning(
            "[%s] 🌙 INC-6 order market-gate: %s BLOCKED — market CLOSED "
            "(BYPASS_MARKET_HOURS off). No broker submission.",
            symbol,
            action,
        )
        order_executor._rec_outcome(
            symbol,
            "blocked:market_closed",
            "Market closed — order not submitted (INC-6 order gate).",
        )
        return True

    async def _hitl_holds_order(
        self, event, context, symbol: str, user_id: str, calculated_qty: float
    ) -> bool:
        """#2704 — the Art-14 gate at its correct position: after sizing, before any mutation.

        Returns True when the order was queued for human approval and the caller must return
        WITHOUT submitting anything. Records `hitl_held` on the outcome recorder + the durable
        capture, identically in both paths (a missing capture in one path would queue orders that
        are invisible in `decision_outcomes`).

        Both call sites pass the quantity the executor resolved, so the queue entry, the approval
        UI and the ADR-016 ceiling all see the REAL size. Dormant when `HITL_ENABLED` is off (the
        default) — then this is a single boolean check and the path is byte-identical.

        Guarded on a positive quantity by the caller: a 0-size order is aborted further down
        anyway, and queueing one would reproduce exactly the Qty-0 symptom this fixes.
        """
        if not order_executor.config.get_config().HITL_ENABLED:
            return False
        from core import hitl_gate

        # Fail-closed lives INSIDE should_hold (any error there returns HOLD). That property
        # matters more here than at the old position: the path from this line to the broker is
        # much shorter.
        if not await hitl_gate.should_hold(
            event, user_id, calculated_qty=calculated_qty
        ):
            return False
        if _capture_enabled():
            order_executor._rec_outcome(
                symbol,
                "hitl_held",
                "queued for human approval (HITL gate)",
                decision_id=getattr(context, "decision_id", None),
            )
            order_executor._capture_outcome(context)
        return True
