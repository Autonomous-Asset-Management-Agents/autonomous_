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
    # rc3 live finding: a jump has CAPACITY — +5,792 of step can only BE ~5,792 of
    # settlement. Without this, a second deposit of almost the same size claimed the
    # FIRST deposit's jump (both stacked on one point: the "+$11,578" marker, the
    # +1,654% week and the -94.27% today on the real account). Each attachment
    # consumes the jump's remaining capacity; a flow with no free matching jump is
    # dropped (its own jump is not in the curve yet — the #3068 drop semantics).
    remaining = [0.0] * n
    for i in range(1, n):
        remaining[i] = float(point_equities[i]) - float(point_equities[i - 1])
    # Deterministic order: earlier activity dates claim first (their settlement is first).
    for d, amount in sorted(cf_by_date.items(), key=lambda kv: str(kv[0])[:10]):
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
            step = remaining[i]
            if (amt > 0) != (step > 0):
                continue  # sign mismatch — not this flow's settlement
            if abs(step) < FLOW_JUMP_MIN_STEP_FRACTION * abs(amt):
                continue  # drift, or a jump already claimed by an earlier flow
            diff = abs(step - amt)
            if best_diff is None or diff < best_diff:
                best_i, best_diff = i, diff
        if best_i is not None:
            out[best_i] += amt
            remaining[best_i] -= amt
        # else: no free matching jump near the activity date → not in the base yet → drop
    return out


# rc3 R2: an equity move of this relative size within one live append is a deposit/
# withdrawal settling, not a market move — the warm benchmark cache must rebuild so the
# new flow gets aligned (Net deposits / today-tile stay honest on settlement day).
SETTLEMENT_JUMP_FRACTION = 0.10


def is_settlement_scale_jump(prev_equity, new_equity) -> bool:
    """True when the equity step between two live reads is settlement-scale (>10 %).

    Fail-soft: missing/zero base → False (a fresh account must not thrash the cache)."""
    try:
        prev = float(prev_equity or 0.0)
        new = float(new_equity or 0.0)
    except (TypeError, ValueError):
        return False
    if prev <= 0.0 or new <= 0.0:
        return False
    return abs(new - prev) / prev > SETTLEMENT_JUMP_FRACTION


def flows_signature(cf_by_date) -> str:
    """rc4 R4: order-independent fingerprint of the funding cash-flows.

    Stored in the benchmark cache at rebuild time; every cached poll refetches the
    flows and compares — a NEW/changed ledger entry (a deposit posting after the
    rebuild, a broker correction) evicts the cache so the next poll rebuilds with the
    fresh flows. Without this, the flows were fetched exactly once per rebuild and a
    late-posting CSD stayed invisible indefinitely (the +95.99% "return")."""
    if not cf_by_date:
        return "none"
    parts = [
        f"{str(d)[:10]}:{round(float(v), 2)}" for d, v in sorted(cf_by_date.items())
    ]
    return "|".join(parts)


def slice_points_to_baseline(points: list, baseline_date) -> list:
    """#3071: cut an (already jump-aligned, #3068) benchmark points list down to the
    user-chosen performance baseline.

    ORDER MATTERS: callers must run ``align_cashflows_to_points`` on the FULL curve first —
    a deposit whose activity date precedes the baseline but whose settlement jump lies
    inside the window then attaches to the in-window jump exactly once, and every flow
    aligned before the baseline is simply part of the window's base (``equity[0]``).

    Rules:
    - ``baseline_date`` falsy → the untouched list (true inception, today's behaviour).
    - baseline on a gap day → the window starts at the first point ON/AFTER it.
    - baseline before the first point → clamped (full curve).
    - degenerate baseline after the curve → keep the last two points (fail-soft: the
      endpoint must never serve an empty/1-point curve that zeroes every KPI).
    """
    if not baseline_date or not points:
        return points
    base = str(baseline_date)[:10]
    idx = next(
        (i for i, p in enumerate(points) if str(p.get("date", ""))[:10] >= base),
        None,
    )
    if idx is None:  # baseline beyond the curve → minimal honest tail
        return points[-2:]
    return points[idx:]


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


# ADR-R16 (#3049 Defekt A): funding-fee plausibility bounds for a gross-vs-net
# cash-flow overshoot. Basis: Alpaca's international-wire fee schedule — 1.5 % per
# transfer, capped at $40 (ACH is free; this fee only exists on non-ACH funding).
# A small absolute tolerance covers FX-rounding on the conversion leg. Overshoots
# INSIDE these bounds are funding costs (never strategy P&L); anything beyond
# stays an honest market/trading loss — no silent beautification. Annual review.
FUNDING_FEE_PCT = 0.015
FUNDING_FEE_CAP_USD = 40.0
FUNDING_FEE_TOL_USD = 2.0


def compute_cashflow_adjusted_returns(
    dates: Sequence[str],
    equity: Sequence[float],
    cashflows: Sequence[float],
) -> dict:
    """Return ``{twr_pct, net_deposits, pnl_net_of_deposits, funding_costs}`` from a
    daily equity curve and the net external cash-flow per day.

    - ``twr_pct``: time-weighted return over the window, in percent. Sub-periods whose
      starting equity is <= 0 (e.g. funding a $0 account) have no valid base and are
      skipped — the chain continues from the next valid sub-period, so a deposit into
      an empty account is not counted as an infinite gain.
    - ``net_deposits``: sum of all cash-flows in the window (deposits positive,
      withdrawals negative).
    - ``funding_costs``: #3049 Defekt A — the fee-plausible part of gross-vs-net
      flow overshoots (international transfer fee / FX spread). The recorded
      activity amount is the GROSS transfer; equity only ever receives the NET
      credit. Stripping the gross amount booked the difference as a fake market
      loss — fatal on a small base (live 28.08.: 32.36 $ overshoot on a 350 $
      account → −9.2 % fake day, twr −5.79 % instead of +3.6 %). Owner directive
      2026-08-28: the KPI measures MACHINE performance, independent of deposit
      size and funding fees.
    - ``pnl_net_of_deposits``: ``equity[-1] - equity[0] - net_deposits +
      funding_costs`` — strategy P&L with contributed capital removed AND funding
      costs excluded (they never reached the account; they are not strategy loss).

    Fail-soft: empty or single-point inputs return zeroed metrics.
    """

    n = len(equity)
    # Align cash-flows defensively (missing tail -> no flow that day).
    cf = [float(cashflows[i]) if i < len(cashflows) else 0.0 for i in range(n)]

    net_deposits = float(sum(cf))

    if n < 2:
        return {
            "twr_pct": 0.0,
            "net_deposits": net_deposits,
            "pnl_net_of_deposits": 0.0,
            "funding_costs": 0.0,
            "effective_cashflows": [float(x or 0.0) for x in cf],
        }

    growth = 1.0
    carry = 0.0  # #3049: a flow whose equity impact lags (settlement) is deferred here
    funding_costs = 0.0
    # #3145: per-point flow as it actually hit equity; only a reclassified funding fee
    # reduces it. Gross stays in ``net_deposits`` — the two answer different questions.
    effective = [float(x or 0.0) for x in cf]
    for i in range(1, n):
        prev = float(equity[i - 1])
        if prev <= 0:
            # No valid base for this sub-period (division guard) — skip, keep chaining.
            continue
        flow = cf[i] + carry
        # #3049 Defekt A: clamp a FEE-PLAUSIBLE gross-vs-net overshoot to the day's
        # actual equity delta. Only the overshoot within the ADR-R16 bounds moves to
        # funding_costs; a larger gap is a genuine market move and stays in the chain.
        # Applied BEFORE the settlement-lag guard below: a lagged flow (delta ~ 0,
        # flow >> delta) blows past the fee bounds and falls through to the carry
        # path unchanged.
        if flow != 0.0:
            delta = float(equity[i]) - prev
            # overshoot > 0 ⇔ the gross flow exceeds its equity effect: a deposit
            # credited NET of the fee, or a withdrawal that drained fee-extra equity.
            if flow > 0.0:
                overshoot = flow - max(delta, 0.0)
            else:
                overshoot = flow - min(delta, 0.0)
            fee_bound = (
                min(FUNDING_FEE_PCT * abs(flow), FUNDING_FEE_CAP_USD)
                + FUNDING_FEE_TOL_USD
            )
            if 0.0 < overshoot <= fee_bound:
                funding_costs += overshoot
                flow -= overshoot  # clamp to the equity-effective amount
                # #3145: remember what actually reached the account at this point. The served
                # curve carries THIS number as the point's cash-flow, so the console
                # chain-links exactly the flows the engine chained — the KPI card and the hero
                # cannot drift apart (they did, by 0.0056 pp on the live vector, because only
                # the engine knew about the transfer fee).
                effective[i] -= overshoot
        market_equity = (
            float(equity[i]) - flow
        )  # strip the external flow landed on day i
        if flow != 0.0:
            # #3049: equity[i] may not reflect `flow` yet — the deposit's account-activity
            # `date` (e.g. 08-24) precedes the day its cash settles into equity (08-25).
            # Subtracting it then fabricated a catastrophic sub-return (the live -28,304 % TWR).
            # rc5 R6: the original test (only defer once the remainder goes NEGATIVE) misses the
            # common case. With prev 6,142.65, a 5,785.60 flow dated one point early and
            # equity[i] 6,143.00 the remainder is a positive 357.40 — so the day read -94.2 %
            # and the next +94 % (the "-94.27 % today" tile) although both were flat. Decide by
            # the MARKET MOVE each reading implies and keep the smaller: a real move is small
            # next to a funding transfer, so a flow that has not landed yet gives itself away by
            # implying an enormous one. `net_deposits` above already counts the flow either way —
            # only the TWR chaining is corrected here. Identical rule in the console's
            # `chainLink` (src/console/shared/twr.ts), so engine and chart cannot diverge.
            implied_if_stripped = abs(market_equity - prev)
            implied_if_deferred = abs(float(equity[i]) - prev)
            if market_equity <= 0.0 or implied_if_deferred < implied_if_stripped:
                carry = flow
                market_equity = float(equity[i])
            else:
                carry = 0.0
        else:
            carry = 0.0
        sub_return = market_equity / prev - 1.0
        growth *= 1.0 + sub_return

    twr_pct = (growth - 1.0) * 100.0
    pnl_net_of_deposits = (
        float(equity[-1]) - float(equity[0]) - net_deposits + funding_costs
    )
    return {
        "twr_pct": twr_pct,
        "net_deposits": net_deposits,
        "pnl_net_of_deposits": pnl_net_of_deposits,
        "funding_costs": funding_costs,
        # #3145: the equity-effective flow per point (gross minus the funding fee the engine
        # reclassified). ``net_deposits`` stays GROSS — the two answer different questions.
        "effective_cashflows": effective,
    }


def trim_leading_unfunded(points: list) -> list:
    """rc5 R7: drop the LEADING run of points whose equity is not positive.

    Alpaca's ``portfolio/history`` answers ``period=1W`` for the whole week even when the
    account was funded two days ago — the days before report ZERO equity. Those points are
    not history, they are the absence of an account, and they cost twice on the Overview:
    the 1W chart draws a flat line at 0 with a cliff at the funding, and the hero's money
    delta measures against 0, so the account's whole value reads as "market P&L"
    (``+$11,776.22`` next to a ``-0.53 %`` TWR — the 01.09. screenshot).

    Only the leading run is removed: an interior zero is real (a fully liquidated account)
    and stays. Fail-soft — the input is returned unchanged when trimming would leave fewer
    than two points (the chart needs two to draw a line), on an empty list, and on points
    with a missing/unparseable ``equity``.
    """
    if not points:
        return points
    first = 0
    for i, p in enumerate(points):
        try:
            eq = float((p or {}).get("equity") or 0.0)
        except (TypeError, ValueError):
            eq = 0.0
        if eq > 0.0:
            first = i
            break
    else:
        return points  # never funded in this window — nothing to anchor on
    if first == 0 or len(points) - first < 2:
        return points
    return points[first:]
