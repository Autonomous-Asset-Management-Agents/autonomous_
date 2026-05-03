# performance_metrics.py
# --- Compute risk-adjusted and trade-level metrics for accurate backtest evaluation ---

import logging
import math
from typing import Dict, List, Any


def compute_performance_metrics(
    daily_equity: List[Dict[str, Any]],
    trades: List[Dict[str, Any]],
    initial_capital: float,
    risk_free_rate_annual: float = 0.05,
) -> Dict[str, Any]:
    """
    Compute standard performance metrics from simulation/live results.

    Args:
        daily_equity: List of {"date": "YYYY-MM-DD", "equity": float}
        trades: List of trade dicts with symbol, side, qty, price, timestamp
        initial_capital: Starting capital
        risk_free_rate_annual: For Sharpe/Sortino (e.g. 0.05 = 5%)

    Returns:
        Dict with: total_return_pct, sharpe_ratio_annual, sortino_ratio_annual,
                  max_drawdown_pct, max_drawdown_duration_days, calmar_ratio_annual,
                  avg_trade_pnl, num_trades, num_round_trips
    """
    out = {
        "total_return_pct": 0.0,
        "sharpe_ratio_annual": None,
        "sortino_ratio_annual": None,
        "max_drawdown_pct": 0.0,
        "max_drawdown_duration_days": 0,
        "calmar_ratio_annual": None,
        "avg_trade_pnl": None,
        "num_trades": 0,
        "num_round_trips": 0,
    }

    if not daily_equity or initial_capital <= 0:
        return out

    equities = [float(p.get("equity", 0)) for p in daily_equity]
    if not equities:
        return out

    # Total return
    final_equity = equities[-1]
    out["total_return_pct"] = (
        (final_equity - initial_capital) / initial_capital
    ) * 100.0

    # Daily returns for Sharpe/Sortino
    daily_returns = []
    for i in range(1, len(equities)):
        if equities[i - 1] > 0:
            r = (equities[i] - equities[i - 1]) / equities[i - 1]
            daily_returns.append(r)

    if daily_returns:
        n = len(daily_returns)
        mean_ret = sum(daily_returns) / n
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / max(n - 1, 1)
        std = math.sqrt(variance) if variance > 0 else 0.0

        # Downside returns (for Sortino)
        downside_returns = [r for r in daily_returns if r < 0]
        downside_std = 0.0
        if len(downside_returns) > 1:
            dm = sum(downside_returns) / len(downside_returns)
            downside_std = math.sqrt(
                sum((r - dm) ** 2 for r in downside_returns)
                / (len(downside_returns) - 1)
            )
        elif downside_returns:
            downside_std = abs(downside_returns[0])

        # Annualization: assume ~252 trading days
        rf_daily = risk_free_rate_annual / 252.0
        if std > 0:
            out["sharpe_ratio_annual"] = round(
                (mean_ret - rf_daily) / std * math.sqrt(252), 4
            )
        if downside_std > 0:
            out["sortino_ratio_annual"] = round(
                (mean_ret - rf_daily) / downside_std * math.sqrt(252), 4
            )

    # Max drawdown
    peak = equities[0]
    max_dd = 0.0
    dd_start_idx = 0
    dd_duration_max = 0
    for i, eq in enumerate(equities):
        if eq > peak:
            peak = eq
            dd_start_idx = i
        if peak > 0:
            dd_pct = (peak - eq) / peak * 100.0
            if dd_pct > max_dd:
                max_dd = dd_pct
            dd_duration_max = max(dd_duration_max, i - dd_start_idx)

    out["max_drawdown_pct"] = round(max_dd, 2)
    out["max_drawdown_duration_days"] = dd_duration_max

    # Calmar ratio (return / max drawdown); annualize return over period
    if max_dd > 0 and len(daily_equity) > 1:
        period_years = len(daily_equity) / 252.0
        if period_years > 0:
            ann_return = out["total_return_pct"] / 100.0 / period_years
            out["calmar_ratio_annual"] = round(ann_return / (max_dd / 100.0), 4)

    # Trade-level metrics
    if trades:
        out["num_trades"] = len(trades)
        net_pnl = final_equity - initial_capital
        if out["num_trades"] > 0:
            out["avg_trade_pnl"] = round(net_pnl / out["num_trades"], 2)
        # Win rate / profit factor need per-trade PnL (round-trip); simulation Trade doesn't store cost basis.
        # Count sells as closed round-trips; we cannot compute W/L without entry price per symbol.
        num_sells = sum(
            1 for t in trades if (t.get("side") or t.get("Side", "")).lower() == "sell"
        )
        out["num_round_trips"] = num_sells

    return out


def log_metrics(metrics: Dict[str, Any], prefix: str = "Backtest"):
    """Log metrics to logging in a readable block."""
    logging.info("--- %s Performance Metrics ---", prefix)
    # TODO(PR-D): Complex f-string, review manually:     logging.info(f"  Total Return: {metrics.get('total_return_pct', 0):.2f}%")
    logging.info(f"  Total Return: {metrics.get('total_return_pct', 0):.2f}%")
    if metrics.get("sharpe_ratio_annual") is not None:
        logging.info("  Sharpe Ratio (ann.): %s", metrics["sharpe_ratio_annual"])
    if metrics.get("sortino_ratio_annual") is not None:
        logging.info("  Sortino Ratio (ann.): %s", metrics["sortino_ratio_annual"])
    if metrics.get("calmar_ratio_annual") is not None:
        logging.info("  Calmar Ratio (ann.): %s", metrics["calmar_ratio_annual"])
    # TODO(PR-D): Complex f-string, review manually:     logging.info(f"  Max Drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%")
    logging.info(f"  Max Drawdown: {metrics.get('max_drawdown_pct', 0):.2f}%")
    logging.info(
        f"  Max DD Duration: {metrics.get('max_drawdown_duration_days', 0)} days"
    )
    logging.info(
        f"  Trades: {metrics.get('num_trades', 0)} (round-trips: {metrics.get('num_round_trips', 0)})"
    )
    if metrics.get("avg_trade_pnl") is not None:
        # TODO(PR-D): Complex f-string, review manually:         logging.info(f"  Avg Trade PnL: ${metrics['avg_trade_pnl']:.2f}")
        logging.info(f"  Avg Trade PnL: ${metrics['avg_trade_pnl']:.2f}")
    logging.info("-----------------------------------")
