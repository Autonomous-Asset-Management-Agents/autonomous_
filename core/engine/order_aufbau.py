# core/engine/order_aufbau.py
# #4231 (ARC-E6 H-1b) — Order-Aufbau und Ausstiegsart, wörtlich aus order_executor.py
"""Order-Aufbau und Ausstiegsart — acht freie Funktionen des Order-Pfads (#4231, H-1b).

Wörtlich aus ``order_executor.py`` umgezogen, Schnitt-Entscheidung #4183
(``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1b). Der Kern
re-exportiert jeden Namen; Leser und Patches auf ``order_executor.<name>`` treffen
dasselbe Objekt (Entscheidung §3, Weg b).

Dieses Modul importiert den Kern nicht. Keine der acht Funktionen liest einen Namen, der
nur am Kern gepatcht wird: ``config`` ist das gemeinsame Modulobjekt, Patches setzen
Attribute daran und treffen hier wie dort. ``baue_trade_record`` wird hier gepatcht
(Weg a), weil sein Leser ``erfasse_order_endzustand`` mit umgezogen ist.
"""

import logging
from typing import Any, Optional

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

import config
from core.composition.root import CompositionRoot
from core.dust_floor import dust_exit_skip


def build_broker_order_request(
    *,
    symbol: str,
    qty: float,
    side_enum: OrderSide,
    client_order_id: str,
    current_price: Optional[float],
    use_limit_orders: bool,
    use_limit_exits: bool,
    spread_buffer: float = 0.001,
):
    """EXC-1 / #2558 — choose the marketable-limit vs market path for one order.

    A *marketable* limit crosses the spread (BUY priced UP through the ask, SELL
    priced DOWN through the bid) so it fills promptly with less slippage than a
    plain market order. The limit path is taken when EITHER:

      * the GLOBAL ``USE_LIMIT_ORDERS`` is on — pre-existing behaviour, BUYs *and*
        exits; OR
      * the exit-selective ``USE_LIMIT_EXITS`` is on **and this order is an exit**
        (SELL) — #2558. This is independent of ``USE_LIMIT_ORDERS`` and leaves the
        buy side byte-identical to today.

    The price math is the single pre-existing heuristic (``current_price`` nudged by
    ``spread_buffer``); it is not duplicated. When no usable ``current_price`` is
    available the order falls back to a market request (as today).
    """
    is_exit = side_enum == OrderSide.SELL
    want_limit = bool(use_limit_orders) or bool(use_limit_exits and is_exit)

    if want_limit and current_price:
        limit_price = current_price
        if side_enum == OrderSide.BUY:
            limit_price *= 1.0 + spread_buffer
        else:
            limit_price *= 1.0 - spread_buffer
        limit_price = round(limit_price, 2)
        return LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=side_enum,
            limit_price=limit_price,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )

    return MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=side_enum,
        time_in_force=TimeInForce.DAY,
        client_order_id=client_order_id,
    )


def exit_failsafe_remaining_qty(
    *,
    is_limit_exit: bool,
    order_qty: float,
    live_order,
) -> Optional[float]:
    """#2558 — qty a MARKET fail-safe must re-submit so a non-filled exit still closes.

    Returns the CONFIRMED-unfilled remainder (``order_qty - filled_qty``) only when the
    broker state is observed (``live_order`` present with a numeric ``filled_qty``).
    Returns ``None`` — fail-closed, never re-submit — when this is not a limit exit, the
    order state was never observed (``live_order is None``), the fill is unparseable, or
    the order already fully filled. Guarding on a CONFIRMED remainder prevents an oversell
    when a partial (or full) fill has already landed.
    """
    if not is_limit_exit or live_order is None:
        return None
    try:
        filled = float(getattr(live_order, "filled_qty", 0) or 0)
    except (TypeError, ValueError):
        return None
    remaining = round(float(order_qty) - filled, 6)
    if remaining <= 0:
        return None
    return remaining


def _zahl(wert: Any) -> float:
    """Eine Broker-Zahl als ``float`` — Alpaca liefert sie oft als Zeichenkette."""
    try:
        return float(wert or 0)
    except (TypeError, ValueError):
        return 0.0


def baue_trade_record(order: Any, *, decision_id: str = "", strategy_name: str = ""):
    """Den analytischen Datensatz aus dem **Broker-Objekt** bauen (#2783 Inkrement 2).

    Aus dem Broker-Objekt, **nicht aus der Absicht**: Die Absicht sagt, was wir wollten;
    das Broker-Objekt sagt, was geschah. Ein Datensatz aus der Absicht waere eine Kopie
    unserer eigenen Annahme und im Abgleich wertlos.

    **Der Status wird uebernommen, nicht angenommen.** ``TradeRecord.order_status`` hat
    den Vorgabewert ``"filled"`` (``cloud_logger.py:311``). Wer ihn nicht setzt, erklaert
    jede Order zum Erfolg — auch die stornierte. Das Ticket verlangt ausdruecklich das
    Gegenteil: nicht gefuellte Auftraege werden erfasst, **nie als Erfolg**.
    """
    from core.cloud_logger import TradeRecord

    menge = _zahl(getattr(order, "filled_qty", 0))
    preis = _zahl(getattr(order, "filled_avg_price", 0))
    seite = str(getattr(order, "side", "") or "")
    return TradeRecord(
        symbol=str(getattr(order, "symbol", "") or ""),
        side="sell" if seite.lower().endswith("sell") else "buy",
        qty=menge,
        price=preis,
        total_value=menge * preis,
        order_status=str(getattr(order, "status", "") or "unknown"),
        decision_id=decision_id or "",
        strategy_name=strategy_name or "RLAgent",
    )


async def erfasse_order_endzustand(
    *,
    order: Any,
    symbol: str,
    action: str,
    decision_id: str = "",
    order_value: float = 0.0,
    approval_id: Optional[str] = None,
    strategy_name: str = "",
) -> None:
    """Schreibt den Order-Endzustand in **beide** Ebenen (#2783 Inkrement 2).

    * **verkettet (beweisfuehrend):** ``HITLExecutionEvent`` mit Menge, Preis, Status und
      Broker-Order-ID auf derselben Art-14-Hash-Kette, die schon die Freigabe traegt.
    * **analytisch:** ``log_trade(TradeRecord)`` — die Senke existiert seit Langem
      (``cloud_logger.py:958``) und hatte bis hierhin **null Produktionsaufrufer**.

    **Reine Beobachtung. Wirft nie.** Wenn diese Funktion laeuft, liegt die Order bereits
    beim Broker — das ist eine Tatsache, an der unsere Buchfuehrung nichts aendert. Eine
    Ausnahme hier wuerde den Pfad abbrechen, **nachdem** Kapital bewegt wurde, und das
    waere deutlich schlimmer als ein fehlender Datensatz. Darum ist jede Senke einzeln
    gekapselt: Faellt eine aus, schreibt die andere trotzdem.

    Ohne Broker-Objekt passiert nichts. Lieber kein Datensatz als ein erfundener — eine
    Null-Fuellung, die wir gar nicht beobachtet haben, waere von einer echten nicht zu
    unterscheiden.
    """
    if order is None:
        logging.warning(
            "[%s] Endzustand nicht erfasst: kein Broker-Objekt. Lieber kein Datensatz "
            "als ein erfundener (#2783).",
            symbol,
        )
        return

    # -- Ebene 1: die verkettete Pruefebene --------------------------------------
    try:
        from core import hitl_gate
        from core.round_table.senate_log import HITLExecutionEvent

        await hitl_gate.log_execution_event(
            HITLExecutionEvent(
                timestamp=CompositionRoot.get_instance().clock_port.now().isoformat(),
                symbol=symbol,
                action=action,
                branch="executed",
                policy_hash=hitl_gate.policy_hash(hitl_gate.policy_snapshot()),
                order_value=float(order_value or 0.0),
                approval_id=approval_id,
                decision_id=decision_id or None,
                broker_order_id=str(getattr(order, "id", "") or "") or None,
                filled_qty=_zahl(getattr(order, "filled_qty", 0)),
                filled_avg_price=_zahl(getattr(order, "filled_avg_price", 0)),
                order_status=str(getattr(order, "status", "") or "unknown"),
            )
        )
    except Exception:
        logging.warning(
            "[%s] Endzustand nicht in die Pruefkette geschrieben — die Order ist "
            "trotzdem beim Broker (#2783).",
            symbol,
            exc_info=True,
        )

    # -- Ebene 2: die analytische Senke ------------------------------------------
    try:
        # Ueber das MODUL, nicht ueber einen direkten Namensimport: So laesst sich die
        # Senke im Test ersetzen und der Aufruf nachweisen. Ein direkter Import waere
        # hier zudem gefaehrlich — er scheiterte still im Fehlerblock unten, und der
        # Test bliebe gruen, obwohl nichts geschrieben wird. Genau das ist mir beim
        # ersten Entwurf passiert (`cloud_logger` gibt es nicht, es heisst
        # `logger_instance`).
        from core import cloud_logger as _cl

        satz = baue_trade_record(
            order, decision_id=decision_id, strategy_name=strategy_name
        )
        _cl.logger_instance.log_trade(satz)
    except Exception:
        logging.warning(
            "[%s] Endzustand nicht in die analytische Senke geschrieben — die "
            "Pruefkette hat ihn trotzdem (#2783).",
            symbol,
            exc_info=True,
        )


def halt_exempt_protective_exit(context, action) -> bool:
    """#3380 (ARC-E1.4): Darf diese Order den Kill-Switch-Halt passieren?

    Genau dann, wenn sie ein **Schutz-Exit** ist: ein SELL, den der Stop-Pfad als solchen
    ausgewiesen hat (``DecisionContext.triggered_by_stop``, gesetzt in
    ``schleifen_stops.py:231``). Ein Halt soll neue Einstiege verhindern, nicht den Schutz
    offener Positionen — sonst liegt eine Position genau dann ohne Schwelle da, wenn die
    Lage ohnehin schon schlecht ist.

    Bewusst eng gefasst:

    * **BUY nie.** Ein Halt bedeutet: kein neues Kapital in den Markt.
    * **Rotation und Trim nie.** Sie tragen ``triggered_by_stop=False``
      (``ausstieg_hebel.py:312``, ``:519``) — sie sind Meinungs-, keine Schutz-Exits.
    * **Kein Kontext, keine Freistellung.** Fehlt der Kontext oder das Kennzeichen, gilt
      der Halt. Die Ausfallrichtung ist „blockieren", nicht „durchlassen".

    Die Entscheidung ist eine reine Funktion und damit einzeln pruefbar; sie steht
    absichtlich neben ``classify_exit_kind``, das die Compliance-Freistellung steuert.
    Beide beschreiben denselben Gedanken an zwei Toren — zusammengefuehrt werden sie mit
    dem Gateway aus #3379.
    """
    if context is None:
        return False
    if str(action).upper() != "SELL":
        return False
    return bool(getattr(context, "triggered_by_stop", False))


def classify_exit_kind(event) -> "Optional[str]":
    """#2713: derive the exit class from the decision event — no new plumbing needed.

    "risk"      -> triggered_by_stop (stop-loss/TP/trailing/position-stop): stays EXEMPT,
                   a protective exit must never queue behind a guard.
    "rotation"  -> lever-A rank exits (portfolio_reason prefix "rotation").
    "trim"      -> deconcentration/drift/sector-cap REDUCEs.
    None        -> unknown/legacy paths: keeps today's exemption (fail-SAFE for risk
                   paths not yet labeled — never blocks a stop-out by accident).
    """
    if event is None:
        return None
    if bool(getattr(event, "triggered_by_stop", False)):
        return "risk"
    reason = str(getattr(event, "portfolio_reason", "") or "").lower()
    if reason.startswith("rotation"):
        return "rotation"
    if (
        reason.startswith(("trim", "deconcentration", "sector_cap"))
        or "drift" in reason
        or "sector_cap" in reason
    ):
        return "trim"
    return None


def _dust_floor_skips_exit(
    *,
    event,
    context,
    qty,
    held_qty,
    symbol: str,
    strategy=None,
    equity=None,
    rec_outcome=None,
) -> bool:
    """#2789: map executor context onto the pure dust-floor decision (core/dust_floor.py).

    ONE adapter for BOTH submit paths. `_execute_tenant_order` and `_process_signal_event`
    name their variables differently (`size` vs `qty`) but carry the same facts, and the
    #2721 lesson is that a guard wired to one path only is inert on the other — the
    desktop/[Global] path is the one the 2026-07-28 rotation flush ran through. Keeping
    the mapping here means both paths cannot drift apart.

    Fails OPEN on unreadable context: a missing price or position must never be the reason
    a protective exit is dropped. The exemptions themselves (stop / unlabeled / full
    close) live in the pure function, so they are testable without an executor.
    """
    try:
        price = float(getattr(context, "current_price", 0.0) or 0.0) if context else 0.0
        order_qty = abs(float(qty or 0.0))
        pos_qty = abs(float(held_qty or 0.0))
    except (TypeError, ValueError):
        return False
    # Equity for the backstop, best source per path: the tenant path holds a real
    # `tenant["equity"]`; the desktop path has none for SELLs (its account read at :2165
    # is BUY-only), so it falls back to the strategy's capital base — no extra broker
    # call on the order path. Unresolvable → 0.0, which makes ONLY the backstop inert;
    # rule A (position-relative) is the primary rule and keeps working.
    try:
        equity_val = (
            float(equity)
            if equity is not None
            else float(
                getattr(
                    getattr(strategy, "portfolio_manager", None), "total_capital", 0.0
                )
                or 0.0
            )
        )
    except (TypeError, ValueError):
        equity_val = 0.0

    skip, reason = dust_exit_skip(
        enabled=bool(getattr(config, "DUST_EXIT_FLOOR_ENABLED", False)),
        notional=order_qty * price,
        position_value=pos_qty * price,
        equity=equity_val,
        exit_kind=classify_exit_kind(event),
        # 0.999 = float tolerance, NOT a policy knob: a rounded "sell everything" must
        # still count as a FULL close, or the floor could trap the last sliver.
        is_full_close=pos_qty > 0 and order_qty >= pos_qty * 0.999,
        min_position_pct=float(getattr(config, "DUST_EXIT_MIN_POSITION_PCT", 0.10)),
        min_equity_pct=float(getattr(config, "DUST_EXIT_MIN_EQUITY_PCT", 0.001)),
    )
    if skip:
        logging.warning(
            "[DustFloor] %s exit skipped — %s (exit_kind=%s). Deferred, not cancelled: "
            "the next cycle re-evaluates once the drift is worth an order (#2789).",
            symbol,
            reason,
            classify_exit_kind(event),
        )
        if rec_outcome is not None:
            try:
                rec_outcome(symbol, "skipped:dust_floor", reason)
            except Exception:  # observation must never raise into the trading path
                pass
    return skip
