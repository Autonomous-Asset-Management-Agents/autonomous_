# flake8: noqa
# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management
# Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# core/engine/perf_metrics.py
"""Cash-flow-adjusted return metrics for the /benchmark-equity KPIs.

A deposit or withdrawal is external capital, not performance — it must NOT move the
reported return. The headline metric is the **Time-Weighted Return (TWR)**: the
equity series is broken at each cash-flow into sub-periods whose returns are
chain-linked geometrically, so the timing and size of deposits/withdrawals are
neutralised. TWR is the only measure commensurable with the SPY benchmark (which is
itself cash-flow-free). Alongside it we expose the € view: ``net_deposits`` (sum of
external flows) and ``pnl_net_of_deposits`` (equity change minus those flows).

Timing convention: ``cashflows[i]`` is the NET external flow that landed ON day ``i``
(deposit positive, withdrawal negative) and is already reflected in ``equity[i]``.
The market return of sub-period ``i-1 -> i`` is ``(equity[i] - cashflows[i]) / equity[i-1] - 1``.
Day 0 is inception (``equity[0]`` = starting capital, ``cashflows[0]`` normally 0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping, Sequence


def net_cashflow_by_date(
    timestamps: Sequence[int],
    cashflow: Mapping,
) -> dict:
    """Collapse Alpaca ``PortfolioHistory.cashflow`` into ``{"YYYY-MM-DD": net_flow}`` (UTC).

    Alpaca returns ``cashflow`` as ``Dict[ActivityType, List[float]]`` — one signed
    amount per ``timestamp`` entry, per activity type (``CSD`` cash deposit +,
    ``CSW`` cash withdrawal −). We sum across activity types per index and across
    multiple flows landing on the same day. Empty/missing input yields ``{}``.
    """
    out: dict = {}
    if not timestamps or not cashflow:
        return out
    for k, ts in enumerate(timestamps):
        day = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        net = 0.0
        for _activity_type, amounts in cashflow.items():
            if k < len(amounts) and amounts[k] is not None:
                net += float(amounts[k])
        out[day] = out.get(day, 0.0) + net
    return out


# #3049: the non-trade account-activity types that move EXTERNAL capital (and so must be
# stripped from the return/drawdown, never read as strategy P&L). CSD = cash deposit (+),
# CSW = cash withdrawal (−), FEE = a transfer/funding fee (−, e.g. the 1.5%-capped-$40
# international Currencycloud fee). Any other type is NOT a funding flow and is skipped.
FUNDING_ACTIVITY_TYPES = ("CSD", "CSW", "FEE")


def net_activities_by_date(activities) -> dict:
    """Collapse Alpaca **account-activities** into ``{"YYYY-MM-DD": net_flow}``.

    This is the source that ACTUALLY returns deposits/withdrawals/fees. ``get_portfolio_history``'s
    ``cashflow`` field is empty in practice (verified against a live account — the #2718/#2735 bug:
    ``net_deposits`` stayed 0 because that field never populated). Each activity is a dict with
    ``activity_type``, ``date``, and ``net_amount`` (string).

    Sign is enforced from ``activity_type`` so Alpaca's own convention can never flip it:
    ``CSD`` adds; ``CSW``/``FEE`` subtract (#3049 — a funding fee reduces external capital and must
    not read as an investment loss). An **unrecognised** type is skipped, not guessed (the old
    ``else -1`` silently signed anything-not-CSD negative). Malformed rows are skipped (fail-soft).
    """
    out: dict = {}
    for a in activities or []:
        try:
            day = str(a.get("date") or "")[:10]
            if not day:
                continue
            t = str(a.get("activity_type", "")).upper()
            if t == "CSD":
                sign = 1.0
            elif t in ("CSW", "FEE"):
                sign = -1.0
            else:
                continue  # #3049: not a funding flow → don't guess its sign
            amount = abs(float(a.get("net_amount")))
            out[day] = out.get(day, 0.0) + sign * amount
        except (TypeError, ValueError, AttributeError):
            continue
    return out


def bucket_cashflows_to_points(
    point_dates: Sequence[str],
    cf_by_date: Mapping,
) -> list:
    """Attribute dated cash-flows to a sparse points curve → a per-point flow list (#2717).

    The equity curve can be sparse (e.g. LIVE mode builds it from ``PortfolioSnapshot`` rows, so a
    day when the engine was off has NO point). A deposit that landed on such a gap day must NOT be
    dropped: it belongs to the sub-period that *spans* it. So each flow at date ``d`` is assigned to
    the FIRST point whose date is ``>= d`` — the end of the interval that contains the deposit. That
    makes ``compute_cashflow_adjusted_returns`` strip it from exactly that sub-period.

    Rules (dates are ISO ``YYYY-MM-DD`` → lexical compare = chronological):
    - ``d <= point_dates[0]`` → dropped: a flow on/before the first tracked point is part of the
      inception base (already in ``equity[0]``), not a tracked deposit "since inception".
    - ``d > point_dates[-1]`` → attached to the last point (keeps it in ``net_deposits``).
    - otherwise → first point index ``i`` with ``point_dates[i] >= d``.

    Returns a list aligned to ``point_dates`` (all-zero for empty input).
    """
    n = len(point_dates)
    out = [0.0] * n
    if n == 0 or not cf_by_date:
        return out
    first = point_dates[0]
    for d, amount in cf_by_date.items():
        amt = float(amount)
        if amt == 0.0 or d <= first:
            continue  # zero flow, or part of the inception base → not a tracked deposit
        idx = next((i for i in range(n) if point_dates[i] >= d), n - 1)
        out[idx] += amt
    return out


def bucket_cashflows_to_points_intraday(
    point_dates: Sequence[str],
    point_equities: Sequence[float],
    cf_by_date: Mapping,
    lag_days: int = 4,
) -> list:
    """Intraday variant of ``bucket_cashflows_to_points`` that anchors a settlement-lagged
    deposit to the EQUITY JUMP rather than to its (earlier) account-activity date (#3049).

    The 1D/1W chart is a ROLLING window, not an inception curve, so the ``d <= first`` "inception
    base" drop is wrong here: a deposit whose Alpaca ``date`` is 08-24 but whose cash settles into
    equity on 08-25 belongs *inside* the window. But it must land on the point where the equity
    actually rose (post-jump) — attaching it to the pre-jump first point would strip it from a base
    that goes negative (the same failure Root B fixes for the daily TWR).

    Rules per flow at date ``d``:
    - ``d`` on/after the window's first DATE → first point whose date ``>= d`` (as the daily bucketer).
    - ``d`` before the window but within ``lag_days`` → the point with the largest same-sign equity
      step (the settlement jump); if there is no such step, dropped (can't place it safely).
    - ``d`` older than ``lag_days`` before the window → dropped: already baked into the opening equity.
    """
    from datetime import date

    n = len(point_dates)
    out = [0.0] * n
    if n == 0 or not cf_by_date:
        return out
    first_date = str(point_dates[0])[:10]

    def _largest_step_index(sign: float):
        best_i, best_step = None, 0.0
        for i in range(1, n):
            step = (float(point_equities[i]) - float(point_equities[i - 1])) * sign
            if step > best_step:
                best_step, best_i = step, i
        return best_i

    for d, amount in cf_by_date.items():
        amt = float(amount)
        if amt == 0.0:
            continue
        dd = str(d)[:10]
        if dd >= first_date:
            idx = next((i for i in range(n) if str(point_dates[i])[:10] >= dd), n - 1)
            out[idx] += amt
            continue
        try:
            gap = (date.fromisoformat(first_date) - date.fromisoformat(dd)).days
        except ValueError:
            continue
        if gap > lag_days:
            continue  # baked into the opening equity
        j = _largest_step_index(1.0 if amt > 0 else -1.0)
        if j is not None:
            out[j] += amt
    return out


def compute_cashflow_adjusted_returns(
    dates: Sequence[str],
    equity: Sequence[float],
    cashflows: Sequence[float],
) -> dict:
    """Return ``{twr_pct, net_deposits, pnl_net_of_deposits}`` from a daily equity curve
    and the net external cash-flow per day.

    - ``twr_pct``: time-weighted return over the window, in percent. Sub-periods whose
      starting equity is <= 0 (e.g. funding a $0 account) have no valid base and are
      skipped — the chain continues from the next valid sub-period, so a deposit into
      an empty account is not counted as an infinite gain.
    - ``net_deposits``: sum of all cash-flows in the window (deposits positive,
      withdrawals negative).
    - ``pnl_net_of_deposits``: ``equity[-1] - equity[0] - net_deposits`` — the P&L
      attributable to the strategy, with contributed/withdrawn capital removed.

    Fail-soft: empty or single-point inputs return zeroed metrics.
    """

    n = len(equity)
    # Align cash-flows defensively (missing tail -> no flow that day).
    cf = [float(cashflows[i]) if i < len(cashflows) else 0.0 for i in range(n)]

    net_deposits = float(sum(cf))
    pnl_net_of_deposits = (
        float(equity[-1]) - float(equity[0]) - net_deposits if n >= 2 else 0.0
    )

    if n < 2:
        return {
            "twr_pct": 0.0,
            "net_deposits": net_deposits,
            "pnl_net_of_deposits": pnl_net_of_deposits,
        }

    growth = 1.0
    carry = 0.0  # #3049: a flow whose equity impact lags (settlement) is deferred here
    for i in range(1, n):
        prev = float(equity[i - 1])
        if prev <= 0:
            # No valid base for this sub-period (division guard) — skip, keep chaining.
            continue
        flow = cf[i] + carry
        market_equity = (
            float(equity[i]) - flow
        )  # strip the external flow landed on day i
        if market_equity <= 0.0 and flow != 0.0:
            # #3049: equity[i] does not yet reflect `flow` — the deposit's account-activity
            # `date` (e.g. 08-24) precedes the day its cash settles into equity (08-25).
            # Subtracting it here fabricated a catastrophic negative sub-return (the live
            # -28,304 % TWR). Defer the flow to the next sub-period; treat this day flow-free.
            # `net_deposits` above already counts it — only the TWR chaining is corrected.
            carry = flow
            market_equity = float(equity[i])
        else:
            carry = 0.0
        sub_return = market_equity / prev - 1.0
        growth *= 1.0 + sub_return

    twr_pct = (growth - 1.0) * 100.0
    return {
        "twr_pct": twr_pct,
        "net_deposits": net_deposits,
        "pnl_net_of_deposits": pnl_net_of_deposits,
    }
