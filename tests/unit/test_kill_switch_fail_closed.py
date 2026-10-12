"""#2467 FIX 4 — a genuine safety trip is fail-closed: it drops the active account to PAPER and
records a WORM ``disable`` (global trips only), so a restart boots fail-closed and the trip is
auditable. ``/api/live/disable`` opts out (``fail_closed=False``) because it does both itself."""

from unittest.mock import patch

import pytest

from core.kill_switch import KillSwitch


@pytest.fixture(autouse=True)
def _fresh_ks():
    ks = KillSwitch()
    ks.reset()
    ks._last_trip = None
    yield
    ks.reset()
    ks._last_trip = None


def _ks():
    ks = KillSwitch()
    ks.reset()
    ks._last_trip = None
    return ks


class TestKillSwitchFailClosed:
    def test_default_trip_applies_fail_closed(self):
        ks = _ks()
        with patch.object(ks, "_apply_fail_closed") as fc, patch("threading.Thread"):
            ks.trip(reason="latency_watchdog")
        fc.assert_called_once()

    def test_disable_path_opts_out_of_fail_closed(self):
        # /api/live/disable passes fail_closed=False (it force-papers + WORM-writes itself).
        ks = _ks()
        with patch.object(ks, "_apply_fail_closed") as fc, patch("threading.Thread"):
            ks.trip(reason="operator switched to paper", fail_closed=False)
        fc.assert_not_called()

    def test_user_scoped_trip_never_flips_the_global_account(self):
        ks = _ks()
        with patch.object(ks, "_apply_fail_closed") as fc, patch("threading.Thread"):
            ks.trip(reason="per-user", user_id="oauth-user-1")
        fc.assert_not_called()

    def test_apply_fail_closed_forces_paper_and_starts_worm_write(self):
        ks = _ks()
        with patch("config.force_paper_trading") as force_paper, patch(
            "threading.Thread"
        ) as thread:
            ks._apply_fail_closed("cycle_watchdog")
        force_paper.assert_called_once()
        # the WORM disable is written on its own daemon thread (best-effort)
        assert thread.called

    def test_force_paper_failure_never_raises(self):
        ks = _ks()
        with patch(
            "config.force_paper_trading", side_effect=RuntimeError("boom")
        ), patch("threading.Thread"):
            ks._apply_fail_closed("watchdog")  # must not raise
