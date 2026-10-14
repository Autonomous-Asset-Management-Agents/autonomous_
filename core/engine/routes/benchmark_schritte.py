"""Die Schritte von ``GET /benchmark-equity`` (#4075, ARC-E6 G-4i).

``get_benchmark_equity`` (``routes/benchmark.py``) war ein Rumpf von 538 Zeilen. Er ist hier
wortgleich in neun Schritte zerlegt, je ein Block des alten Rumpfs (Plan §1). Der Dirigent im
Router ruft ``_schritt_cache_lesen``, entscheidet mit der unveraenderten Bedingung den Pfad und
fuehrt dessen Schritte aus, bis einer eine Antwort liefert (alles ausser ``_WEITER``):

- Neuaufbau: ``_schritt_live_kurve`` → ``_schritt_snapshots_laden`` → ``_schritt_ohne_snapshots``
  → ``_schritt_punkte_bauen`` → ``_schritt_spy_kennzahlen``;
- Cache-Pfad: ``_schritt_cache_heute`` → ``_schritt_cache_spy`` → ``_schritt_cache_antwort``.

Werte, die zwischen Bloecken fliessen, liegen in ``BenchmarkKurve`` (``z``). Gegen den alten
Rumpf unterscheiden sich die Schritte nur in drei Abbildungen: ``z.<feld>``, ``return _WEITER``
am Blockende und die lokalen Importe, die jeder Schritt wiederholt, der sie braucht. Das
beweist ``tests/unit/test_benchmark_kurve_wortgleich.py`` per AST gegen eine eingecheckte Kopie.

Zugriff wie im Router: ``engine``, ``config``, ``RedisClient``, ``_get_live_cashflows`` und
``_get_performance_baseline`` ueber ``ar``, die Daten-Helfer ueber das Modulobjekt ``_bd``.

``timezone`` ist hier absichtlich NICHT importiert. Im alten Rumpf machte der lokale
``from datetime import timezone`` des Neuaufbaus den Namen zur Funktions-Lokalen; im Cache-Pfad
war er nie gebunden. Der Zeitstempel fuer den heutigen SPY-Punkt in ``_schritt_cache_spy``
warf also immer, und das ``except Exception: pass`` dort schluckte es. Der Schritt behaelt genau dieses Verhalten
(verhaltensneutraler Umbau); die Korrektur ist ein eigenes Issue (Walkthrough, „Offen").

Plan: ``docs/4075-g4i-benchmark-kurve-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from core.engine import api_routes as ar
from core.engine.routes import benchmark_daten as _bd


@dataclass
class BenchmarkKurve:
    """Zustand zwischen den Schritten — die Werte, die im alten Rumpf von Block zu Block
    flossen (Plan §1). Jedes Feld setzt sein Block, bevor ein spaeterer es liest."""

    r: Any = None
    _cached: Any = None
    cfg_paper: bool = False
    _cache_mode_stale: bool = False
    _baseline: Any = None
    _cache_baseline_stale: bool = False
    records: list = field(default_factory=list)
    earliest_snap: Any = None
    initial_capital: Any = None
    points: list = field(default_factory=list)
    spy_points: list = field(default_factory=list)
    acct_cf: Any = None
    data: Any = None
    today_str: Any = None


#: Ein Schritt ist fertig, der Dirigent ruft den naechsten.
_WEITER = object()


async def _schritt_cache_lesen(z: BenchmarkKurve):
    """Cache lesen, Modus, Baseline, Veraltet-Flags (alter Rumpf ``:2004–2032``)."""
    z.r = ar.RedisClient.get_sync_redis()
    # Offload the blocking Redis GET off the event loop (read-only cache read for the chart).
    data_str = await asyncio.to_thread(z.r.get, "benchmark_equity_data")
    z._cached = None
    if data_str:
        try:
            z._cached = __import__("json").loads(data_str)
        except Exception:
            z._cached = None
    # PR-3: current trading mode. is_simulation is BACKTEST-only (a paper account is a REAL
    # Alpaca paper account -> is_simulation=False for BOTH paper and live), so paper-vs-live is
    # a SEPARATE discriminator. Fail-safe default = paper (True).
    z.cfg_paper = bool(getattr(ar.config, "PAPER_TRADING", True))
    # PR-3: a cache tagged with the OTHER mode (e.g. the paper curve that persists in Redis/DB
    # across the paper->live restart) is stale — force a DB rebuild under the current mode so
    # paper numbers never pollute the live-labelled curve. Legacy caches lack the key ->
    # default True (paper); in live that mismatches config -> correctly treated as stale.
    z._cache_mode_stale = bool(z._cached) and (
        bool(z._cached.get("paper_trading", True)) != z.cfg_paper
    )
    # #3071: the served window depends on the persisted baseline — a cache built under a
    # DIFFERENT baseline (or before one was set/cleared) is stale and must rebuild.
    z._baseline = await ar._get_performance_baseline()

    z._cache_baseline_stale = bool(z._cached) and (
        (z._cached.get("baseline_date") or None) != z._baseline
    )


async def _schritt_live_kurve(z: BenchmarkKurve):
    """Neuaufbau, LIVE: die ganze Tageskurve vom Broker (``:2051–2141``)."""
    # Fallback to database query if Redis cache is empty/cold
    import json

    from core.engine.perf_metrics import flows_signature

    # #3054: LIVE (separate Alpaca account) — build the COMPLETE daily curve from Alpaca
    # portfolio_history (period=all) instead of the sparse local snapshots that starve
    # Sharpe/drawdown/TWR (14 of ~32 days). PR-3 gated the Alpaca backfill to paper for fear
    # of paper-era pollution; that does not apply to a SEPARATE live account (owner sign-off
    # 2026-08-26). Cash-flows (incl. the #3049 FEE) are attached exactly as the snapshot
    # path. Fail-soft: no Alpaca history -> fall through to the snapshot logic below.
    if not z.cfg_paper:
        _live_inc = await asyncio.to_thread(_bd._get_inception_equity)
        # #3054: _get_inception_equity returns a (date, equity, backfill) 3-tuple or
        # None. Guard the shape defensively so an unexpected return can never crash the
        # endpoint on the live path (fail-soft to the snapshot logic below).
        if isinstance(_live_inc, (tuple, list)) and len(_live_inc) == 3:
            # #3068: jump-aligned attribution (NOT by activity date) — a settlement-
            # lagged deposit otherwise strips from the wrong sub-period (the real
            # -22.94% inception / -50.91% drawdown on a live account).
            from core.engine.perf_metrics import align_cashflows_to_points

            _inc_date, _inc_eq, _backfill = _live_inc
            live_points = [
                {"date": p["date"], "equity": p["equity"]} for p in _backfill
            ]
            # rc5 R7: Alpaca answers period=all for the whole account lifetime — the
            # days BEFORE the account was funded come back as zero equity. They are not
            # history, and as the curve's base they make the account's own value read
            # as market P&L. Trim the leading run (fail-soft, interior zeros kept).
            from core.engine.perf_metrics import trim_leading_unfunded

            live_points = trim_leading_unfunded(live_points)
            live_cf = await asyncio.to_thread(ar._get_live_cashflows)
            if live_cf:
                live_buckets = align_cashflows_to_points(
                    [p["date"] for p in live_points],
                    [p["equity"] for p in live_points],
                    live_cf,
                )
                for _p, _c in zip(live_points, live_buckets):
                    if _c:
                        _p["cashflow"] = round(_c, 2)
            # #3071: dual figures — the TRUE-inception TWR is computed on the full
            # aligned curve BEFORE slicing (reports/audit always keep both).
            from core.engine.perf_metrics import slice_points_to_baseline

            _live_inception_date = live_points[0]["date"]
            _live_full_metrics, _ = await _bd._read_metrics_db_or_fallback(
                live_points, "ALL", False, "CH6_READ_METRICS_DB_3391"
            )
            live_points = slice_points_to_baseline(live_points, z._baseline)
            _live_initial = live_points[0]["equity"]
            live_metrics, _ = await _bd._read_metrics_db_or_fallback(
                live_points, z._baseline, False, "CH6_READ_METRICS_DB_3394"
            )
            live_spy, live_spy_first = await asyncio.to_thread(
                _bd._reconstruct_spy_points, live_points, _live_initial
            )
            live_resp = {
                "points": live_points,
                "spy_points": live_spy,
                "spy_first_close": live_spy_first,
                "initial_capital": _live_initial,
                "start_date": live_points[0]["date"],
                "end_date": live_points[-1]["date"],
                "strategy": "RLAgent",
                "final_equity": live_points[-1]["equity"],
                "paper_trading": False,
                # #3071: honest labelling + audit — the chosen base and the true
                # inception travel with every response.
                "baseline_date": z._baseline,
                "inception_date": _live_inception_date,
                "inception_twr_pct": _live_full_metrics["twr_pct"],
                # rc4 R4: ledger fingerprint — cached polls compare against a
                # fresh fetch and evict on change (late-posting deposits).
                "flows_sig": flows_signature(live_cf),
                **live_metrics,
            }
            try:
                z.r.set("benchmark_equity_data", json.dumps(live_resp))
            except Exception as _r_err:  # noqa: BLE001 — cache write is best-effort
                logging.warning("Failed to cache live benchmark data: %s", _r_err)
            return live_resp
    return _WEITER


async def _schritt_snapshots_laden(z: BenchmarkKurve):
    """Neuaufbau: Tages-Snapshots aus der Datenbank (``:2150–2221``)."""
    import sqlalchemy as sa

    from core.database.models import PortfolioSnapshot
    from core.database.session import AsyncSessionLocal

    # #2588: a failed snapshot query must DEGRADE, never kill the endpoint. The
    # outer catch used to turn e.g. a local schema drift (``no such column:
    # portfolio_snapshots.is_sim_day`` after #2544) into ``internal_error`` with
    # ZERO points — every chart range beyond 1D/1W went "Collecting equity
    # history…" and the Since-inception/Started/MaxDD KPIs died. With
    # ``records = []`` the existing no-records path below serves the broker's
    # full portfolio history in paper (live stays fail-closed: no_live_history).
    try:
        async with AsyncSessionLocal() as session:
            dialect = session.bind.dialect.name
            # PR-3: segregate paper vs live. LIVE -> only REAL live snapshots
            # (paper_trading IS False). PAPER -> paper (True) OR legacy NULL rows written
            # before this column existed (isnot False = True OR NULL). Applied to BOTH the
            # postgres-distinct and the sqlite max-per-day statements so the live curve is
            # built ONLY from live snapshots (no paper carryover after a paper->live restart).
            mode_filter = (
                PortfolioSnapshot.paper_trading.isnot(False)
                if z.cfg_paper
                else PortfolioSnapshot.paper_trading.is_(False)
            )
            if dialect == "postgresql":
                stmt = (
                    sa.select(PortfolioSnapshot)
                    .where(mode_filter)
                    .distinct(
                        sa.func.date_trunc(
                            "day", PortfolioSnapshot.timestamp
                        )  # noqa: E501
                    )
                    .order_by(
                        sa.func.date_trunc(
                            "day", PortfolioSnapshot.timestamp
                        ),  # noqa: E501
                        PortfolioSnapshot.timestamp.desc(),
                    )
                )
                result = await session.execute(stmt)
                z.records = list(result.scalars().all())
                z.records.sort(key=lambda x: x.timestamp)
            else:
                # SQLite: subquery max(timestamp) grouped by date(timestamp)  # noqa: E501
                subq = (
                    sa.select(
                        sa.func.date(PortfolioSnapshot.timestamp).label(
                            "snapshot_date"
                        ),
                        sa.func.max(PortfolioSnapshot.timestamp).label(
                            "max_ts"
                        ),  # noqa: E501
                    )
                    .where(mode_filter)
                    .group_by(sa.func.date(PortfolioSnapshot.timestamp))
                    .subquery()
                )
                stmt = (
                    sa.select(PortfolioSnapshot)
                    .join(
                        subq,
                        sa.and_(
                            sa.func.date(PortfolioSnapshot.timestamp)
                            == subq.c.snapshot_date,
                            PortfolioSnapshot.timestamp == subq.c.max_ts,
                        ),
                    )
                    .where(mode_filter)
                    .order_by(PortfolioSnapshot.timestamp.asc())
                )
                result = await session.execute(stmt)
                z.records = list(result.scalars().all())
    except Exception as db_err:  # noqa: BLE001 — degrade, never a dead chart
        logging.warning(
            "benchmark-equity: DB snapshot query failed (%s) — degrading to "
            "the broker's portfolio history so the chart/KPIs stay alive. "
            "Likely local schema drift; init_local_db heals it on the next "
            "boot. (#2588)",
            db_err,
            exc_info=True,
        )
        z.records = []
    return _WEITER


async def _schritt_ohne_snapshots(z: BenchmarkKurve):
    """Neuaufbau ohne Snapshots: leere Antwort oder Broker-Kurve (``:2223–2290``)."""
    import json

    if not z.records:
        if not z.cfg_paper:
            # PR-3 (LIVE, zero live snapshots): show NO live history until real LIVE
            # snapshots accumulate. NO paper carryover and NO broker-seeded inception —
            # do NOT call _get_inception_equity (no Alpaca period=all backfill), because
            # that broker curve/inception would pollute the freshly-live account with its
            # pre-live (paper-era) equity. initial_capital is None until a live snapshot
            # lands. PAPER is unaffected (the bug is live-only).
            return {
                "points": [],
                "spy_points": [],
                "strategy": "RLAgent",
                "initial_capital": None,
                "message": "no_live_history",
            }
        # PAPER (unchanged): fresh engine / empty DB — no recorded snapshots yet. Still
        # surface the account's REAL equity history from Alpaca (period=all) so the Console
        # + demo render the full curve from inception on day one, instead of an empty chart.
        # Only a truly empty account (no Alpaca history either) returns the "no run yet"
        # placeholder. (#1790, follow-up to #1782 — the records path below is unchanged.)
        inception = await asyncio.to_thread(_bd._get_inception_equity)
        if inception is None:
            return {
                "points": [],
                "spy_points": [],
                "strategy": "RLAgent",
                "initial_capital": None,
                "message": "No benchmark run yet.",
            }
        _, inception_equity, backfill = inception
        z.points = list(backfill)
        z.spy_points, spy_first_close = await asyncio.to_thread(
            _bd._reconstruct_spy_points, z.points, inception_equity
        )
        metrics, _ = await _bd._read_metrics_db_or_fallback(
            z.points, "ALL", z.cfg_paper, "CH6_READ_METRICS_DB_3542"
        )
        alpaca_only = {
            "points": z.points,
            "spy_points": z.spy_points,
            "spy_first_close": spy_first_close,
            "initial_capital": inception_equity,
            "start_date": z.points[0]["date"],
            "end_date": z.points[-1]["date"],
            "strategy": "RLAgent",
            "final_equity": z.points[-1]["equity"],
            # PR-3: tag the rebuilt cache with the mode so the next read's stale-check
            # (embedded paper_trading vs config.PAPER_TRADING) recognises it as current.
            "paper_trading": z.cfg_paper,
            **metrics,  # #1957: twr_pct / net_deposits / pnl_net_of_deposits
        }
        try:
            z.r.set("benchmark_equity_data", json.dumps(alpaca_only))
        except Exception as r_err:
            logging.warning(
                "Failed to save Alpaca-only benchmark data to Redis: %s",
                r_err,  # noqa: E501
            )
        return {
            "points": z.points,
            "spy_points": z.spy_points,
            "start_date": z.points[0]["date"],
            "end_date": z.points[-1]["date"],
            "strategy": "RLAgent",
            "initial_capital": inception_equity,
            "final_equity": z.points[-1]["equity"],
            **metrics,
        }
    return _WEITER


async def _schritt_punkte_bauen(z: BenchmarkKurve):
    """Neuaufbau: Punkte aus den Snapshots, Inception, Cashflows (``:2292–2344``)."""
    z.earliest_snap = z.records[0]
    z.initial_capital = z.earliest_snap.total_equity or 100000.0

    z.points = []
    for r_item in z.records:
        z.points.append(
            {
                "date": r_item.timestamp.strftime("%Y-%m-%d"),
                "equity": round(r_item.total_equity or 0.0, 2),
            }
        )

    # #1782: anchor "since inception" to the account's REAL trading start (Alpaca full
    # portfolio-history), not records[0] (the first RECORDED snapshot = engine boot).
    # Prepend the pre-snapshot daily equity + use the true inception as initial_capital.
    # Fail-soft: _get_inception_equity() -> None keeps the records[0] behaviour.
    #
    # PR-3: this Alpaca broker-seed is PAPER-ONLY. In LIVE we must NOT prepend the broker
    # history: period=all would drag in the account's pre-live (paper-era) equity and
    # pollute the live curve/inception. In live, initial_capital stays the earliest LIVE
    # snapshot's total_equity (records[0]) and no backfill is prepended.
    if z.cfg_paper:
        inception = await asyncio.to_thread(_bd._get_inception_equity)
        if inception is not None:
            _, inception_equity, backfill = inception
            z.initial_capital = inception_equity
            seen = {p["date"] for p in z.points}
            first_recorded = z.points[0]["date"] if z.points else None
            z.points = [
                p
                for p in backfill
                if p["date"] not in seen
                and (first_recorded is None or p["date"] < first_recorded)
            ] + z.points
    # #2717: attach the account's real deposit/withdrawal cash-flows (from the
    # account-activities API — portfolio-history.cashflow is empty in reality) and bucket
    # each into the first point on/after its date, so a deposit that landed in a SPARSE
    # snapshot gap (a day the engine was off) still lands in the sub-period that spans it
    # instead of being dropped — which left the deposit-inflated return on screen. Applies to
    # BOTH modes (a live account is the reported case; paper simply has no CSD/CSW).
    z.acct_cf = await asyncio.to_thread(ar._get_live_cashflows)
    if z.acct_cf:
        # #3068: jump-aligned (not date-based) — see the live block above.
        from core.engine.perf_metrics import align_cashflows_to_points

        buckets = align_cashflows_to_points(
            [p.get("date") for p in z.points],
            [p.get("equity") or 0.0 for p in z.points],
            z.acct_cf,
        )
        for p, cf in zip(z.points, buckets):
            if cf:
                p["cashflow"] = round(cf, 2)
    return _WEITER


async def _schritt_spy_kennzahlen(z: BenchmarkKurve):
    """Neuaufbau: Baseline-Schnitt, SPY, Kennzahlen, Cache schreiben (``:2348–2409``)."""
    import json

    # #3071: slice AFTER the jump-alignment above (a boundary deposit attaches to its
    # in-window jump exactly once); dual figures — full-curve TWR survives for reports.
    from core.engine.perf_metrics import flows_signature, slice_points_to_baseline

    _inception_date_str = z.points[0]["date"] if z.points else None
    _full_metrics, _ = await _bd._read_metrics_db_or_fallback(
        z.points, "ALL", z.cfg_paper, "CH6_READ_METRICS_DB_3634"
    )
    z.points = slice_points_to_baseline(z.points, z._baseline)
    if z._baseline and z.points:
        z.initial_capital = z.points[0].get("equity") or z.initial_capital

    start_date_str = (
        z.points[0]["date"]
        if z.points
        else z.earliest_snap.timestamp.strftime("%Y-%m-%d")
    )

    z.spy_points, spy_first_close = await asyncio.to_thread(
        _bd._reconstruct_spy_points, z.points, z.initial_capital
    )

    metrics, _ = await _bd._read_metrics_db_or_fallback(
        z.points, z._baseline, z.cfg_paper, "CH6_READ_METRICS_DB_3649"
    )
    reconstructed_data = {
        "points": z.points,
        "spy_points": z.spy_points,
        "spy_first_close": spy_first_close,
        "initial_capital": z.initial_capital,
        "start_date": start_date_str,
        "end_date": z.records[-1].timestamp.strftime("%Y-%m-%d"),
        "strategy": z.earliest_snap.strategy_name or "RLAgent",
        "final_equity": z.records[-1].total_equity,
        # PR-3: tag the rebuilt cache with the mode so the next read's stale-check
        # recognises it as current (else live would rebuild on every poll).
        "paper_trading": z.cfg_paper,
        # #3071: baseline echo (cache stale-check key) + true-inception figures.
        "baseline_date": z._baseline,
        "inception_date": _inception_date_str,
        "inception_twr_pct": _full_metrics["twr_pct"],
        # rc4 R4: ledger fingerprint (see live path).
        "flows_sig": flows_signature(z.acct_cf),
        **metrics,  # #1957: twr_pct / net_deposits / pnl_net_of_deposits
    }

    try:
        z.r.set("benchmark_equity_data", json.dumps(reconstructed_data))
    except Exception as r_err:
        logging.warning(
            "Failed to save reconstructed benchmark data to Redis: %s",
            r_err,  # noqa: E501
        )

    return {
        "points": z.points,
        "spy_points": z.spy_points,
        "start_date": start_date_str,
        "end_date": z.records[-1].timestamp.strftime("%Y-%m-%d"),
        "strategy": z.earliest_snap.strategy_name or "RLAgent",
        "initial_capital": z.initial_capital,
        "final_equity": z.records[-1].total_equity,
        **metrics,
    }


async def _schritt_cache_heute(z: BenchmarkKurve):
    """Cache-Pfad: heutigen Punkt ergaenzen, Cache bei neuen Geldfluessen raeumen (``:2411–2465``)."""
    from core.engine.perf_metrics import flows_signature

    z.data = z._cached
    z.points = list(z.data.get("points", []))
    z.spy_points = list(z.data.get("spy_points", []))
    z.initial_capital = z.data.get("initial_capital")
    z.today_str = date.today().strftime("%Y-%m-%d")

    if ar.engine.api and z.initial_capital:
        try:
            acc = await asyncio.to_thread(ar.engine.api.get_account)
            live_equity = float(acc.equity or 0)
            # rc3 R2: a settlement-scale step between the cached tail and the live
            # equity means a deposit/withdrawal just landed. The append below carries
            # NO cashflow, so Net deposits / today's tile would stay wrong for hours.
            # Evict the cache: the NEXT poll (<=60s) does a full rebuild with freshly
            # aligned flows. This response still serves the cached (stale) numbers.
            from core.engine.perf_metrics import is_settlement_scale_jump

            # rc4 R4: LEDGER FRESHNESS — refetch the funding flows on every cached
            # poll and compare fingerprints. A deposit that posts to the activity
            # ledger AFTER the rebuild (the real 25.08. CSD that stayed invisible:
            # Net deposits +5.79k vs an 11.9k book, the +95.99% "return") evicts
            # the cache -> full rebuild with fresh flows on the next poll (<=60s).
            _fresh_cf = await asyncio.to_thread(ar._get_live_cashflows)
            if flows_signature(_fresh_cf) != (z.data.get("flows_sig") or "none"):
                try:
                    z.r.delete("benchmark_equity_data")
                    logging.info(
                        "benchmark: funding-ledger changed (sig %s -> %s) — cache "
                        "evicted for full rebuild on next poll. (rc4 R4)",
                        z.data.get("flows_sig"),
                        flows_signature(_fresh_cf),
                    )
                except Exception:  # noqa: BLE001 — eviction is best-effort
                    pass
            if z.points and is_settlement_scale_jump(
                z.points[-1].get("equity"), live_equity
            ):
                try:
                    z.r.delete("benchmark_equity_data")
                    logging.info(
                        "benchmark: settlement-scale live step (%.2f -> %.2f) — "
                        "cache evicted for full rebuild on next poll. (rc3 R2)",
                        float(z.points[-1].get("equity") or 0.0),
                        live_equity,
                    )
                except Exception:  # noqa: BLE001 — eviction is best-effort
                    pass
            if live_equity > 0 and (
                not z.points or z.points[-1].get("date") != z.today_str
            ):
                z.points.append(
                    {"date": z.today_str, "equity": round(live_equity, 2)}
                )  # noqa: E501
        except Exception:
            pass
    return _WEITER


async def _schritt_cache_spy(z: BenchmarkKurve):
    """Cache-Pfad: SPY-Linie heilen oder um heute ergaenzen (``:2467–2514``)."""
    spy_first_close = z.data.get("spy_first_close")
    if not z.spy_points and z.points and z.initial_capital:
        # Self-heal a frozen-empty spy_points: it is reconstructed only on a COLD cache,
        # so an early empty result (SPY data not yet available at the first reconstruction)
        # stayed empty forever and the S&P line never appeared. Recompute it from the
        # current points now, and heal the cache so we don't refetch on every poll.
        z.spy_points, spy_first_close = await asyncio.to_thread(
            _bd._reconstruct_spy_points, z.points, z.initial_capital
        )
        if z.spy_points:
            z.data["spy_points"] = z.spy_points
            z.data["spy_first_close"] = spy_first_close
            try:
                z.r.set("benchmark_equity_data", __import__("json").dumps(z.data))
            except Exception as heal_err:
                logging.warning("Failed to heal benchmark cache: %s", heal_err)
        else:
            # If reconstruction failed, evict thin warm cache so subsequent polls retry
            try:
                z.r.delete("benchmark_equity_data")
            except Exception:
                pass
    elif (
        z.spy_points and spy_first_close and z.initial_capital and spy_first_close > 0
    ):  # noqa: E501
        try:
            # G-4i: ``timezone`` ist absichtlich frei (wie im alten Rumpf), Modul-Docstring.
            end_dt = datetime.now(timezone.utc)  # noqa: F821
            spy_df = ar.engine.data_provider.get_data("SPY", end_dt, days=10)
            if spy_df is not None and not spy_df.empty and "close" in spy_df.columns:
                last_close = float(spy_df.iloc[-1]["close"])
                if (
                    last_close > 0 and z.spy_points[-1].get("date") != z.today_str
                ):  # noqa: E501
                    z.spy_points.append(
                        {
                            "date": z.today_str,
                            "equity": round(
                                z.initial_capital * (last_close / spy_first_close),
                                2,  # noqa: E501
                            ),
                        }
                    )
        except Exception:
            pass
    return _WEITER


async def _schritt_cache_antwort(z: BenchmarkKurve):
    """Cache-Pfad: Kennzahlen neu rechnen, Antwort (``:2520–2536``)."""
    # #1957: recompute cash-flow-adjusted KPIs from the final points (today's live-equity
    # append above may have extended the series). Points keep their cached per-day cashflow;
    # the appended intraday point has none (0) — a same-day deposit surfaces on the next full
    # rebuild (cache is thin/stale then), which is acceptable at daily granularity.
    metrics, _ = await _bd._read_metrics_db_or_fallback(
        z.points, z.data.get("baseline_date"), z.cfg_paper, "CH6_READ_METRICS_DB_3799"
    )
    return {
        "points": z.points,
        "spy_points": z.spy_points,
        "start_date": z.data.get("start_date"),
        "end_date": z.data.get("end_date"),
        "strategy": z.data.get("strategy", "RLAgent"),
        "initial_capital": z.initial_capital,
        "final_equity": z.data.get("final_equity"),
        # #3071: cached points are already sliced; pass the labelling fields through.
        "baseline_date": z.data.get("baseline_date"),
        "inception_date": z.data.get("inception_date"),
        "inception_twr_pct": z.data.get("inception_twr_pct"),
        **metrics,
    }
