"""Agenten-Router (#4072, ARC-E6 G-4f).

Round Table (``/round-table-decisions``, ``/round-table/{symbol}``), Specialist-Reports
(``/specialist-report/{symbol}``, ``/specialist-reports``), Chat (``/chat``), Simulation und
Lernlauf (``/run-simulation``, ``/simulation-result``, ``/run-learning``) sowie die v1/v2-Routen
(``/api/v1/engine/force-cycle``, ``/api/v2/universe``): alles, was auf die Agenten-Schicht
zugreift. Die Routen und ``_serialize_specialist_report`` sind unveraendert aus
``api_routes.py`` umgezogen; die HTTP-Flaeche bleibt gleich
(``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und ueber ``api_routes`` gepatchte Namen (``engine``, ``config``,
``bump_usage``, ``logger``, ``answer_chat_with_fallback``, ``boot_xai_runtime``,
``answer_via_xai``) liest das Modul zur Laufzeit ueber ``ar`` (Zugriffsregel,
``core/engine/routes/__init__.py``), damit Patches auf ``core.engine.api_routes.<name>``
weiter greifen und der Logger-Name ``core.engine.api_routes`` gleich bleibt.

Plan: ``docs/4072-g4f-agenten-router/implementation_plan.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import require_engine_key, verify_user_id_sig
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.xai.agent_core import is_agent_core_enabled

router = APIRouter()
ROUTER.append(router)


@router.post("/run-simulation")
async def run_sim(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    # Defense-in-depth: the Simulation/backtest page is entitlement-gated (central switch
    # core/entitlement/tier.py: simulation_enabled=False for all tiers while the backtest
    # runtime is hardened). Refuse a direct POST when disabled so the thread never starts.
    # Mirrors the /api/live/enable allow_live gate; no-op once a tier re-enables it.
    from core.entitlement import resolve_entitlement

    if not resolve_entitlement().simulation_enabled:
        raise HTTPException(
            status_code=403,
            detail="[Entitlement] the Simulation/backtest page is disabled for this edition.",
        )

    symbol_sample_mode = p.get("symbol_sample_mode", "full_market")
    ar.engine.run_simulation_in_thread(
        p["start_date"],
        p["end_date"],
        p["initial_capital"],
        symbol_sample_mode,  # noqa: E501
    )
    return {"status": "success"}


@router.get("/simulation-result")
async def simulation_result(_: None = Depends(require_engine_key)):  # noqa: B008
    """SIM-1 T1 (#1484): the last backtest result — the Console's reload-safe poll target.  # noqa: E501

    Dual-Design Option B: rather than rely on the ephemeral ``simulation_status`` event stream, the  # noqa: E501
    Console polls this until done, so a page reload mid-backtest still recovers the status/result.  # noqa: E501
    Returns ``{"status":"running"}`` while a sim runs, the stored result when complete, else idle.  # noqa: E501
    """
    if getattr(ar.engine, "is_simulation", False):
        return {"status": "running"}
    return getattr(ar.engine, "last_simulation_result", None) or {
        "status": "idle"
    }  # noqa: E501


@router.post("/run-learning")
async def run_learn(p: Dict, _: None = Depends(require_engine_key)):  # noqa: B008
    ar.engine.run_learning_in_thread(
        p["start_date"], p["end_date"], p["initial_capital"]
    )  # noqa: E501
    return {"status": "success"}


# --- Epic INF-9: E2E Testing Force Cycle ---


@router.post("/api/v1/engine/force-cycle")
async def force_cycle(
    p: Dict, request: Request, _: None = Depends(require_engine_key)  # noqa: B008
):
    """
    Synchronous 'Force Cycle' for end-to-end testing (Audit Gates).
    Evaluates a single symbol deterministically on a specific past target_date.
    Returns the generated session_id and the strategy's signal.
    """
    # SEC M7: validate the caller-supplied symbol (defense-in-depth). The endpoint is  # noqa: E501
    # require_engine_key + localhost, but an unbounded/crafted string must never reach  # noqa: E501
    # the data provider. Ticker shape: starts alpha, then alnum/./-, <= 10 chars.  # noqa: E501
    symbol = str(p.get("symbol", "AAPL")).strip().upper()
    if not (
        1 <= len(symbol) <= 10
        and symbol[0].isalpha()
        and all(c.isalnum() or c in ".-" for c in symbol)
    ):
        raise HTTPException(status_code=422, detail="Invalid symbol.")
    target_date = p.get("target_date")

    if not target_date:
        target_date = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()  # noqa: E501

    if not ar.engine or not ar.engine.data_provider:
        raise HTTPException(
            status_code=503, detail="Engine/DataProvider not available."
        )

    # PR F: anonymous operator-action counter (additive, fail-safe — never alters the cycle).  # noqa: E501
    ar.bump_usage("force_cycles")

    # Fetch T-1 (or target_date) exact daily close (determinism!)
    end_dt = datetime.fromisoformat(target_date)
    if not end_dt.tzinfo:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

    try:
        # Request a chunk of data, ending exactly at T-1
        df = ar.engine.data_provider.get_data(symbol, end_dt, days=10)
        if df is None or df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No historical data found for {symbol}.",  # noqa: E501
            )

        latest_row = df.iloc[-1]
        mock_ohlc = {
            "open": float(latest_row["open"]),
            "high": float(latest_row["high"]),
            "low": float(latest_row["low"]),
            "close": float(latest_row["close"]),
            "volume": float(latest_row["volume"]),
            "timestamp": end_dt.isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Data Provider Error: {e}"
        )  # noqa: E501

    # MiFID II Audit Log
    api_key_header = request.headers.get("x-bot-api-key", "")
    key_ident = (
        api_key_header[:6] + "..."
        if api_key_header and len(api_key_header) > 6
        else "internal"
    )
    logging.info(
        f"MiFID Audit: Force Cycle triggered for {symbol} at target datum {target_date}. Caller Key: {key_ident}"  # noqa: E501
    )

    import uuid

    from core.orchestration.graph import build_symbol_eval_graph

    # Instantiate the LangGraph evaluator with mocked input
    state = {
        "symbol": symbol,
        "ohlc": mock_ohlc,
        "market_regime": getattr(ar.engine, "cached_regime", {"regime": "bull"}),
        "_is_simulation": True,  # Prevent actual execution
        "error": None,
    }

    graph = build_symbol_eval_graph()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Run graph evaluation (T-1 deterministic input)
        final_state = await graph.ainvoke(state, config)
    except Exception as e:
        return {"status": "error", "message": f"LangGraph Error: {e}"}

    session_id = final_state.get("session_id")
    signal_obj = final_state.get("signal")
    signal_action = (
        getattr(signal_obj, "action", "NONE") if signal_obj else "NONE"
    )  # noqa: E501

    return {
        "status": "success",
        "session_id": session_id,
        "signal": signal_action,
        "target_date": target_date,
        "timestamp": mock_ohlc["timestamp"],
        "close_price": mock_ohlc["close"],
        "round_table_scores": final_state.get("round_table_scores", []),
    }


# --- Chat ---


@router.post(
    "/chat",
    dependencies=[
        Depends(require_engine_key),
        Depends(verify_user_id_sig),
    ],  # noqa: E501
)
async def chat(p: Dict):
    message = (p.get("message") or "").strip()
    if not message:
        return {
            "reply": "Please ask a question.",
            "message": "Please ask a question.",
        }  # noqa: E501
    try:
        # XAI-T9a (#1401): when the glass-box core is enabled (OSS desktop sets
        # XAI_AGENT_CORE), route through the 4-domain router. The core is built per call so  # noqa: E501
        # the live engine.specialist_registry (set later, and only when the registry is  # noqa: E501
        # enabled) is always current — a core cached at warm-up would pin stock_research to  # noqa: E501
        # an empty registry. On any XAI-path error, fall through to the legacy chat so the  # noqa: E501
        # flag-on path is never worse than flag-off. A dormant core (flag off, the default)  # noqa: E501
        # yields None, keeping the legacy path byte-identical.
        if is_agent_core_enabled():
            try:
                core = ar.boot_xai_runtime(
                    specialist_registry=getattr(
                        ar.engine, "specialist_registry", None
                    )  # noqa: E501
                )
                xai_reply = await ar.answer_via_xai(message, core=core)
                if xai_reply is not None:
                    return {"reply": xai_reply, "message": xai_reply}
            except Exception:
                logging.exception(
                    "XAI chat path failed — falling back to legacy chat."
                )  # noqa: E501
        context = ar.engine.get_chat_context()
        reply = ar.answer_chat_with_fallback(context, message)
        if not reply:
            reply = "I couldn't generate an answer right now."
        return {"reply": reply, "message": reply}
    except Exception as e:
        logging.error("Chat failed: %s", e, exc_info=True)
        reply = "Sorry — I couldn't process that right now. Please try again."
        return {"reply": reply, "message": "internal_error"}


# ---------------------------------------------------------------------------
# Symbol Universe API (Task #361 — OTel Gherkin requirement)
# ---------------------------------------------------------------------------


@router.get(
    "/api/v2/universe",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_symbol_universe(request: Request):
    """Return the current symbol universe used by the trading engine.

    Task #361 Gherkin:
      Given  a request to /api/v2/universe
      When   the OTel SDK is initialised as the very first import
      Then   every Span must carry: db.statement, user.id, response.body_length,  # noqa: E501
             service.version (Git SHA)

    Span attributes are added automatically by OtelSpanMiddleware.
    user.id is read from the X-User-Id header injected by serve_public_api.py.
    service.version comes from the GIT_COMMIT env var via core.telemetry.
    """
    from core.telemetry import get_service_version

    if ar.engine is None:
        return {
            "status": "starting",
            "symbols": [],
            "count": 0,
            "service_version": get_service_version(),
            "message": "Engine is still initialising — retry shortly.",
        }

    # Prefer the live universe from the last market scan
    symbols: list[str] = list(getattr(ar.engine, "_last_top_picks", []))

    # Fall back to the watchlist / configured symbols if scan hasn't run yet
    if not symbols and hasattr(ar.engine, "symbols"):
        symbols = list(ar.engine.symbols or [])

    return {
        "status": "ok",
        "symbols": symbols,
        "count": len(symbols),
        "service_version": get_service_version(),
    }


# ── G1b (#1050): desktop-console read-only routes ──────────────────────────
# Ported from the production bundle (DTO key-set frozen as a fixture, pinned
# by tests/unit/test_g1b_console_routes.py). Round-Table nomenclature per the
# maintainer directive — the bundle's /senate-* paths are NOT carried over.
# Read-only display layer: specialist_registry (None → documented empty state
# while disabled on main) and the G1a recent-decisions store. No order-path
# access.


def _serialize_specialist_report(sym: str, r, *, full: bool = False) -> Dict:
    """Bundle-DTO-compatible report serialization (exact key-set contract).

    Fields main's SpecialistReport does not carry yet (about/company_summary/
    edge_signals/headlines — insight-quality features still bundle-only) are
    emitted with empty defaults so the console contract holds.

    REPORTS-UX Wave 1 (#2710): ``full=False`` (the default, used by the bulk
    /specialist-reports list) keeps the note-body char caps so a ~500-symbol
    payload stays light — byte-identical to before. ``full=True`` (the
    per-symbol GET /specialist-report/{symbol}) serves the body UNCAPPED so the
    expanded reader never truncates.
    """

    def _round_or_none(value, digits):
        return round(value, digits) if value is not None else None

    updated_at = getattr(r, "updated_at", None)
    # PR-review P0-1: NEVER use `or` as numeric fallback — 0.0 is a legitimate,
    # maximally-bearish sentiment and `0.0 or 50.0` would silently mask it as
    # neutral (financial edge-case governance rule).
    sentiment = getattr(r, "sentiment_score", None)
    insider_total = getattr(r, "insider_trades_total", None)
    # P0-1 (T-SER #1266): preserve the historical 0.0-when-absent fallback but stop  # noqa: E501
    # `or`-masking a legitimate 0.0 confidence (0.0 is a real value, not a default).  # noqa: E501
    confidence = getattr(r, "confidence", None)
    # #2587 dead-source honesty: names from _SPECIALIST_SOURCE_NAMES whose fetch
    # FAILED this cycle (HTTP error / exception), as opposed to "checked and found
    # nothing". Their counts serialize as null so the card renders "—" instead of a
    # fabricated 0. Legacy reports without the field keep exact int counts.
    unavailable = set(getattr(r, "sources_unavailable", None) or [])
    result = {
        "symbol": sym,
        "sentiment_score": round(
            sentiment if sentiment is not None else 50.0, 1
        ),  # noqa: E501
        "recommendation": getattr(r, "recommendation", "hold"),
        "confidence": round(confidence, 3) if confidence is not None else 0.0,
        "escalate": bool(getattr(r, "escalate", False)),
        "escalate_reason": getattr(r, "escalate_reason", "") or "",
        "reasons": (getattr(r, "reasons", None) or [])[:5],
        "about": (
            getattr(r, "about", "")
            or getattr(r, "company_summary", "")
            or f"{sym}: overview unavailable this cycle."
        )[:900],
        "company_summary": getattr(r, "company_summary", "") or "",
        "edge_signals": getattr(r, "edge_signals", None) or [],
        "investment_thesis": (getattr(r, "investment_thesis", "") or "")[
            :1500
        ],  # noqa: E501
        "bull_case": (getattr(r, "bull_case", "") or "")[:1000],
        "bear_case": (getattr(r, "bear_case", "") or "")[:1000],
        "news_summary": (getattr(r, "news_summary", "") or "")[:1500],
        "headlines": (getattr(r, "headlines", None) or [])[:8],
        "alternative_signals": (getattr(r, "alternative_signals", "") or "")[
            :800
        ],  # noqa: E501
        "insider_trades_count": (
            insider_total
            if insider_total is not None
            else len(getattr(r, "insider_trades", None) or [])
        ),
        "political_trades_count": (
            None
            if "congressional_trades" in unavailable
            else len(getattr(r, "political_trades", None) or [])
        ),
        "material_events_count": len(
            getattr(r, "material_events", None) or []
        ),  # noqa: E501
        "reddit_mentions": (
            None
            if "reddit_mentions" in unavailable
            else getattr(r, "reddit_mentions", 0) or 0
        ),
        "wiki_spike": bool(getattr(r, "wiki_spike", False)),
        "short_interest_pct": getattr(r, "short_interest_pct", None),
        "updated_at": updated_at.isoformat() if updated_at else None,
        "ml_direction": getattr(r, "ml_direction", "unavailable"),
        "ml_confidence": _round_or_none(getattr(r, "ml_confidence", None), 3),
        "ml_base_return_pct": _round_or_none(
            getattr(r, "ml_base_return_pct", None), 2
        ),  # noqa: E501
        "ml_bear_return_pct": _round_or_none(
            getattr(r, "ml_bear_return_pct", None), 2
        ),  # noqa: E501
        "ml_bull_return_pct": _round_or_none(
            getattr(r, "ml_bull_return_pct", None), 2
        ),  # noqa: E501
        "signal_quality": getattr(r, "signal_quality", "llm_only"),
        "walkforward_ic": _round_or_none(
            getattr(r, "walkforward_ic", None), 3
        ),  # noqa: E501
        "walkforward_sharpe": _round_or_none(
            getattr(r, "walkforward_sharpe", None), 2
        ),  # noqa: E501
        "ml_attention_features": getattr(r, "ml_attention_features", None)
        or [],  # noqa: E501
        # Group-B keys (T-SER #1266) - additive (+7). Producers: T2 (pros/cons/
        # summary), T6a (data_quality/degraded), later TA stage (rsi_14/macd_signal).  # noqa: E501
        # data_quality uses _round_or_none NOT `or` - 0.0 is a legitimate low-integrity  # noqa: E501
        # value (P0-1). summary capped [:1500] like news_summary/investment_thesis.  # noqa: E501
        "pros": getattr(r, "pros", None) or [],
        "cons": getattr(r, "cons", None) or [],
        "summary": (getattr(r, "summary", "") or "")[:1500],
        "data_quality": _round_or_none(getattr(r, "data_quality", 1.0), 3),
        "degraded": bool(getattr(r, "degraded", False)),
        "rsi_14": _round_or_none(getattr(r, "rsi_14", None), 1),
        "macd_signal": getattr(r, "macd_signal", None),
    }
    # RPAR-1 (#1262) Abschluss / #1490: deterministic, bundle-free report-quality badge. Additive  # noqa: E501
    # keys ONLY when enabled -> the exact key-set contract + BORA byte-identity hold with flag OFF.  # noqa: E501
    if getattr(ar.config.get_config(), "REPORT_QUALITY_BADGE_ENABLED", False):
        from core.specialist.report_quality import compute_report_quality

        score, label = compute_report_quality(r)
        result["report_quality"] = score
        result["report_quality_label"] = label
    # RPT-GOLD (#1998): the full auditable Markdown note + mechanical audit verdict.
    # Additive keys ONLY when the report generator flag is ON -> the exact key-set
    # contract + BORA byte-identity hold with the flag OFF (same pattern as the
    # report-quality badge above). getattr defaults keep this safe even before the
    # SpecialistReport DTO carries the fields (RPT-6 #2075).
    if getattr(ar.config.get_config(), "REPORT_GENERATOR_V2_ENABLED", False):
        result["report_markdown"] = getattr(r, "report_markdown", None)
        result["report_audit_ok"] = getattr(r, "report_audit_ok", None)
        result["report_audit_summary"] = getattr(r, "report_audit_summary", None)
        # The note's own cross-sectional verdict ("buy"/"hold"/"sell" or None when it
        # abstains). The reader badge reads THIS, not `recommendation` above — so an
        # abstaining note (report-only path leaves `recommendation` at the "hold"
        # default) surfaces null and the pill shows nothing. Additive + flag-gated,
        # so the key-set contract + BORA byte-identity hold with the flag OFF.
        result["report_recommendation"] = getattr(r, "report_recommendation", None)
    # Rich-synthesis card (Direction A): the grounding citations + dropped-claim
    # ledger + non-directional HAR-RV volatility band. Additive keys ONLY when the
    # card flag is ON -> the exact key-set contract + BORA byte-identity hold with
    # the flag OFF. The flag is hard-coupled to SPECIALIST_GROUNDING_ENABLED (config
    # _enforce_card_grounding), so ungrounded prose can never reach these keys.
    # P0-1: vol-band values use getattr defaults, NEVER `or` — a 0.0 base is real.
    if getattr(ar.config.get_config(), "SPECIALIST_SYNTHESIS_CARD_ENABLED", False):
        result["synthesis_citations"] = getattr(r, "synthesis_citations", None) or []
        result["synthesis_dropped"] = getattr(r, "synthesis_dropped", None) or []
        result["grounding_ok"] = getattr(r, "grounding_ok", None)
        result["vol_band_low_pct"] = _round_or_none(
            getattr(r, "vol_band_low_pct", None), 2
        )
        result["vol_band_base_pct"] = _round_or_none(
            getattr(r, "vol_band_base_pct", None), 2
        )
        result["vol_band_high_pct"] = _round_or_none(
            getattr(r, "vol_band_high_pct", None), 2
        )
        result["vol_band_sigma_pct"] = _round_or_none(
            getattr(r, "vol_band_sigma_pct", None), 2
        )
        result["scenario_basis"] = getattr(r, "scenario_basis", "") or ""
        # Card model view: the LSTM cross-section standing — THE single rank
        # definition (core/report/lstm_panel_store.cross_section_standing), the
        # same one the auditable report and the Round-Table LSTM vote read, so
        # the card can never tell the user a standing the board didn't see.
        # Absent from the panel -> honest None triple (abstain, never guess).
        # #2587: the store method is ``cross_section_at`` (``cross_section`` lives
        # only on the EngineLstmPanelReader adapter). The old call raised
        # AttributeError into the except below -> permanent "—" on the coverage
        # card while the Round-Table verdict (agents.py, cross_section_at) showed
        # the rank on the same page.
        # #2680: read the ONE ranking seam (``active_cross_section``) — when the
        # smoothing flag is on, the board votes on the trend table, so the card MUST
        # show the same basis or it re-opens exactly the divergence #2587 closed.
        try:
            from core.report.lstm_panel_store import (
                active_cross_section,
                cross_section_standing,
                get_store,
            )

            _xsec = active_cross_section(get_store(), datetime.now(timezone.utc))
            _pct, _rank, _n = cross_section_standing(_xsec, r.symbol)
        except Exception as _exc:  # noqa: BLE001 — panel unavailable = abstain
            ar.logger.warning(
                "Specialist synthesis card panel unavailable for %s: %s", r.symbol, _exc
            )
            _pct, _rank, _n = None, None, None
        result["lstm_xsec_percentile"] = _round_or_none(_pct, 1)
        result["lstm_xsec_rank"] = _rank
        result["lstm_xsec_universe"] = _n
        # OWN-MODEL price range (HAR-RV on last close) + hard price stats.
        result["last_close"] = _round_or_none(getattr(r, "last_close", None), 2)
        result["price_band_low"] = _round_or_none(getattr(r, "price_band_low", None), 2)
        result["price_band_high"] = _round_or_none(
            getattr(r, "price_band_high", None), 2
        )
        result["chg_20d_pct"] = _round_or_none(getattr(r, "chg_20d_pct", None), 1)
        result["dist_52w_high_pct"] = _round_or_none(
            getattr(r, "dist_52w_high_pct", None), 1
        )
    # RQ-1 B2 (#1522): per-source freshness ("as of" + stale badge). Additive keys ONLY when  # noqa: E501
    # enabled -> exact key-set / BORA byte-identity hold with the flag OFF (same contract as  # noqa: E501
    # the report-quality badge above). "as of" = newest filing date per source; data_stale  # noqa: E501
    # flips when even the freshest source is older than the SLA. From the report's own lists.  # noqa: E501
    if getattr(ar.config.get_config(), "SPECIALIST_FRESHNESS_ENABLED", False):

        def _as_of(filings):
            dates = [
                f.get("filed", "")
                for f in (filings or [])
                if isinstance(f, dict) and f.get("filed")
            ]
            return max(dates) if dates else None

        _ins = _as_of(getattr(r, "insider_trades", None))
        _evt = _as_of(getattr(r, "material_events", None))
        _act = _as_of(getattr(r, "activist_stakes", None))
        _newest = max([d for d in (_ins, _evt, _act) if d], default=None)
        _sla = int(
            getattr(ar.config.get_config(), "SPECIALIST_FRESHNESS_SLA_DAYS", 30)
        )  # noqa: E501
        _cutoff = (datetime.now(timezone.utc) - timedelta(days=_sla)).strftime(
            "%Y-%m-%d"
        )
        result["insider_as_of"] = _ins
        result["material_events_as_of"] = _evt
        result["activist_as_of"] = _act
        result["data_stale"] = bool(_newest) and _newest < _cutoff
    if full:
        # REPORTS-UX Wave 1 (#2710): serve the note body UNCAPPED for the
        # per-symbol reader so it never truncates. These keys already exist above
        # (capped); here they are overwritten with the full value. The list path
        # (full=False, the default) is untouched -> byte-identical DTO + BORA parity.
        result["about"] = (
            getattr(r, "about", "")
            or getattr(r, "company_summary", "")
            or f"{sym}: overview unavailable this cycle."
        )
        result["investment_thesis"] = getattr(r, "investment_thesis", "") or ""
        result["bull_case"] = getattr(r, "bull_case", "") or ""
        result["bear_case"] = getattr(r, "bear_case", "") or ""
        result["news_summary"] = getattr(r, "news_summary", "") or ""
        result["alternative_signals"] = getattr(r, "alternative_signals", "") or ""
        result["summary"] = getattr(r, "summary", "") or ""
    return result


@router.get("/specialist-report/{symbol}")
async def get_specialist_report(
    symbol: str,
    fields: Optional[str] = None,
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """REPORTS-UX Wave 1 (#2710): the FULL, untruncated report for one symbol.

    The bulk /specialist-reports list caps the note body (a ~500-symbol payload
    must stay light); this per-symbol route serves it UNCAPPED (full=True) so the
    expanded reader never truncates. ``?fields=summary`` returns a light
    projection (no body) for cheap row-gating. 404 when the symbol has no cached
    report; documented empty-state when the registry is disabled (G1 pattern).
    """
    registry = getattr(ar.engine, "specialist_registry", None)
    if registry is None:
        return {
            "status": "unavailable",
            "message": "StockSpecialistRegistry not running on this deployment.",  # noqa: E501
            "report": None,
        }
    r = registry.get_report(symbol.upper())
    if r is None:
        raise HTTPException(
            status_code=404, detail=f"No cached report for {symbol.upper()}."
        )
    if fields == "summary":
        _ua = getattr(r, "updated_at", None)
        _conf = getattr(r, "confidence", None)
        return {
            "status": "ok",
            "report": {
                "symbol": symbol.upper(),
                "recommendation": getattr(r, "recommendation", "hold"),
                "confidence": round(_conf, 3) if _conf is not None else 0.0,
                "escalate": bool(getattr(r, "escalate", False)),
                "updated_at": _ua.isoformat() if hasattr(_ua, "isoformat") else None,
                "data_stale": bool(getattr(r, "data_stale", False)),
                "report_quality": getattr(r, "report_quality", None),
            },
        }
    return {
        "status": "ok",
        "report": _serialize_specialist_report(symbol.upper(), r, full=True),
    }


@router.get("/specialist-reports")
async def get_specialist_reports(_: None = Depends(require_engine_key)):  # noqa: B008
    """Cached SpecialistReports for the desktop console cards.

    Documented empty state (G1 spec): while the StockSpecialistRegistry is
    disabled on main (`base.py _init_specialist_registry` → None), this
    returns 200 with an empty list — a stable API contract before data flows.
    """
    registry = getattr(ar.engine, "specialist_registry", None)
    if registry is None:
        return {
            "status": "unavailable",
            "message": "StockSpecialistRegistry not running on this deployment.",  # noqa: E501
            "reports": [],
            "registry_status": {},
        }
    all_reports = registry.get_all_reports()
    escalations = registry.get_escalations()
    status_info = registry.get_status()
    reports_out = [
        _serialize_specialist_report(sym, r)
        for sym, r in sorted(all_reports.items())  # noqa: E501
    ]
    return {
        "status": "ok",
        "total": len(reports_out),
        "escalations": len(escalations),
        "registry_status": status_info,
        "reports": reports_out,
    }


@router.get("/round-table-decisions")
async def get_round_table_decisions_route(
    _: None = Depends(require_engine_key),  # noqa: B008
):
    """Latest Round-Table decision per symbol (newest first, G1a store)."""
    from core.round_table.recent_decisions import (  # noqa: E501
        get_recent_round_table_decisions,
    )

    decisions = get_recent_round_table_decisions(limit=200)
    return {"status": "ok", "total": len(decisions), "decisions": decisions}


@router.get("/round-table/{symbol}")
async def get_round_table_for_symbol(
    symbol: str, _: None = Depends(require_engine_key)  # noqa: B008
):
    """Latest Round-Table verdict for one symbol, in the console's senators
    shape. Error-shaped (but 200) with empty senators when no decision exists
    — never raises."""
    from core.round_table.recent_decisions import get_round_table_decision

    sym = (symbol or "").strip().upper()
    empty = {"status": "error", "symbol": sym, "senators": [], "score": None}
    if not sym:
        return empty
    latest = get_round_table_decision(sym)
    if latest is None:
        return empty
    senators = []
    for v in latest.get("votes", []) or []:
        signal = str(v.get("signal", "")).upper()
        vote = {"BUY": "BULL", "SELL": "BEAR", "HOLD": "ABSTAIN"}.get(
            signal, "ABSTAIN"
        )  # noqa: E501
        senators.append(
            {
                "name": str(v.get("agent_name") or v.get("name") or ""),
                "vote": vote,
                "score": v.get("score"),
                "weight": v.get("weight"),
                "reasoning": v.get("reasoning") or "",
                "vetoed": bool(v.get("vetoed", False)),
            }
        )
    return {
        "status": "ok",
        "symbol": sym,
        "senators": senators,
        "score": latest.get("consensus_score"),
        "signal_action": latest.get("signal_action"),
        "timestamp": latest.get("timestamp"),
    }
