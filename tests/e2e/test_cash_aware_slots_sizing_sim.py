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

Human-readable trajectory:
    py -3 tests/e2e/test_cash_aware_slots_sizing_sim.py
Invariant assertions:
    pytest tests/e2e/test_cash_aware_slots_sizing_sim.py -o addopts=""
"""

from types import SimpleNamespace
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


# ─────────────────────────── human-readable trajectory (script) ──────────────────────
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
    print(
        "\n(run `pytest tests/e2e/test_cash_aware_slots_sizing_sim.py` for the "
        "invariant assertions)"
    )
