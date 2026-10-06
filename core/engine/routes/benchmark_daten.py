"""Daten-Helfer der Benchmark-Kurve (#4074, ARC-E6 G-4h).

SPY-Rekonstruktion (``_reconstruct_spy_points``), echte Konto-Inception aus der Alpaca-Historie
(``_get_inception_equity``) und die einzahlungsbereinigten Kennzahlen
(``_read_metrics_db_or_fallback``, ``_return_metrics_from_points``). Sie sind unveraendert aus
``api_routes.py`` umgezogen. Die Route ``GET /benchmark-equity`` liegt in ``routes/benchmark.py``
und liest sie ueber dieses Modulobjekt, damit ein Patch auf
``core.engine.routes.benchmark_daten.<name>`` greift. Eine eigene Datei, weil Route und Helfer
zusammen ueber der Datei-Schwelle von 800 Zeilen laegen. Dieses Modul hat keine Routen.

``engine`` liest das Modul zur Laufzeit ueber ``ar`` (Zugriffsregel,
``core/engine/routes/__init__.py``).

Plan: ``docs/4074-g4h-benchmark-kurve/implementation_plan.md``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.engine import api_routes as ar


def _reconstruct_spy_points(points, initial_capital):
    """Rebuild the SPY benchmark series aligned to ``points`` (portfolio daily equity),
    normalized so it starts at ``initial_capital`` on the first point's date.

    Returns ``(spy_points, spy_first_close)``. Fail-soft: returns ``([], 1.0)`` on any error
    or when SPY data is unavailable. Backend-agnostic (uses ``engine.data_provider`` only) so
    the caller's Redis / LocalState caching behaves identically in every edition (BORA). This
    is the SINGLE source of the SPY reconstruction — called on a cold cache AND to self-heal a
    frozen-empty ``spy_points`` (an early empty reconstruction used to stay empty forever, so
    the S&P line never appeared even once SPY data became available).
    """
    spy_points: list = []
    spy_first_close = 1.0
    if not points or not initial_capital:
        return spy_points, spy_first_close
    try:
        first_date = str(points[0].get("date"))
        t_start = datetime.strptime(first_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        elapsed_days = (now_utc - t_start).days + 1
        spy_df = None
        if ar.engine and getattr(ar.engine, "data_provider", None) is not None:
            spy_df = ar.engine.data_provider.get_data(
                "SPY", now_utc, days=max(10, elapsed_days + 5)
            )
            if (spy_df is None or spy_df.empty) and elapsed_days > 30:
                spy_df = ar.engine.data_provider.get_data(
                    "SPY", now_utc, days=max(10, elapsed_days + 5), use_case="live"
                )
        if (
            (spy_df is None or spy_df.empty or "close" not in spy_df.columns)
            and ar.engine
            and getattr(ar.engine, "api", None) is not None
        ):
            try:
                from alpaca.data.timeframe import TimeFrame as AlpacaTimeFrame
                from alpaca.trading.requests import StockBarsRequest

                from core.data_provider import _bars_to_dataframe

                bars_resp = ar.engine.api.get_stock_bars(
                    StockBarsRequest(
                        symbol_or_symbols="SPY",
                        timeframe=AlpacaTimeFrame.Day,
                        start=t_start,
                        end=now_utc,
                    )
                )
                if (
                    bars_resp
                    and getattr(bars_resp, "df", None) is not None
                    and not bars_resp.df.empty
                ):
                    spy_df = _bars_to_dataframe(bars_resp.df)
            except Exception as alpaca_err:
                logging.warning(
                    "benchmark: direct Alpaca SPY fetch fallback failed: %s", alpaca_err
                )

        if spy_df is None or spy_df.empty or "close" not in spy_df.columns:
            logging.warning(
                "benchmark: SPY data unavailable (get_data('SPY') returned empty) — the S&P "
                "line is omitted. Check the market-data source (Alpaca IEX on a paper account)."
            )
        if spy_df is not None and not spy_df.empty and "close" in spy_df.columns:
            spy_df = spy_df.sort_index()
            spy_map = {
                idx.strftime("%Y-%m-%d"): float(row["close"])
                for idx, row in spy_df.iterrows()
            }
            # rc3 finding (#3071): the curve may start on a NON-trading day (a chosen
            # baseline like Saturday 2026-08-01, or a weekend inception). The old
            # fallback took spy_df.iloc[0] — the OLDEST close of the fetched window —
            # which rebased the S&P card to a months-old level (+11.10% shown where the
            # real since-baseline figure was +1.08%). Correct base: the first close
            # ON/AFTER the start date; only if none exists (curve beyond the data),
            # the last known close BEFORE it (fail-soft, never the window's oldest).
            spy_first_close = spy_map.get(first_date)
            if not spy_first_close:
                _after = [d for d in sorted(spy_map) if d >= first_date]
                if _after:
                    spy_first_close = spy_map[_after[0]]
                else:
                    _before = [d for d in sorted(spy_map) if d < first_date]
                    spy_first_close = (
                        spy_map[_before[-1]]
                        if _before
                        else float(spy_df.iloc[0]["close"])
                    )
            if spy_first_close and spy_first_close > 0:
                last_known = spy_first_close
                for p in points:
                    close = spy_map.get(p.get("date"), last_known)
                    last_known = close
                    spy_points.append(
                        {
                            "date": p.get("date"),
                            "equity": round(
                                initial_capital * (close / spy_first_close), 2
                            ),
                        }
                    )
    except Exception as spy_err:
        logging.warning("Failed to reconstruct SPY points: %s", spy_err, exc_info=True)
    return spy_points, spy_first_close


def _get_inception_equity():
    """Fetch the account's FULL equity history from Alpaca to find the true trading inception —
    the first day the account held non-zero equity — so "since inception" is measured from the
    real account start, not the first recorded ``PortfolioSnapshot`` (engine boot). (#1782)

    Returns ``(inception_date, inception_equity, backfill_points)`` (daily, UTC) or ``None``
    (fail-soft) so the caller falls back to ``records[0]``. Backend-agnostic; never hard-fails.
    """
    try:
        if ar.engine is None or not ar.engine.api:
            return None
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        # #2717: do NOT request cashflow_types here — portfolio-history's `cashflow` field comes back
        # EMPTY from Alpaca in practice, so it never stripped deposits. Real CSD/CSW cash-flows are
        # attached to the points later, from the account-activities API (see _get_live_cashflows).
        #
        # #2878: request the FULL history via ``period="all"`` (measured against a live paper
        # account: returns every day from the first funded day onward). Do NOT use ``start=`` —
        # Alpaca treats it as a ~1-MONTH window STARTING at that date, not "from that date to now",
        # so a pre-inception ``start`` yields an all-zero month and NO inception (the bug that
        # truncated the chart to the first local snapshot). If ``period="all"`` ever comes back
        # empty/misaligned (a transient Alpaca quirk — the original "worked until yesterday"
        # incident), retry with an explicit 1-year window before failing soft to ``records[0]``.
        def _fetch(**kw):
            return ar.engine.api.get_portfolio_history(
                GetPortfolioHistoryRequest(timeframe="1D", **kw)
            )

        def _aligned(h):
            ts = list(getattr(h, "timestamp", None) or [])
            eq = list(getattr(h, "equity", None) or [])
            return (ts, eq) if ts and len(ts) == len(eq) else (None, None)

        timestamps, equities = _aligned(_fetch(period="all"))
        if timestamps is None:
            logging.warning(
                "benchmark: Alpaca portfolio-history period=all empty/misaligned — retrying "
                "with period=1A. (#2878)"
            )
            timestamps, equities = _aligned(_fetch(period="1A"))
        if timestamps is None:
            logging.warning(
                "benchmark: Alpaca portfolio-history empty/misaligned — falling back to the "
                "first recorded snapshot as inception. (#1782)"
            )
            return None
        backfill: list = []
        inception_date = None
        inception_equity = None
        for ts, eq in zip(timestamps, equities):
            if eq is None:
                continue
            eq = float(eq)
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            if inception_equity is None:
                if eq <= 0:
                    continue  # skip pre-deposit $0 days (created_at != first funded day)
                inception_date, inception_equity = day, eq
            backfill.append({"date": day, "equity": round(eq, 2), "cashflow": 0.0})
        if inception_equity is None or not backfill:
            logging.warning(
                "benchmark: Alpaca portfolio-history had no non-zero equity — falling back to "
                "the first recorded snapshot as inception. (#1782)"
            )
            return None
        return inception_date, inception_equity, backfill
    except (
        Exception
    ) as exc:  # noqa: BLE001 — fail-soft: any error -> DB records[0] fallback
        logging.warning(
            "benchmark: Alpaca portfolio-history fetch failed (%s) — falling back to the first "
            "recorded snapshot as inception. (#1782)",
            exc,
            exc_info=True,
        )
        return None


async def _read_metrics_db_or_fallback(
    points, baseline_str, paper_trading, toggle_name
):
    import logging

    import config

    calc = _return_metrics_from_points(points)

    if not getattr(config, toggle_name, False):
        return calc, False

    try:
        import sqlalchemy as sa

        from core.database.models import PortfolioMetricsReport
        from core.database.session import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            stmt = (
                sa.select(PortfolioMetricsReport)
                .where(
                    PortfolioMetricsReport.baseline == (baseline_str or "ALL"),
                    PortfolioMetricsReport.paper_trading == paper_trading,
                )
                .order_by(PortfolioMetricsReport.timestamp.desc())
            )
            report = (await session.execute(stmt)).scalars().first()

            if report is None and points:
                # #3741: Es gab einen Leser, aber keinen Schreiber — `build_metrics_report`
                # hatte im Produktivcode keinen einzigen Aufrufer, also fiel dieser Zweig
                # IMMER auf die Neuberechnung zurueck und `coverage_gap_map` (die
                # Lueckenkarte aus #3405) blieb leer. Der Bericht wird jetzt beim ersten
                # Lesen aus den vorhandenen Datensaetzen gebaut und abgelegt.
                from datetime import datetime as _dt
                from datetime import timedelta as _td
                from datetime import timezone as _tz

                from core.engine import metrics_builder

                _von = _dt.strptime(points[0]["date"], "%Y-%m-%d").replace(
                    tzinfo=_tz.utc
                )
                _bis = _dt.strptime(points[-1]["date"], "%Y-%m-%d").replace(
                    tzinfo=_tz.utc
                ) + _td(days=1)
                report = await metrics_builder.build_metrics_report(
                    session,
                    _von,
                    _bis,
                    baseline_str or "ALL",
                    paper_trading,
                )
                if report is not None:
                    logging.info(
                        "CH-6: Bericht aus Datensaetzen gebaut (%s, paper=%s, %d Punkte).",
                        baseline_str or "ALL",
                        paper_trading,
                        len(points),
                    )

            if report:
                if abs(report.twr_pct - calc.get("twr_pct", 0.0)) > 0.01:
                    logging.warning(
                        f"Parallelbetrieb mismatch {toggle_name}: DB={report.twr_pct} vs Calc={calc.get('twr_pct')}"
                    )

                res = calc.copy()
                res["twr_pct"] = report.twr_pct
                res["decision_ids"] = report.decision_ids
                res["order_ids"] = report.order_ids
                return res, False
    except Exception as e:
        logging.warning(f"Failed to read PortfolioMetricsReport: {e}")

    # Fallback
    res = calc.copy()
    res["fallback_source"] = True
    return res, True


def _return_metrics_from_points(points: list) -> dict:
    """Cash-flow-adjusted KPIs from a benchmark points list (#1957).

    Each point is ``{"date", "equity", "cashflow"?}``; a missing ``cashflow`` counts as 0.
    Returns ``{twr_pct, net_deposits, pnl_net_of_deposits}`` (all 0 for <2 points). This is the
    single source of truth for the return KPIs — the frontend stops computing (now-start)/start,
    so a deposit no longer inflates Growth Rate / Total Return / the SPY comparison.
    """
    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    dates = [p.get("date") for p in points]
    equity = [float(p.get("equity") or 0.0) for p in points]
    cashflows = [float(p.get("cashflow") or 0.0) for p in points]
    res = compute_cashflow_adjusted_returns(dates, equity, cashflows)
    # #3145: write the EQUITY-EFFECTIVE flow back onto the points, so the curve the console
    # receives carries exactly the flows this function chained. The console recomputes the same
    # return for the chart and the hero; before this it chained the GROSS amounts and landed
    # 0.0056 pp away from the KPI card on the live vector — the same number shown twice,
    # differently. ``net_deposits`` remains gross.
    eff = res.get("effective_cashflows") or []
    if len(eff) == len(points):
        for _p, _e in zip(points, eff):
            if _e:
                _p["cashflow"] = round(float(_e), 2)
            elif "cashflow" in _p:
                _p.pop("cashflow")
    return res
