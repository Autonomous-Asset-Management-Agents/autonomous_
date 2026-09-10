"""E2E / backtest-style sizing simulation for O3 (CASH_AWARE_SLOTS_ENABLED).

Drives the REAL ``RiskManager.calculate_position_size`` and the REAL
``effective_free_slots`` divisor through the *complete* buy process of a filling /
mostly-invested book, comparing:

  * BASELINE (flag OFF) — fixed divisor ``effective_max_positions()`` = 10 (today's `/10`)
  * O3 (flag ON) — dynamic divisor ``effective_free_slots(held)`` = remaining free slots

This is the cleanest end-to-end simulation of *this* change: it exercises exactly the
sizing path O3 touches, deterministically (no ML models / market data / entitlement gate),
with the real MIN/MAX rails intact. The built-in `run_simulation` backtest is entitlement-
gated OFF on OSS/desktop and its fallback-buy path sizes with a hardcoded 2 % (not the
sizer), so it does not exercise O3.

A second block (#3135, added 2026-09-01) asks the question this file did NOT ask through the
whole #3094/#3135 incident: does the per-name differentiation REACH THE EXECUTED ORDER VALUE?
See the banner above ``Candidate`` for why the block above could never have caught it.

Human-readable trajectory:
    PYTHONPATH=. py -3 tests/e2e/test_cash_aware_slots_sizing_sim.py
Invariant assertions:
    pytest tests/e2e/test_cash_aware_slots_sizing_sim.py -o addopts=""
"""

import statistics
from types import SimpleNamespace
from typing import NamedTuple
from unittest.mock import MagicMock, patch

from core.risk_manager import RiskManager, effective_free_slots, effective_max_positions

EQUITY = 100_000.0
MAX_POSITION_PERCENT = 0.25  # config MAX rail — the sizing ceiling


def _divisor(held: int, flag_on: bool) -> int:
    """Compute the cash divisor via the REAL effective_free_slots, with the flag toggled.

    The patch is scoped to just the divisor read so calculate_position_size still sees the
    real config for its own rails.
    """
    with patch(
        "config.get_config",
        return_value=SimpleNamespace(CASH_AWARE_SLOTS_ENABLED=flag_on),
    ):
        return effective_free_slots(held)


def simulate(signals, start_cash, start_held, flag_on):
    """Run a sequence of BUY signals through the real sizer on a filling book."""
    rm = RiskManager(client=MagicMock(), total_capital=EQUITY)
    cash = float(start_cash)
    held = int(start_held)
    rows = []
    for sym, price, conv in signals:
        divisor = _divisor(held, flag_on)
        qty = rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=price * 0.03,
            confidence="high",
            size_scaler=1.0,
            market_data={"vix": 18.0},
            num_stocks_in_strategy=divisor,
            current_price=price,
            account_cash=cash,
            allow_fractional=True,
            conviction_score=conv,
        )
        notional = qty * price
        rows.append(
            {
                "sym": sym,
                "price": price,
                "held": held,
                "divisor": divisor,
                "per_slot": (cash - 1)
                / divisor,  # ADR-R09 (2026-07-27): buffer $50 → $1
                "qty": qty,
                "notional": notional,
                "pct": 100 * notional / EQUITY,
                "cash_before": cash,
            }
        )
        if qty > 0:
            cash -= notional
            held += 1
    return rows


# The operator's real pain: a mostly-invested book (8 of 10 held) with little free cash,
# and three better symbols wanting to rotate in.
ROTATION_SIGNALS = [
    ("AAPL", 150.0, 0.70),
    ("MSFT", 220.0, 0.70),
    ("NVDA", 180.0, 0.70),
]
ROTATION_START_CASH = 8_000.0
ROTATION_START_HELD = 8

# A fresh book filling from empty with ample cash.
FRESH_SIGNALS = [(f"S{i:02d}", 100.0 + 10 * i, 0.70) for i in range(10)]
FRESH_START_CASH = 100_000.0
FRESH_START_HELD = 0

# A FULL book (held == cap): a new / displacement buy must size CONSERVATIVELY (cash/cap), not all-in.
FULL_BOOK_SIGNAL = [("QQQ", 200.0, 0.70)]
FULL_BOOK_START_CASH = 8_000.0
FULL_BOOK_START_HELD = 10


# ─────────────────────────── invariant assertions (pytest) ───────────────────────────
def test_o3_rails_hold_and_deploys_more_of_the_same_cash():
    """Rails hold on BOTH tracks and O3 deploys more of the same free cash than baseline.

    NOTE the per-row size is NOT monotone across the two runs: O3 front-loads (bigger early
    buys), so its cash depletes faster and a *later* O3 buy can be smaller (or 0) than the
    baseline's — the trajectories diverge. The defensible invariants are (a) the first buy,
    where both start from the identical state, (b) the total deployed, and (c) the rails.
    """
    base = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, False)
    o3 = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, True)
    ceiling = MAX_POSITION_PERCENT * EQUITY * 1.001
    # (c) rails hold on every buy of both tracks: never over the MAX cap, never negative.
    for row in base + o3:
        assert row["qty"] >= 0, row
        assert row["notional"] <= ceiling, row
    # (a) identical starting state → O3's first buy >= baseline's first buy.
    assert o3[0]["notional"] + 1e-6 >= base[0]["notional"]
    # (b) O3 deploys more of the same cash, and never more than the cash (no leverage).
    base_total = sum(r["notional"] for r in base)
    o3_total = sum(r["notional"] for r in o3)
    assert o3_total + 1e-6 >= base_total
    assert o3_total <= ROTATION_START_CASH + 1e-6


def test_o3_materially_larger_on_mostly_invested_book():
    """On a mostly-invested book the first rotation buy is materially bigger under O3
    (fixed /10 → dust; /remaining-slots → proper size)."""
    base = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, False)
    o3 = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, True)
    # held=8, cap=10 → baseline divisor 10, O3 divisor 2 → ~5x the per-slot reservation.
    assert o3[0]["notional"] >= 2.0 * base[0]["notional"]


def test_fresh_empty_book_first_buy_is_byte_identical():
    """With an empty book (held=0) effective_free_slots == cap, so O3's first buy equals
    the baseline exactly — the change is inert until the book starts filling."""
    base = simulate(FRESH_SIGNALS, FRESH_START_CASH, FRESH_START_HELD, False)
    o3 = simulate(FRESH_SIGNALS, FRESH_START_CASH, FRESH_START_HELD, True)
    assert base[0]["divisor"] == o3[0]["divisor"] == effective_max_positions()
    assert abs(o3[0]["notional"] - base[0]["notional"]) < 1e-6


def test_full_book_sizes_conservatively_not_all_in():
    """O3-SAFETY (adversarial review): on a FULL book (held == cap) the new/displacement buy is sized at
    the conservative cash/cap divisor — IDENTICAL to the flag-OFF baseline, NOT all-in (cash/1). Without
    the remaining<=0 -> cap fix this buy would be ~cap x larger (max concentration)."""
    base = simulate(FULL_BOOK_SIGNAL, FULL_BOOK_START_CASH, FULL_BOOK_START_HELD, False)
    o3 = simulate(FULL_BOOK_SIGNAL, FULL_BOOK_START_CASH, FULL_BOOK_START_HELD, True)
    assert o3[0]["divisor"] == base[0]["divisor"] == effective_max_positions()
    assert abs(o3[0]["notional"] - base[0]["notional"]) < 1e-6


def test_paper_equals_live_sizer_is_mode_neutral():
    """calculate_position_size takes no paper/live flag — identical inputs give identical
    sizes regardless of account mode (Paper == Live, byte-identical)."""
    a = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, True)
    b = simulate(ROTATION_SIGNALS, ROTATION_START_CASH, ROTATION_START_HELD, True)
    assert [r["notional"] for r in a] == [r["notional"] for r in b]


# ══════════════════════════════════════════════════════════════════════════════════════
# #3135 — DOES THE DIFFERENTIATION REACH THE ORDER?
# ══════════════════════════════════════════════════════════════════════════════════════
#
# WHY THIS BLOCK EXISTS. Everything above drives the exact function (`calculate_position_size`)
# in the exact regime the #3135 defect lives in (`ROTATION_*`: 8 of 10 held, $8k cash — the
# cash cap binds) and stayed GREEN through the whole incident. It could not have gone red:
# it neutralises, by construction, all three inputs the differentiation is made of —
#
#     ROTATION_SIGNALS  → conviction 0.70 for every symbol   (no spread)
#     simulate()        → size_scaler=1.0                    (pinned neutral)
#     simulate()        → forecast_vol never passed → None   → vol_targeting_scaler = 1.0
#
# — and then asserts only AGGREGATES (rails hold, total deployed, no leverage). A test cannot
# lose a spread it never created. #3094 shipped a per-name size differentiation that was dead
# on arrival in production (8 buys, risk_size_scaler 0.50–1.089, all filled ≈$1,005) and no
# test in the suite noticed, because every test that touched this path was built like that.
#
# THE RULE THIS ENCODES (RCA_2026_08_31_SLOT_CASH_FLATTENING.md §5): a sizing change is
# verified when the EXECUTED ORDER VALUES differ — not when a score, a scaler or a forecast
# differs. Scaler, conviction and forecast_vol are INPUTS. The terminal observable is the
# dollar amount that goes to the broker. So this block asserts on the spread of notionals.
#
# RED→GREEN. The fix (#3136) is flag-gated, so both arms run here in one file:
#   PROPORTIONAL_SLOT_CASH_ENABLED OFF → the defect, reproduced and pinned (all equal)
#   PROPORTIONAL_SLOT_CASH_ENABLED ON  → differentiation survives to the order value
# Flipping the flag off in config must turn `test_proportional_split_reaches_the_order_value`
# red. That is the whole point of the pair.


class Candidate(NamedTuple):
    """One BUY candidate of a cycle, with BOTH differentiating inputs varied on purpose."""

    sym: str
    price: float
    conviction: float
    forecast_vol: float  # daily stdev; VOL_TARGET_DAILY_VOL default 0.015 = neutral


# The live shape of 2026-08-31: several BUYs of ONE cycle are sized against the SAME cash
# pool (no fill has settled yet), on a mostly-invested book. Both inputs are spread so that
# the demand ordering is unambiguous WITHOUT re-implementing the sizing formula:
#   CALM  — highest conviction AND calmest  → largest demand
#   WILD  — lowest conviction  AND wildest  → smallest demand
# Prices differ deliberately: when the cash cap binds, notional = per_slot × fraction and is
# price-independent, so a price effect would show up as a broken ordering, not as a pass.
CYCLE_CANDIDATES = [
    Candidate("CALM", 150.0, 0.90, 0.008),
    Candidate("MID", 220.0, 0.70, 0.015),
    Candidate("WILD", 180.0, 0.50, 0.030),
]
CYCLE_CASH = 8_000.0
# 7 of 10 held → effective_free_slots == 3 == len(CYCLE_CANDIDATES). The match is deliberate:
# the equal split reserves (cash-buffer)/slots PER BUY, so a cycle with more candidates than
# free slots is over-subscribed by construction and would fail the no-leverage assertion for
# a reason that has nothing to do with the differentiation. The live incident had the same
# shape (8 concurrent buys, 8 slots' worth of cash, all filled ≈$1,005).
CYCLE_HELD = 7
CASH_BUFFER = 1.0  # ADR-R09: RISK_CASH_BUFFER_USD
# #3136's demand fraction is `min(1, target_value / (total_capital * MAX_POSITION_PERCENT_SIZING))`.
SIZING_REFERENCE = EQUITY * 0.30  # MAX_POSITION_PERCENT_SIZING default


def simulate_cycle(candidates, cash, held, proportional):
    """Size every candidate of ONE cycle against the SAME cash/held state.

    This is the production shape the incident was measured in — and the one thing
    ``simulate()`` above does NOT model: it drains cash sequentially, so its rows differ for
    a reason (depleting cash) that has nothing to do with the differentiation under test.
    Here the state is held constant across the cycle, so any spread in the notionals can
    only come from conviction/forecast_vol.
    """
    rm = RiskManager(client=MagicMock(), total_capital=EQUITY)
    divisor = _divisor(held, True)  # CASH_AWARE_SLOTS ON — today's production divisor
    rows = []
    for cand in candidates:
        with patch(
            "config.PROPORTIONAL_SLOT_CASH_ENABLED", proportional, create=True
        ), patch("config.VOL_TARGETING_SIZING_ENABLED", True, create=True):
            qty = rm.calculate_position_size(
                stop_loss_atr_multiplier=3.0,
                atr=cand.price * 0.03,
                confidence="high",
                size_scaler=1.0,
                market_data={"vix": 18.0},
                num_stocks_in_strategy=divisor,
                current_price=cand.price,
                account_cash=cash,
                allow_fractional=True,
                conviction_score=cand.conviction,
                forecast_vol=cand.forecast_vol,
            )
        rows.append(
            {
                "sym": cand.sym,
                "qty": qty,
                "notional": qty * cand.price,
                "divisor": divisor,
                "per_slot": (cash - CASH_BUFFER) / divisor,
            }
        )
    return rows


def _notionals(rows):
    return [r["notional"] for r in rows]


# Fractional shares are rounded before they become a notional, and the candidates carry
# different prices, so two orders that are "the same dollar amount" land a few hundredths
# apart. One cent is the resolution the broker (and the incident) works in.
CENT = 0.01


def _spread(rows):
    """Coefficient of variation of the executed order values — 0.0 means flattened."""
    vals = _notionals(rows)
    mean = statistics.fmean(vals)
    return 0.0 if mean <= 0 else statistics.pstdev(vals) / mean


def test_the_fixture_actually_varies_the_differentiating_inputs():
    """THE META-TEST — the one that would have caught this class of blindness.

    The reason the suite above never went red is not a missing assertion, it is a fixture
    that pins conviction, size_scaler and forecast_vol to constants. Any future sizing test
    that stops varying them degrades silently into an aggregate smoke test. This makes that
    degradation a failure instead.
    """
    assert (
        len({c.conviction for c in CYCLE_CANDIDATES}) > 1
    ), "conviction is constant across the cycle — the cap has nothing to flatten"
    assert (
        len({c.forecast_vol for c in CYCLE_CANDIDATES}) > 1
    ), "forecast_vol is constant across the cycle — #3094's input is neutralised"
    assert any(
        c.forecast_vol != 0.015 for c in CYCLE_CANDIDATES
    ), "every forecast_vol sits at VOL_TARGET_DAILY_VOL → vol_targeting_scaler is 1.0 for all"


def test_equal_split_flattens_the_cycle_this_is_the_3135_defect():
    """Flag OFF reproduces the incident: three very different names, one order value.

    Pinned deliberately. This is the RED arm — if it ever stops flattening, the equal-split
    path changed and the ON arm below is no longer proving anything.
    """
    rows = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=False)
    vals = _notionals(rows)
    assert (
        min(vals) > 0
    ), f"the cycle bought nothing — wrong regime, not a flattening: {rows}"
    assert max(vals) - min(vals) < CENT, (
        "expected the equal split to flatten every order to the same dollar amount "
        f"(the observed defect: 8 buys, scaler 0.50-1.089, all ~$1,005) — got {vals}"
    )


def test_proportional_split_reaches_the_order_value():
    """THE TERMINAL ASSERTION (#3136 GREEN arm).

    Not "the scaler differs" and not "the target value differs" — the EXECUTED order value
    differs, downstream of the constraint that was binding. This is the criterion #3094
    should have been held to.
    """
    rows = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=True)
    vals = _notionals(rows)
    assert min(vals) > 0, f"the cycle bought nothing: {rows}"
    assert _spread(rows) > 0.10, (
        "the IV/conviction differentiation did NOT reach the order value — the orders are "
        f"(near-)identical despite spread conviction and forecast_vol: {vals}"
    )


def test_order_values_follow_the_demand_ordering():
    """Direction, not just magnitude: the wild low-conviction name must get the least.

    A spread alone could be noise or an artefact of price; this pins the sign. The ordering
    is derived from the two inputs (CALM dominates WILD on BOTH), so the test does not
    re-implement the sizing formula it is checking. CALM vs MID is `>=`, not `>` — see
    `test_the_demand_fraction_saturates_above_the_sizing_reference` for why.
    """
    by_sym = {
        r["sym"]: r["notional"]
        for r in simulate_cycle(
            CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=True
        )
    }
    assert by_sym["CALM"] >= by_sym["MID"] > by_sym["WILD"], (
        "expected calm/high-conviction >= mid > wild/low-conviction in EXECUTED order value, "
        f"got {by_sym}"
    )


def test_the_demand_fraction_saturates_above_the_sizing_reference():
    """KNOWN LIMIT of #3136 — surfaced by this test layer, not a failure of it.

    The fraction is `min(1, target_value / (total_capital * MAX_POSITION_PERCENT_SIZING))`,
    bounded at 1.0 so the cap can only ever SHRINK the equal split (the no-leverage axiom).
    The price of that bound: every name whose target value reaches the reference gets the
    FULL equal slot, so two strong names still receive an identical amount. Measured here:

        CALM  target $56,250  fraction 1.00  ─┐ identical order value
        MID   target $33,750  fraction 1.00  ─┘
        WILD  target $13,125  fraction 0.44   ← the only one differentiated

    With ``confidence="high"`` (scaler 1.5) the reference is reached from roughly
    conviction >= 0.5, i.e. across most of the band the sizer actually trades in. #3136
    therefore de-flattens the WEAK tail, not the strong cluster where the alpha claim of
    #3094 lives. Pinned so a future re-scaling of the reference is a deliberate decision
    with a red test, not a silent change.
    """
    assert (
        SIZING_REFERENCE == 30_000.0
    ), "the reference moved — re-derive the numbers above"
    rows = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=True)
    by_sym = {r["sym"]: r["notional"] for r in rows}
    assert abs(by_sym["CALM"] - by_sym["MID"]) < CENT, (
        "the saturation is gone — either MAX_POSITION_PERCENT_SIZING, the confidence "
        f"scaler or the bound changed. Re-derive the limit before adjusting: {by_sym}"
    )
    per_slot = rows[0]["per_slot"]
    assert abs(by_sym["CALM"] - per_slot) < CENT, (
        f"a saturated name must receive the FULL equal slot (${per_slot:,.2f}), "
        f"got ${by_sym['CALM']:,.2f}"
    )


def test_no_leverage_across_the_whole_concurrent_cycle():
    """THE SAFETY AXIOM at cycle level — the invariant the fix must never buy its spread with.

    The unit tests check no-leverage per call. The failure mode that matters here is
    collective: several concurrent budgets that are each legal but sum past the cash. The
    proportional fraction is bounded to [0,1] and can only SHRINK the equal split, so the
    ON arm must stay at or below the OFF arm, symbol by symbol and in total.
    """
    off = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=False)
    on = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=True)
    assert (
        sum(_notionals(on)) <= CYCLE_CASH - CASH_BUFFER + 1e-6
    ), f"the cycle's concurrent budgets exceed the free cash: {_notionals(on)}"
    for row_on, row_off in zip(on, off):
        assert row_on["notional"] <= row_off["notional"] + 1e-6, (
            f"{row_on['sym']}: the proportional split ENLARGED the cap "
            f"({row_on['notional']:.2f} > {row_off['notional']:.2f}) — it may only shrink"
        )


# ─────────────────────────── human-readable trajectory (script) ──────────────────────
def _print_cycle():
    off = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=False)
    on = simulate_cycle(CYCLE_CANDIDATES, CYCLE_CASH, CYCLE_HELD, proportional=True)
    print(
        "\n=== #3135: one cycle, one cash pool — does the spread reach the order? ==="
    )
    print(
        f"    equity=${EQUITY:,.0f}  held={CYCLE_HELD}  free cash=${CYCLE_CASH:,.0f}  "
        f"divisor={off[0]['divisor']}  per-slot=${off[0]['per_slot']:,.0f}"
    )
    print(
        f"{'sym':<6}{'conv':>6}{'fc_vol':>8}"
        f"{'   | EQUAL SPLIT':<20}{'   | PROPORTIONAL':<20}"
    )
    for cand, b, o in zip(CYCLE_CANDIDATES, off, on):
        print(
            f"{cand.sym:<6}{cand.conviction:>6.2f}{cand.forecast_vol:>8.3f}"
            f"   {b['notional']:>16,.0f}   {o['notional']:>16,.0f}"
        )
    print(
        f"    spread (CV of order values): equal={_spread(off):.4f}  "
        f"proportional={_spread(on):.4f}"
    )


def _print_scenario(title, signals, start_cash, start_held):
    base = simulate(signals, start_cash, start_held, False)
    o3 = simulate(signals, start_cash, start_held, True)
    print(f"\n=== {title} ===")
    print(
        f"    equity=${EQUITY:,.0f}  cap={effective_max_positions()} positions  "
        f"start: held={start_held}, free cash=${start_cash:,.0f}"
    )
    hdr = (
        f"{'sym':<6}{'held':>5}{'price':>9}"
        f"{'   | BASELINE /10':<26}{'   | O3 /remaining':<26}"
    )
    print(hdr)
    print(
        f"{'':6}{'':5}{'':9}   {'div':>4}{'notional':>12}{'%eq':>7}"
        f"   {'div':>4}{'notional':>12}{'%eq':>7}"
    )
    base_total = o3_total = 0.0
    base_dust = o3_dust = 0
    for b, o in zip(base, o3):
        print(
            f"{b['sym']:<6}{b['held']:>5}{b['price']:>9.2f}"
            f"   {b['divisor']:>4}{b['notional']:>12,.0f}{b['pct']:>6.1f}%"
            f"   {o['divisor']:>4}{o['notional']:>12,.0f}{o['pct']:>6.1f}%"
        )
        base_total += b["notional"]
        o3_total += o["notional"]
        base_dust += 1 if 0 < b["notional"] < 50 else 0
        o3_dust += 1 if 0 < o["notional"] < 50 else 0
    print(
        f"{'TOTAL':<6}{'':5}{'':9}   {'':4}{base_total:>12,.0f}{'':7}"
        f"   {'':4}{o3_total:>12,.0f}"
    )
    print(
        f"    deployed: baseline ${base_total:,.0f}  vs  O3 ${o3_total:,.0f}  "
        f"({o3_total / max(base_total, 1e-9):.1f}x)"
    )


if __name__ == "__main__":
    _print_scenario(
        "Mostly-invested book, little cash (the operator's pain)",
        ROTATION_SIGNALS,
        ROTATION_START_CASH,
        ROTATION_START_HELD,
    )
    _print_scenario(
        "Fresh book filling from empty (ample cash)",
        FRESH_SIGNALS,
        FRESH_START_CASH,
        FRESH_START_HELD,
    )
    _print_cycle()
    print(
        "\n(run `pytest tests/e2e/test_cash_aware_slots_sizing_sim.py` for the "
        "invariant assertions)"
    )
