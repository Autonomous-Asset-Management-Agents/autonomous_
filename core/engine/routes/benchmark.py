"""Benchmark-Router (#4074 G-4h, #4075 G-4i; ARC-E6).

``GET /benchmark-equity``: die Konto-Kurve gegen SPY, einzahlungsbereinigt (TWR), mit
waehlbarer Basis (#3071). Die Route liest nur und platziert keine Order. G-4h zog sie unveraendert
aus ``api_routes.py`` hierher; G-4i zerlegte ihren Rumpf wortgleich in Schritte
(``routes/benchmark_schritte.py``). Hier bleibt der Dirigent: Er liest den Cache, entscheidet mit
der unveraenderten Bedingung den Pfad und fuehrt dessen Schritte aus.

Dazu die restlichen Benchmark-Routen, seit G-4i unveraendert aus ``api_routes.py`` hier:
``GET``/``POST /api/performance-baseline`` (#3071) und ``POST /run-benchmark``. Die HTTP-Flaeche
bleibt gleich (``tests/unit/test_api_flaeche_schnappschuss.py``).

Geteilten Zustand und geteilte Helfer (``engine``, ``config``, ``RedisClient``,
``_get_live_cashflows``, ``_get_performance_baseline``, ``PERF_BASELINE_KEY``) liest das Modul zur
Laufzeit ueber ``ar`` (Zugriffsregel, ``core/engine/routes/__init__.py``). Die eigenen Helfer
liegen in ``routes/benchmark_daten.py`` und werden ueber das Modulobjekt ``_bd`` gelesen, damit
ein Patch auf ``core.engine.routes.benchmark_daten.<name>`` greift.

Plaene: ``docs/4074-g4h-benchmark-kurve/implementation_plan.md``,
``docs/4075-g4i-benchmark-kurve-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_engine_key, verify_user_id_sig
from core.engine import api_routes as ar
from core.engine.routes import ROUTER
from core.engine.routes import benchmark_schritte as _bs

router = APIRouter()
ROUTER.append(router)


@router.get(
    "/benchmark-equity",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_benchmark_equity():
    try:
        z = _bs.BenchmarkKurve()
        await _bs._schritt_cache_lesen(z)
        # Rebuild fully from the DB when the cache is absent OR "thin" OR wrong-mode (stale).
        # _append_live_equity_to_benchmark (core/engine/base.py) warms this key every engine cycle
        # with `points` only (no initial_capital / spy_points), which otherwise pre-empts this
        # reconstruction — so the handler returned a points-only cache forever: NO S&P line + a
        # history truncated to base.py's per-cycle appends. A missing initial_capital marks a thin
        # cache -> rebuild.
        # #1957 (Archon finding 5 — addressed differently): a legacy cache lacks twr_pct. Rather
        # than force a rebuild, the cached-return path below recomputes the cash-flow-adjusted KPIs
        # from `points` on EVERY read, so the response always carries a fresh twr_pct regardless of
        # what the cache holds — strictly better than a one-time cache invalidation (no stale
        # window, no cache churn, and it never fights the warm-cache spy self-heal).
        if (
            not z._cached
            or not z._cached.get("initial_capital")
            or z._cache_mode_stale
            or z._cache_baseline_stale
        ):
            # Die Schritte je Pfad, in der Reihenfolge des alten Rumpfs (Plan §1). Zur Laufzeit
            # ueber ``_bs`` gelesen, damit ein Patch auf einen Schritt greift.
            schritte = (
                _bs._schritt_live_kurve,
                _bs._schritt_snapshots_laden,
                _bs._schritt_ohne_snapshots,
                _bs._schritt_punkte_bauen,
                _bs._schritt_spy_kennzahlen,
            )
        else:
            schritte = (
                _bs._schritt_cache_heute,
                _bs._schritt_cache_spy,
                _bs._schritt_cache_antwort,
            )
        for schritt in schritte:
            ergebnis = await schritt(z)
            if ergebnis is not _bs._WEITER:
                return ergebnis
    except Exception as e:
        logging.error("benchmark_equity failed: %s", e, exc_info=True)
        return {"points": [], "spy_points": [], "message": "internal_error"}


@router.get(
    "/api/performance-baseline",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def get_performance_baseline():
    """#3071: the persisted performance baseline (null = true inception)."""
    return {"baseline_date": await ar._get_performance_baseline()}


@router.post(
    "/api/performance-baseline",
    dependencies=[Depends(require_engine_key), Depends(verify_user_id_sig)],
)
async def set_performance_baseline(body: dict):
    """#3071: persist (or clear, with ``{"baseline_date": null}``) the baseline date.

    Timestamped ``system_config`` upsert → the choice is auditable (fair-presentation
    guardrail: the console always labels the chosen base, reports carry both figures).
    """
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    raw = (body or {}).get("baseline_date")
    if raw is not None:
        try:
            raw = datetime.strptime(str(raw)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=422, detail="baseline_date must be YYYY-MM-DD or null"
            )
    async with AsyncSessionLocal() as session:
        row = (
            (
                await session.execute(
                    sa.select(SystemConfig).filter_by(config_key=ar.PERF_BASELINE_KEY)
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            session.add(
                SystemConfig(
                    config_key=ar.PERF_BASELINE_KEY,
                    config_value={"baseline_date": raw},
                    updated_at=datetime.now(timezone.utc),
                )
            )
        else:
            row.config_value = {"baseline_date": raw}
            row.updated_at = datetime.now(timezone.utc)
        await session.commit()
    # The benchmark cache is keyed with baseline_date — the next poll's stale-check
    # (baseline mismatch) forces a full rebuild, so no explicit invalidation is needed.
    return {"baseline_date": raw}


def _last_trading_day():
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


@router.post("/run-benchmark")
async def run_benchmark(
    p: Dict = None, _: None = Depends(require_engine_key)  # noqa: B008
):
    p = p or {}
    start_date = p.get("start_date", "2025-01-01")
    end_date = p.get("end_date", _last_trading_day())
    initial_capital = float(p.get("initial_capital", 100000))
    symbol_sample_mode = p.get("symbol_sample_mode", "sp500")
    ar.engine.run_benchmark_in_thread(
        start_date, end_date, initial_capital, symbol_sample_mode
    )
    return {
        "status": "success",
        "message": f"Benchmark started. End date: {end_date}.",
    }  # noqa: E501
