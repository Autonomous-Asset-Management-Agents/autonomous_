"""O3 — cash-aware sizing divisor: `effective_free_slots`.

The cash constraint in `calculate_position_size` splits free cash by a slot count. Today that count
is the FIXED book cap (`effective_max_positions()` = 10), so a mostly-invested book with little free
cash sizes every new buy at `cash/10` = dust. `effective_free_slots(open_positions)` returns the
REMAINING free slots (`max_positions - held`) so freed cash funds proper-sized positions. A FULL or
over-cap book (0 free slots) falls back to the conservative fixed book-cap divisor — NEVER divisor 1
(all-in) — per the adversarial-review safety finding. Flag-gated (`CASH_AWARE_SLOTS_ENABLED`): OFF
restores the fixed book-cap divisor byte-identically.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.risk_manager import effective_free_slots, effective_max_positions

pytestmark = pytest.mark.iron_dome


def _cfg(enabled, max_pos=10, full_universe=False, full_max=10):
    return SimpleNamespace(
        CASH_AWARE_SLOTS_ENABLED=enabled,
        MAX_POSITIONS=max_pos,
        FULL_UNIVERSE_TRADING_ENABLED=full_universe,
        FULL_UNIVERSE_MAX_POSITIONS=full_max,
    )


class TestEffectiveFreeSlots:
    def test_flag_off_is_fixed_book_cap(self):
        """OFF → byte-identical to today: always the fixed cap, ignoring held count."""
        with patch("config.get_config", return_value=_cfg(False)):
            assert effective_free_slots(0) == effective_max_positions()
            assert effective_free_slots(7) == effective_max_positions()
            assert effective_free_slots(10) == effective_max_positions()

    def test_flag_on_returns_remaining_slots(self):
        """ON → cap − held (the divisor now reflects only the slots still to fill)."""
        with patch("config.get_config", return_value=_cfg(True, max_pos=10)):
            assert effective_free_slots(0) == 10  # empty book → same as cap
            assert effective_free_slots(3) == 7
            assert (
                effective_free_slots(8) == 2
            )  # near-full → big remaining budget per name

    def test_genuine_last_free_slot_divides_by_one(self):
        """ON → exactly one free slot (held == cap-1) → divisor 1: the whole cash budget funds the
        last position, by design (bounded by the unchanged MAX rail)."""
        with patch("config.get_config", return_value=_cfg(True, max_pos=10)):
            assert effective_free_slots(9) == 1

    def test_full_or_overcap_book_falls_back_to_cap_not_one(self):
        """O3-SAFETY: a FULL (held == cap) or over-cap / stale-over-count (held > cap) book has ZERO
        free slots → the conservative fixed book-cap divisor, NEVER 1 (which would route ~all free cash
        into a single order = max concentration, the opposite of the risk-averse intent).
        """
        with patch("config.get_config", return_value=_cfg(True, max_pos=10)):
            assert (
                effective_free_slots(10) == 10
            )  # full → cash/cap (small), not cash/1 (all-in)
            assert (
                effective_free_slots(12) == 10
            )  # over-cap / stale over-count → still cap
            assert (
                effective_free_slots(50) == 10
            )  # scan-universe-sized over-count → still cap

    def test_flag_on_full_universe_uses_full_cap(self):
        """ON + full-universe → remaining is measured against FULL_UNIVERSE_MAX_POSITIONS."""
        with patch(
            "config.get_config",
            return_value=_cfg(True, full_universe=True, full_max=20),
        ):
            assert effective_free_slots(5) == 15

    def test_none_or_garbage_open_count_is_safe(self):
        """A None/negative held count degrades to the cap (never raises, never < 1)."""
        with patch("config.get_config", return_value=_cfg(True, max_pos=10)):
            assert effective_free_slots(None) == 10
            assert effective_free_slots(-3) == 10

    def test_unconfirmed_positions_falls_back_to_cap(self):
        """O3-SAFETY: when the held count is NOT confirmed-fresh (positions_confirmed=False, i.e. the
        last refresh_positions() swallowed a broker-read error and the map may be a stale over-count),
        the divisor is the conservative fixed cap regardless of the passed held count — never a
        collapsed remaining-slots divisor that would oversize the buy."""
        with patch("config.get_config", return_value=_cfg(True, max_pos=10)):
            # held=8 would normally give remaining=2 (a big per-name budget); unconfirmed → cap instead.
            assert effective_free_slots(8, positions_confirmed=False) == 10
            assert effective_free_slots(9, positions_confirmed=False) == 10
            # confirmed (default) keeps the remaining-slots behaviour.
            assert effective_free_slots(8, positions_confirmed=True) == 2
