from datetime import datetime

import sqlalchemy as sa

from core.database.models import (
    DecisionOutcome,
    PortfolioMetricsReport,
    PortfolioSnapshot,
)


async def build_metrics_report(
    session: sa.ext.asyncio.AsyncSession,
    start_date: datetime,
    end_date: datetime,
    baseline: str,
    paper_trading: bool,
    timestamp: datetime | None = None,
) -> PortfolioMetricsReport:
    """Builds and persists a PortfolioMetricsReport for the given period."""
    # 1. Fetch points
    stmt = (
        sa.select(PortfolioSnapshot)
        .where(
            PortfolioSnapshot.timestamp >= start_date,
            PortfolioSnapshot.timestamp <= end_date,
            PortfolioSnapshot.paper_trading == paper_trading,
        )
        .order_by(PortfolioSnapshot.timestamp.asc())
    )
    snaps = (await session.execute(stmt)).scalars().all()

    if not snaps:
        return None

    points = [
        {
            "date": s.timestamp.strftime("%Y-%m-%d"),
            "equity": s.total_equity,
            "cashflow": s.cash,
        }
        for s in snaps
    ]

    from core.engine.perf_metrics import compute_cashflow_adjusted_returns

    dates = [p.get("date") for p in points]
    equity = [float(p.get("equity") or 0.0) for p in points]
    cashflows = [float(p.get("cashflow") or 0.0) for p in points]
    res = compute_cashflow_adjusted_returns(dates, equity, cashflows)

    # Fetch decision_ids and build gap map (#3405)
    dec_stmt = sa.select(DecisionOutcome.decision_id, DecisionOutcome.votes_json).where(
        DecisionOutcome.decision_time >= start_date,
        DecisionOutcome.decision_time <= end_date,
    )
    decisions = (await session.execute(dec_stmt)).all()
    decision_ids = [d[0] for d in decisions]

    from core.database.models import Trade

    trade_stmt = sa.select(Trade.trade_id, Trade.position_pnl).where(
        Trade.executed_at >= start_date,
        Trade.executed_at <= end_date,
        Trade.is_simulation == paper_trading,
    )
    trades = (await session.execute(trade_stmt)).all()
    order_ids = [t[0] for t in trades]

    # Win rate
    closed_trades = [t for t in trades if t[1] is not None]
    if closed_trades:
        winning = sum(1 for t in closed_trades if t[1] > 0)
        win_rate_pct = (winning / len(closed_trades)) * 100.0
    else:
        win_rate_pct = 0.0

    # Max Drawdown
    peak = 0.0
    max_dd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        if peak > 0:
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd
    max_drawdown_pct = max_dd * 100.0

    gap_map = {}
    for d in decisions:
        votes = d[1] or []
        for v in votes:
            agent = v.get("agent_name")
            if not agent:
                continue
            if agent not in gap_map:
                gap_map[agent] = {
                    "total_symbols": 0,
                    "has_data_count": 0,
                    "abstain_reasons": {},
                }

            gap_map[agent]["total_symbols"] += 1
            reason = v.get("abstain_reason")
            if reason:
                reason_str = str(reason)
                gap_map[agent]["abstain_reasons"][reason_str] = (
                    gap_map[agent]["abstain_reasons"].get(reason_str, 0) + 1
                )
            else:
                gap_map[agent]["has_data_count"] += 1

    for _, stats in gap_map.items():
        stats["has_data_ratio"] = (
            stats["has_data_count"] / stats["total_symbols"]
            if stats["total_symbols"] > 0
            else 0.0
        )

    date_suffix = end_date.strftime("%Y%m%d")
    report_id = f"report-{baseline}-{paper_trading}-{date_suffix}"

    # create report
    report = PortfolioMetricsReport(
        id=report_id,
        timestamp=timestamp or end_date,
        start_date=start_date,
        end_date=end_date,
        baseline=baseline,
        paper_trading=paper_trading,
        initial_capital=points[0]["equity"],
        final_equity=points[-1]["equity"],
        twr_pct=res.get("twr_pct", 0.0),
        max_drawdown_pct=max_drawdown_pct,
        win_rate_pct=win_rate_pct,
        decision_ids=list(decision_ids),
        order_ids=order_ids,
        coverage_gap_map=gap_map,
    )

    session.add(report)
    await session.commit()
    return report
