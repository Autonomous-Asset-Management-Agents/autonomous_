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


# #3049: the account-activity types that CAN move EXTERNAL capital. CSD = cash deposit (+),
# CSW = cash withdrawal (−). FEE is fetched too, but is only external capital when it is the
# TRANSFER cost of a nearby deposit/withdrawal (see net_activities_by_date) — otherwise it is a
# routine trading cost that belongs in the strategy's P&L. Any other type is not a funding flow.
FUNDING_ACTIVITY_TYPES = ("CSD", "CSW", "FEE")

# #3054-fix: a wire/settlement fee (e.g. the 1.5%-capped-$40 international Currencycloud fee) posts
# within a few days of its transfer. A FEE this close to a CSD/CSW is that transfer's cost and is
# folded into the deposit's NET capital; a FEE further from any transfer is a routine trading cost.
FEE_TRANSFER_WINDOW_DAYS = 4


def net_activities_by_date(activities) -> dict:
    """Collapse Alpaca **account-activities** into ``{"YYYY-MM-DD": net_flow}`` of EXTERNAL capital.

    This is the source that ACTUALLY returns deposits/withdrawals/fees. ``get_portfolio_history``'s
    ``cashflow`` field is empty in practice (verified against a live account — the #2718/#2735 bug:
    ``net_deposits`` stayed 0 because that field never populated). Each activity is a dict with
    ``activity_type``, ``date``, and ``net_amount`` (string).

    Sign is enforced from ``activity_type`` so Alpaca's own convention can never flip it: ``CSD``
    adds, ``CSW`` subtracts. An **unrecognised** type is skipped, not guessed (the old ``else -1``
    silently signed anything-not-CSD negative). Malformed rows are skipped (fail-soft).

    **FEE classification (#3054-fix).** #3049 signed EVERY ``FEE`` negative, which both removed
    routine trading fees from the deposit-neutral return AND painted a chart marker per fee
    (the "−$0/−$1/−$2" clutter). Correct behaviour: a ``FEE`` within ``FEE_TRANSFER_WINDOW_DAYS`` of
    a CSD/CSW is that transfer's cost → it reduces that transfer's NET capital (net = deposit − fee).
    A ``FEE`` with no nearby transfer is a routine trading cost → it is NOT a funding flow (it stays
    in equity and is therefore counted as strategy P&L/return, never stripped).
    """
    from datetime import date

    # `capital` holds the CSD/CSW external-capital events; `fees` are (iso_day, amount>0) rows,
    # classified against `capital` below (transfer cost → fold in; routine cost → drop).
    capital: dict = {}
    fees: list = []
    for a in activities or []:
        try:
            day = str(a.get("date") or "")[:10]
            if not day:
                continue
            t = str(a.get("activity_type", "")).upper()
            amount = abs(float(a.get("net_amount")))
            if t == "CSD":
                capital[day] = capital.get(day, 0.0) + amount
            elif t == "CSW":
                capital[day] = capital.get(day, 0.0) - amount
            elif t == "FEE":
                fees.append((day, amount))
            # else: not a funding flow → skip (don't guess its sign)
        except (TypeError, ValueError, AttributeError):
            continue

    def _nearest_transfer_day(fee_day: str):
        """The capital-event day within the window closest to ``fee_day`` (ties → earliest), or
        ``None`` when no transfer is near → the fee is a routine trading cost."""
        try:
            fd = date.fromisoformat(fee_day)
        except ValueError:
            return None
        best_day = None
        best_gap = None
        for cday in capital:
            try:
                gap = abs((date.fromisoformat(cday) - fd).days)
            except ValueError:
                continue
            if gap > FEE_TRANSFER_WINDOW_DAYS:
                continue
            if (
                best_gap is None
                or gap < best_gap
                or (gap == best_gap and cday < best_day)
            ):
                best_day, best_gap = cday, gap
        return best_day

    for fee_day, fee_amt in fees:
        cday = _nearest_transfer_day(fee_day)
        if cday is not None:  # transfer fee → reduces that deposit's net capital
            capital[cday] = capital.get(cday, 0.0) - fee_amt
        # else: routine trading fee → not a funding flow (stays in P&L)
    return capital


# #3068: how close (in days) a flow's activity date must be to its equity jump. Alpaca's
# account-activity date can precede the settlement into equity by a couple of days
# (international transfers); 4 days covers a weekend-spanning settlement.
FLOW_JUMP_LAG_DAYS = 4

# #3068: an equity step must be at least this fraction of the flow to count as its
# settlement jump — a +$2 market drift can never "be" a +$5,793 deposit.
FLOW_JUMP_MIN_STEP_FRACTION = 0.5


def align_cashflows_to_points(
    point_dates: Sequence[str],
    point_equities: Sequence[float],
    cf_by_date: Mapping,
    lag_days: int = FLOW_JUMP_LAG_DAYS,
) -> list:
    """Attribute each external cash-flow to the equity point whose JUMP matches it (#3068).

    Date-based attribution (``bucket_cashflows_to_points``) misassigns settlement-lagged
    deposits: Alpaca dates the activity on day D but the cash reaches equity on D+1..D+lag.
    Stripping the flow from the wrong sub-period fabricated, on a real live account:
    today −94.27 % (yesterday's settled deposit re-attached into today's window), max
    drawdown −50.91 % and inception −22.94 % (a deposit stripped one day before its jump).
    The rc1 carry-forward guard only fired when ``equity − flow <= 0`` — every wrong-base
    case that stayed positive sailed through.

    Rules per flow at activity date ``d`` (ISO date or datetime; the date part is used):
    - Candidate points: index ``i >= 1`` whose date is within ``±lag_days`` of ``d`` and whose
      step ``equity[i] − equity[i−1]`` has the flow's sign and ``|step| >=``
      ``FLOW_JUMP_MIN_STEP_FRACTION × |flow|`` (a settlement jump, not market drift).
    - The flow lands on the candidate whose step is CLOSEST to the flow (ties → earliest).
    - No candidate → the flow is already inside the curve's base (settled before the first
      point, or its jump is outside this window) → dropped. Attaching it anywhere else is
      what fabricated the −94 % day.

    Returns a list aligned to ``point_dates`` (all-zero for empty input).
    """
    from datetime import date as _date

    n = len(point_dates)
    out = [0.0] * n
    if n == 0 or not cf_by_date:
        return out

    def _iso(v):
        try:
            return _date.fromisoformat(str(v)[:10])
        except ValueError:
            return None

    pdates = [_iso(x) for x in point_dates]
    for d, amount in cf_by_date.items():
        amt = float(amount)
        fd = _iso(d)
        if amt == 0.0 or fd is None:
            continue
        best_i = None
        best_diff = None
        for i in range(1, n):
            if pdates[i] is None:
                continue
            if abs((pdates[i] - fd).days) > lag_days:
                continue
            step = float(point_equities[i]) - float(point_equities[i - 1])
            if (amt > 0) != (step > 0):
                continue  # sign mismatch — not this flow's settlement
            if abs(step) < FLOW_JUMP_MIN_STEP_FRACTION * abs(amt):
                continue  # market drift, not a settlement jump
            diff = abs(step - amt)
            if best_diff is None or diff < best_diff:
                best_i, best_diff = i, diff
        if best_i is not None:
            out[best_i] += amt
        # else: no matching jump near the activity date → already in the base → drop
    return out


def bucket_cashflows_to_points(
    point_dates: Sequence[str],
    cf_by_date: Mapping,
) -> list:
    """Attribute dated cash-flows to a sparse points curve → a per-point flow list (#2717).

    ⚠️ #3068: superseded by ``align_cashflows_to_points`` for every production call site —
    date-based attribution fabricates huge negative sub-returns for settlement-lagged
    deposits. Kept only for backwards compatibility of its documented semantics.

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

    #3068: now a thin wrapper over ``align_cashflows_to_points`` — the shared jump-matcher.
    The old rules ("in-window → attach by date; pre-window → largest same-sign step") were
    the −94.27 %-today bug: yesterday's settled deposit (already inside the opening equity)
    was strapped onto a tiny +$0.6 intraday step and then stripped from today's return. The
    matcher only attaches a flow to a step that can actually BE its settlement (same sign,
    ``>= 50 %`` of the flow) and drops it otherwise.
    """
    return align_cashflows_to_points(
        point_dates, point_equities, cf_by_date, lag_days=lag_days
    )


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
