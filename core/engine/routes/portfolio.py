"""Portfolio-Lese-Router (#4070, ARC-E6 G-4d).

Uebersicht (``/portfolio-summary``), Intraday-Kurve (``/portfolio-intraday``), Aktivitaeten
(``/activities``), letzte Trades (``/recent-trades``), offene Orders (``/open-orders``),
Kurse (``/stock-history``), Top-Picks (``/top-picks``) und Nachrichten (``/recent-news``):
was die Konsole vom Konto und vom Markt zeigt. Alle Routen lesen nur, keine platziert oder
storniert eine Order. Sie sind unveraendert aus ``api_routes.py`` umgezogen; die
HTTP-Flaeche bleibt gleich (``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und geteilte Helfer (``engine``, ``config``, ``_resolve_orders_client``,
``_get_live_cashflows``, ``_get_performance_baseline``) liest das Modul zur Laufzeit ueber
``ar`` (Zugriffsregel, ``core/engine/routes/__init__.py``), damit Patches auf
``core.engine.api_routes.<name>`` weiter greifen.

Plan: ``docs/4070-g4d-portfolio-router/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request

from core.auth import require_engine_key, verify_user_id_sig
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.secret_manager_utils import oauth_secrets
from core.user_wallet_store import wallet_store

router = APIRouter()
ROUTER.append(router)


@router.get(
    "/top-picks",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_top_picks():
    return {
        "status": "success",
        "picks": getattr(ar.engine, "_last_top_picks", []),
    }  # noqa: E501


def _enum_value_lower(x: object) -> str:
    """Normalise an Alpaca enum-or-str field to its lowercase value.

    alpaca-py fields like ``Order.status`` / ``Order.side`` are ``(str, Enum)``
    members whose ``str()`` is ``"OrderStatus.FILLED"`` (Py3.11+ ``Enum.__str__``),
    NOT the value ``"filled"`` — so ``str(o.status).lower() == "filled"`` never
    matched and ``/recent-trades`` returned 0 fills despite open positions. Reading
    ``.value`` (enum) or falling back to the object itself (plain string) fixes
    both the status filter and the returned ``side``.
    """
    return str(getattr(x, "value", x)).lower()


# Sort-key fallback for a missing/None order timestamp. MUST be a tz-aware datetime (not "") — in
# Python 3 `sorted()` comparing a datetime against a str raises TypeError, which would fail-soft the
# /open-orders, /activities and /recent-trades endpoints to empty whenever ANY order lacks a stamp.
_SORT_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _order_to_trade(o: object) -> dict:
    """Map an Alpaca Order to the trade dict of /recent-trades and /activities."""
    filled_at = getattr(o, "filled_at", None) or getattr(o, "submitted_at", None)
    return {
        "id": str(getattr(o, "id", "")),
        "symbol": str(getattr(o, "symbol", "")),
        "side": _enum_value_lower(getattr(o, "side", "")),
        "qty": float(getattr(o, "filled_qty", None) or getattr(o, "qty", 0) or 0),
        "price": float(getattr(o, "filled_avg_price", None) or 0),
        "filled_at": str(filled_at) if filled_at else None,
    }


def _order_to_open_order(o: object) -> dict:
    """Map an Alpaca OPEN/working Order to the /open-orders dict (display-only, #2137).

    Distinct from _order_to_trade: an open order has no fill price yet, but carries its intent
    (order type + limit/stop) and its live status. All numeric fields are None-safe; qty keeps its
    fractional precision (the console renders it with fmtQty)."""

    def _num(v):
        return float(v) if v is not None else None

    submitted = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
    return {
        "id": str(getattr(o, "id", "")),
        "symbol": str(getattr(o, "symbol", "")),
        "side": _enum_value_lower(getattr(o, "side", "")),
        "qty": float(getattr(o, "qty", None) or 0),
        "type": _enum_value_lower(
            getattr(o, "order_type", None) or getattr(o, "type", "")
        ),
        "limit_price": _num(getattr(o, "limit_price", None)),
        "stop_price": _num(getattr(o, "stop_price", None)),
        "status": _enum_value_lower(getattr(o, "status", "")),
        "submitted_at": str(submitted) if submitted else None,
    }


@router.get(
    "/recent-trades",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_recent_trades(request: Request, limit: int = 20):
    """Return the last N filled orders from Alpaca (single 500-order window)."""
    try:
        api_client = await ar._resolve_orders_client(request)

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500)
        orders = await asyncio.to_thread(api_client.get_orders, req)
        filled = [
            o for o in orders if _enum_value_lower(getattr(o, "status", "")) == "filled"
        ]
        filled = sorted(
            filled,
            key=lambda o: getattr(o, "filled_at", None)
            or getattr(o, "submitted_at", None)
            or _SORT_EPOCH,
            reverse=True,
        )[:limit]

        trades = [_order_to_trade(o) for o in filled]
        return {"status": "success", "trades": trades}
    except Exception as e:
        logging.error("recent_trades failed: %s", e, exc_info=True)
        return {"status": "error", "trades": [], "message": "internal_error"}


@router.get(
    "/activities",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_activities(request: Request, max_orders: int = 10000):
    """Return the FULL filled-order history from Alpaca (paginated).

    Unlike /recent-trades (a single 500-order window), this pages backwards
    through get_orders until the oldest order, so no fill is silently dropped once
    the account exceeds 500 orders. ``truncated`` is true if the page-count safety
    cap was reached before the history was exhausted.
    """
    try:
        api_client = await ar._resolve_orders_client(request)

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        page_size = 500
        max_pages = max(1, (max_orders + page_size - 1) // page_size)
        seen: set = set()
        orders: list = []
        until = None
        truncated = True
        for _ in range(max_pages):
            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL, limit=page_size, until=until
            )
            page = await asyncio.to_thread(api_client.get_orders, req)
            fresh = [o for o in page if str(getattr(o, "id", "")) not in seen]
            for o in fresh:
                seen.add(str(getattr(o, "id", "")))
                orders.append(o)
            # A short page (or no new ids) means we reached the oldest order.
            if len(page) < page_size or not fresh:
                truncated = False
                break
            # Advance the cursor to the oldest order in this page and page again.
            until = min(
                (
                    getattr(o, "submitted_at", None)
                    for o in page
                    if getattr(o, "submitted_at", None) is not None
                ),
                default=None,
            )
            if until is None:
                truncated = False
                break

        filled = [
            o for o in orders if _enum_value_lower(getattr(o, "status", "")) == "filled"
        ]
        filled = sorted(
            filled,
            key=lambda o: getattr(o, "filled_at", None)
            or getattr(o, "submitted_at", None)
            or _SORT_EPOCH,
            reverse=True,
        )
        trades = [_order_to_trade(o) for o in filled]
        return {
            "status": "success",
            "trades": trades,
            "count": len(trades),
            "truncated": truncated,
        }
    except Exception as e:
        logging.error("activities failed: %s", e, exc_info=True)
        return {
            "status": "error",
            "trades": [],
            "count": 0,
            "truncated": False,
            "message": "internal_error",
        }


@router.get(
    "/open-orders",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_open_orders(request: Request):
    """Return the operator's currently OPEN / working orders from Alpaca (#2137, display-only).

    Mirrors /activities' tenant resolution (``_resolve_orders_client``) and fail-soft posture. Open
    orders are few, so a single OPEN-status window suffices (no pagination); newest first. Never
    raises → ``{status:"error", orders:[]}``, so a broker/engine hiccup can never blank the page.
    This is the console's "live working orders" view; it does NOT cancel or place orders.
    """
    try:
        api_client = await ar._resolve_orders_client(request)
        if not api_client:
            return {"status": "error", "orders": [], "count": 0}

        import asyncio

        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        req = GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=500)
        orders = await asyncio.to_thread(api_client.get_orders, req)
        orders = sorted(
            orders,
            key=lambda o: getattr(o, "submitted_at", None) or _SORT_EPOCH,
            reverse=True,
        )
        out = [_order_to_open_order(o) for o in orders]
        return {"status": "success", "orders": out, "count": len(out)}
    except Exception as e:
        logging.error("open-orders failed: %s", e, exc_info=True)
        return {
            "status": "error",
            "orders": [],
            "count": 0,
            "message": "internal_error",
        }


@router.get(
    "/recent-news",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_recent_news():
    return {
        "status": "success",
        "articles": getattr(ar.engine, "_recent_news_cache", [])[-50:],
    }


def _stock_history_days(range_key: str) -> int:
    r = (range_key or "1m").strip().lower()
    return {"1d": 2, "1w": 7, "1m": 30, "1y": 365, "max": 1825}.get(r, 30)


@router.get(
    "/stock-history",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_stock_history(symbol: str = "", period: str = "1m"):
    if not symbol or not symbol.strip():
        return {"status": "error", "message": "Missing symbol"}
    symbol = symbol.strip().upper()
    # C9/SEC M7 (#2375): validate ticker shape before it reaches the data provider's cache path
    # (os.path.join(DATA_CACHE_DIR, f"{symbol}.parquet")) — blocks path traversal. Same rule as
    # /force-cycle: starts alpha, alnum/./- only, <= 10 chars (no path separators possible).
    if not (
        1 <= len(symbol) <= 10
        and symbol[0].isalpha()
        and all(c.isalnum() or c in ".-" for c in symbol)
    ):
        return {
            "status": "error",
            "symbol": symbol,
            "message": "Invalid symbol",
            "data": [],
        }
    days = _stock_history_days(period)
    end_date = datetime.now(timezone.utc)
    try:
        df = ar.engine.data_provider.get_data(symbol, end_date, days=days)
        if df is None or df.empty:
            return {
                "status": "success",
                "symbol": symbol,
                "range": period,
                "data": [],
                "message": "No data",
            }
        df = df.sort_index()
        data = [
            {
                "date": idx.strftime("%Y-%m-%d"),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": int(row.get("volume", 0) or 0),
            }
            for idx, row in df.iterrows()
        ]
        return {
            "status": "success",
            "symbol": symbol,
            "range": period,
            "data": data,
        }  # noqa: E501
    except Exception as e:
        logging.error(
            "stock_history failed for %s: %s", symbol, e, exc_info=True
        )  # noqa: E501
        return {
            "status": "error",
            "symbol": symbol,
            "message": "internal_error",
            "data": [],
        }


def _json_safe(obj):
    """Coerce a value tree to JSON-native types for FastAPI response serialization.  # noqa: E501

    numpy.float32 does NOT subclass Python float, so FastAPI's jsonable_encoder cannot  # noqa: E501
    serialize it and raises AFTER the handler returns (outside its try/except) -> a bare  # noqa: E501
    HTTP 500. This walks dicts/lists, converts numpy scalars via ``.item()``, and maps  # noqa: E501
    non-finite floats (NaN/Inf) to None; JSON-native values pass through unchanged.  # noqa: E501
    Fixes the /portfolio-summary 500 when the strategy is active and enriches positions  # noqa: E501
    with numpy-derived scores (momentum/conviction/total_score).
    """
    import math

    import numpy as np

    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    return obj


def _safe_float(value: Any) -> Optional[float]:
    """Parse a broker field to a finite float, or None (#3421). Never raises —
    a missing/garbage/NaN quote must degrade to 'unknown', never abort the summary."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # math-free finite check (NaN != itself; ±inf compares equal to itself):
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _pct_or_none(value: Any) -> Optional[float]:
    """Alpaca ``change_today`` is a FRACTION (0.023 = +2.3%). Return it as a percent,
    or None when absent/unparseable (#3421). Pure; used by the positions passthrough."""
    f = _safe_float(value)
    return f * 100.0 if f is not None else None


@router.get(
    "/portfolio-summary",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_portfolio_summary(request: Request):  # noqa: C901
    try:
        active = getattr(ar.engine, "active_strategy", None)
        user_id = request.headers.get("X-User-Id")

        # Fallback to the engine's global client, but try to load the user's personal client  # noqa: E501
        api_client = ar.engine.api
        api_message = "No portfolio manager active"

        if user_id:
            try:
                wallet = await wallet_store.get_wallet(user_id)
                if wallet and wallet.get("secret_manager_id"):
                    tokens = oauth_secrets.get_tokens(
                        wallet["secret_manager_id"]
                    )  # noqa: E501
                    if tokens and tokens.get("access_token"):
                        is_paper = "paper" in ar.config.BASE_URL.lower()
                        api_client = ar.TradingClient(
                            oauth_token=tokens["access_token"], paper=is_paper
                        )
                        api_message = "Live personal account"
            except Exception as e:
                logging.error(
                    f"Failed to fetch multi-tenant API client for {user_id}: {e}"  # noqa: E501
                )

        # If we have a personal api_client, fetch exact positions & equity first.  # noqa: E501
        # This overrides the Bot's "portfolio manager" score-based positions,
        # because the user dashboard should reflect reality.
        real_positions = None
        real_equity = None
        # The REAL broker settled cash (Alpaca account.cash) — the authoritative Cash/margin figure
        # for the Overview. The console used to DERIVE cash as `equity − Σ market_value`, which is
        # wrong on a margin/short account (ignores buying power / unsettled funds). None when the
        # account probe fails → the console keeps its legacy derivation as a fallback.
        real_cash = None
        # PR-B: the real account currency (Alpaca account.currency, e.g. "USD"),
        # so the console shows the correct symbol instead of a hardcoded €.
        real_currency = "USD"
        if api_client:
            try:
                # Offload the synchronous Alpaca REST round-trips off the uvicorn event loop so a
                # slow broker call can't stall every other request (this is a read-only display path).
                acc = await asyncio.to_thread(api_client.get_account)
                positions = await asyncio.to_thread(api_client.get_all_positions)
                real_equity = float(acc.equity or 0)
                # account.cash can be legitimately negative (margin used) → don't coerce with `or 0`.
                # Isolated parse: a missing/unparseable cash must degrade to None (console then keeps
                # its legacy derivation), never abort the whole — otherwise-live — summary fetch.
                try:
                    _cash = getattr(acc, "cash", None)
                    real_cash = float(_cash) if _cash is not None else None
                except (TypeError, ValueError):
                    real_cash = None
                real_currency = getattr(acc, "currency", None) or "USD"
                api_message = (
                    "Live account" if not user_id else "Live personal account"
                )  # noqa: E501
                real_positions = [
                    {
                        "symbol": p.symbol,
                        "qty": float(p.qty),
                        "market_value": float(p.market_value or 0),
                        "unrealized_pnl": float(p.unrealized_pl or 0),
                        "unrealized_pnl_pct": (
                            float(p.unrealized_plpc or 0) * 100
                            if p.unrealized_plpc
                            else 0
                        ),
                        # #3421: today's price move (vs. yesterday's close) — Alpaca serves it on the
                        # SAME position object we already fetched, so this is pure passthrough (no
                        # extra call). change_today is a FRACTION → surface as percent; current_price
                        # lets the console show the live quote. Missing/unparseable ⇒ None (the
                        # console then shows just the price, never a fabricated move).
                        "current_price": _safe_float(getattr(p, "current_price", None)),
                        "change_today_pct": _pct_or_none(
                            getattr(p, "change_today", None)
                        ),
                    }
                    for p in positions
                ]
            except Exception as api_err:
                # #2980 (§5.6): a live-broker fetch failure is a fallback event —
                # operators must SEE it (WARNING), not have it swallowed at DEBUG.
                logging.warning("Live account fallback failed: %s", api_err)
                api_message = "Failed to fetch broker data"

        # Now merge with PortfolioManager insights if the bot is active
        if active and hasattr(active, "portfolio_manager"):
            pm = active.portfolio_manager
            if pm:
                summary = pm.get_portfolio_summary()
                debates = pm.get_debate_history(limit=5)
                rebalance_recs = pm.get_rebalance_recommendations()

                # Use real positions if available, otherwise fallback to bot's internal state  # noqa: E501
                if real_positions is not None:
                    # Enrich real positions with bot scores
                    for rp in real_positions:
                        if rp["symbol"] in pm._position_scores:
                            score = pm._position_scores[rp["symbol"]]
                            rp["total_score"] = score.total_score
                            rp["momentum_score"] = score.momentum_score
                            rp["conviction_score"] = score.conviction_score
                            rp["days_held"] = score.days_held
                    final_positions = real_positions
                    final_equity = real_equity
                else:
                    final_positions = [
                        {
                            "symbol": symbol,
                            "qty": score.qty,
                            "market_value": score.market_value,
                            "unrealized_pnl": score.unrealized_pnl,
                            "unrealized_pnl_pct": score.unrealized_pnl_pct,
                            "total_score": score.total_score,
                            "momentum_score": score.momentum_score,
                            "conviction_score": score.conviction_score,
                            "days_held": score.days_held,
                        }
                        for symbol, score in pm._position_scores.items()
                    ]
                    final_equity = None  # Bot scores don't represent total equity accurately  # noqa: E501

                total_pnl = (
                    sum(p.get("unrealized_pnl", 0) for p in final_positions)
                    if final_positions
                    else 0
                )
                return _json_safe(
                    {
                        "status": "success",
                        "currency": real_currency,
                        # Bug B (0.3.5): tag which account this snapshot belongs to, using the
                        # SAME source /health reports, so the mode-switch overlay can reject a
                        # stale cross-account in-flight poll.
                        "paper_trading": getattr(ar.config, "PAPER_TRADING", True),
                        "summary": summary,
                        "positions": final_positions,
                        "equity": final_equity,
                        "cash": real_cash,
                        "recent_debates": debates,
                        "rebalance_recommendations": rebalance_recs,
                        "agent_statuses": getattr(
                            ar.engine, "_last_round_table_state", []
                        ),
                        "message": api_message,
                        "total_unrealized_pnl": total_pnl,
                    }
                )

        if real_positions is not None:
            total_pnl = (
                sum(p.get("unrealized_pnl", 0) for p in real_positions)
                if real_positions
                else 0
            )
            return _json_safe(
                {
                    "status": "success",
                    "currency": real_currency,
                    "paper_trading": getattr(
                        ar.config, "PAPER_TRADING", True
                    ),  # Bug B (0.3.5)
                    "summary": None,
                    "positions": real_positions,
                    "equity": real_equity,
                    "cash": real_cash,
                    "agent_statuses": getattr(
                        ar.engine, "_last_round_table_state", []
                    ),  # noqa: E501
                    "message": api_message,
                    "total_unrealized_pnl": total_pnl,
                }
            )

        return _json_safe(
            {
                "status": "success",
                "currency": real_currency,
                "paper_trading": getattr(
                    ar.config, "PAPER_TRADING", True
                ),  # Bug B (0.3.5)
                "summary": None,
                "positions": [],
                "agent_statuses": getattr(
                    ar.engine, "_last_round_table_state", []
                ),  # noqa: E501
                "message": api_message,
                "total_unrealized_pnl": 0,
            }
        )
    except Exception as e:
        logging.error("portfolio_summary failed: %s", e, exc_info=True)
        # Bug B (0.3.5): tag even the error path with the account so a poll that fails during the
        # target engine's early boot cannot leave the mode-switch overlay's account-aware gate
        # without a tag → the reveal never wedges to the 90s honest timeout.
        return {
            "status": "error",
            "message": "internal_error",
            "paper_trading": getattr(ar.config, "PAPER_TRADING", True),
        }


# #2124: range -> (Alpaca period, timeframe). The daily /benchmark-equity path
# collapses each day to ONE point (base.py:_append_live_equity_to_benchmark), so
# a 1-day window is a 2-point straight line. This endpoint serves the INTRADAY
# equity curve at broker resolution for the Overview's short ranges. Longer
# ranges keep using /benchmark-equity (daily + S&P benchmark).
_INTRADAY_RANGES = {"1D": ("1D", "5Min"), "1W": ("1W", "1H")}


# #3189: bar width per timeframe, in seconds — the anchor point below is placed exactly one
# bar before the first bar, so it precedes the session strictly and the overnight gap draws
# as a step at the left edge instead of being averaged into the first bar.
_INTRADAY_BAR_SECONDS = {"5Min": 300, "1H": 3600}


@router.get(
    "/portfolio-intraday",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_portfolio_intraday(
    request: Request,
    range_: str = Query("1D", alias="range"),  # noqa: B008 — FastAPI query default
):
    """#2124: intraday account-equity curve so the Overview 1D/1W chart shows
    real movement instead of a single straight segment. Proxies Alpaca
    portfolio/history at 5Min (1D) / 1H (1W). Report-only, fail-soft to an empty
    curve — never a fabricated point; the daily path is untouched.

    Tenant-safe: the client is resolved per X-User-Id via ``_resolve_orders_client``
    (multi-tenant leak guard), and the blocking Alpaca call is offloaded with
    ``asyncio.to_thread`` so it never stalls the FastAPI event loop."""
    period, timeframe = _INTRADAY_RANGES.get(range_, _INTRADAY_RANGES["1D"])
    try:
        api_client = await ar._resolve_orders_client(request)
        if api_client is None:
            return {"points": []}
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        hist = await asyncio.to_thread(
            api_client.get_portfolio_history,
            GetPortfolioHistoryRequest(period=period, timeframe=timeframe),
        )
        if hist is None:
            # FINDING-01 (Archon #2124): explicit None guard for clearer diagnostics
            # than the getattr()-swallow path below. Fail-soft, never a fabricated point.
            logging.warning(
                "portfolio-intraday: hist response from Alpaca is None (range=%s)",
                range_,
            )
            return {"points": []}
        timestamps = list(getattr(hist, "timestamp", None) or [])
        equities = list(getattr(hist, "equity", None) or [])
        if not timestamps or len(timestamps) != len(equities):
            logging.warning(
                "portfolio-intraday: empty/misaligned history for range=%s", range_
            )
            return {"points": []}
        points = []
        for ts, eq in zip(timestamps, equities):
            if eq is None:
                continue
            iso = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
            points.append({"date": iso, "equity": round(float(eq), 2)})
        # #3189: the curve used to START at the session's first bar, so the overnight gap between
        # yesterday's CLOSE and today's OPEN fell out of every 1D/1W figure — on an account that
        # holds positions overnight, often the bulk of the move. The P&L tile meanwhile measures
        # `equity - lastEquity` against the previous daily close, which is why the chart and the
        # tile showed two different numbers for the same day/week (owner report 03.09.).
        # Alpaca ships the correct anchor in this very response: `base_value`, the basis its own
        # profit_loss is computed from. Prepend it as the first point — BEFORE the cash-flow
        # alignment and the pre-funding trim below, so a deposit still attaches to the equity jump
        # and the anchor is not mistaken for a leading unfunded point.
        # Only the window's LEFT edge was ever affected: the overnight jumps INSIDE a week already
        # sat between two consecutive hourly bars and were counted.
        # Fail-soft: an absent / non-positive base_value invents nothing.
        try:
            _base_value = float(getattr(hist, "base_value", None) or 0.0)
        except (TypeError, ValueError):
            _base_value = 0.0
        if points and _base_value > 0.0:
            _bar_s = _INTRADAY_BAR_SECONDS.get(timeframe, 300)
            _anchor_ts = int(timestamps[0]) - _bar_s
            points.insert(
                0,
                {
                    "date": datetime.fromtimestamp(
                        _anchor_ts, tz=timezone.utc
                    ).isoformat(),
                    "equity": round(_base_value, 2),
                },
            )
        # rc5 R7: an account funded two days ago still gets a FULL week back from
        # period=1W — the earlier hours report zero equity. Plotted, that is a flat line
        # at 0 plus a cliff, and it is the base the Overview hero measures its money
        # delta against ("+$11,776.22" beside a "-0.53 %" TWR, 01.09.). Drop the leading
        # pre-funding run; interior zeros (a liquidated account) survive.
        from core.engine.perf_metrics import trim_leading_unfunded

        points = trim_leading_unfunded(points)
        # Attach external cash-flows to the intraday points, exactly like /benchmark-equity does
        # for the daily curve — so the 1D/1W chart (a) strips a deposit from its return instead of
        # painting it as a +54 % "week", and (b) shows the SAME deposit/withdrawal marker the
        # monthly view has. Same source (_get_live_cashflows) + bucketing (a flow dated D lands on
        # the first point whose ISO timestamp >= D), keyed to the intraday timestamps.
        acct_cf = await asyncio.to_thread(ar._get_live_cashflows)
        if acct_cf:
            # #3049: anchor a settlement-lagged deposit to the equity JUMP (not its earlier
            # activity date), so the 1D/1W chart shows the marker + strips it instead of
            # painting "+1663% today". Equity-aware intraday variant of the daily bucketer.
            from core.engine.perf_metrics import bucket_cashflows_to_points_intraday

            buckets = bucket_cashflows_to_points_intraday(
                [p["date"] for p in points],
                [p["equity"] for p in points],
                acct_cf,
            )
            for p, cf in zip(points, buckets):
                if cf:
                    p["cashflow"] = round(cf, 2)
        # #3145: the performance baseline is time zero for EVERY served curve, not just the
        # daily one. Without this the 1D/1W chart and the Overview hero kept measuring across
        # history the user had explicitly cut away (baseline 01.09., older values still shown).
        # Sliced AFTER the cash-flow alignment, exactly like the daily path — a deposit whose
        # activity date precedes the baseline but settles inside the window attaches once.
        try:
            _intraday_baseline = await ar._get_performance_baseline()
        except Exception as _bl_err:  # noqa: BLE001 — display path never hard-fails
            logging.warning(
                "portfolio-intraday: baseline read failed (%s) — serving true inception",
                _bl_err,
                exc_info=True,
            )
            _intraday_baseline = None
        if _intraday_baseline:
            from core.engine.perf_metrics import slice_points_to_baseline

            points = slice_points_to_baseline(points, _intraday_baseline)
        return {"points": points, "baseline_date": _intraday_baseline}
    except Exception as exc:  # noqa: BLE001 — display path never hard-fails
        logging.warning(
            "portfolio-intraday failed (range=%s): %s", range_, exc, exc_info=True
        )
        return {"points": []}
