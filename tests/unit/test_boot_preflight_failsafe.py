# tests/unit/test_boot_preflight_failsafe.py
# HOTFIX (live-boot-failsafe): the boot pre-flight must NEVER become an exit-1 restart loop that
# bricks the desktop. _preflight_action() is the pure decision — desktop degrades to PAPER, cloud
# stops cleanly (exit 0), and no input combination yields an exit-1 (loop) action.

from core.engine.__main__ import _preflight_action


def test_preflight_ok_continues_both_editions():
    assert _preflight_action(True, is_desktop=True) == "continue"
    assert _preflight_action(True, is_desktop=False) == "continue"


def test_preflight_failed_desktop_degrades_to_paper():
    # Desktop must NEVER exit-1-loop — it degrades to PAPER and keeps booting (managed).
    assert _preflight_action(False, is_desktop=True) == "degrade_paper"


def test_preflight_failed_cloud_stops_clean_no_loop():
    # Cloud stops cleanly (exit 0) — Cloud Run marks unhealthy, no restart loop.
    assert _preflight_action(False, is_desktop=False) == "exit0"


def test_preflight_never_yields_an_exit1_loop_action():
    for ok in (True, False):
        for desktop in (True, False):
            assert _preflight_action(ok, desktop) in (
                "continue",
                "degrade_paper",
                "exit0",
            )
