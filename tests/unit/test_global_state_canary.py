# tests/unit/test_global_state_canary.py
"""The canary that guards test isolation needs its own guard.

Three independent leaks of the same class shipped undetected (keyring → os.environ,
2026-07; config reload override and kill-switch singleton split, both 2026-08-11), and in
every case the failure surfaced in an unrelated downstream test. The canary in
``tests/conftest.py`` exists to name the culprit instead. If it silently stopped detecting
anything, the suite would look healthier than it is — which is the failure mode these tests
prevent.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

from tests.conftest import (  # noqa: E402
    _CANARY_CONFIG_KEYS,
    _global_state_diff,
    _global_state_snapshot,
)


def test_snapshot_sees_the_knobs_it_claims_to_watch():
    """ANTI-VACUITY: a snapshot that silently captured nothing would make the canary
    unfailable — exactly the property that let the three leaks survive.

    Both modules are imported here first, so the assertion cannot pass merely because a
    module happened to be absent from ``sys.modules`` in an isolated run.
    """
    import config  # noqa: F401
    import core.kill_switch  # noqa: F401

    snap = _global_state_snapshot()
    assert "config.COMPLIANCE_MAX_ORDER_VALUE" in snap, (
        "config knobs are missing from the snapshot — the key list drifted from what "
        f"config actually defines: {_CANARY_CONFIG_KEYS}"
    )
    assert "os.environ" in snap
    assert "kill_switch.stale_importers" in snap
    # The singleton's raw id is deliberately NOT watched: a reload legitimately creates a new
    # instance (test_audit_log_userdata_path reloads on purpose), and watching the id flagged
    # every reload test. The defect is a SPLIT — importers holding different instances.
    assert "kill_switch.id" not in snap


def test_diff_ignores_the_harness_own_env_variable():
    """`PYTEST_CURRENT_TEST` is rewritten by pytest for every phase. Without this
    exemption the canary flags every single test — measured, before it was added."""
    before = {"os.environ": {"PYTEST_CURRENT_TEST": "a (setup)"}}
    after = {"os.environ": {"PYTEST_CURRENT_TEST": "a (teardown)"}}
    assert _global_state_diff(before, after) == []


def test_diff_ignores_native_library_thread_variables():
    """OpenMP/torch stamp these on first import, so they land on whichever test imports the
    library first — an ordering accident, not that test's defect. Ignored by prefix after
    three separate members turned up one at a time."""
    for key in (
        "KMP_DUPLICATE_LIB_OK",
        "KMP_INIT_AT_FORK",
        "OMP_NUM_THREADS",
        "TORCHINDUCTOR_CACHE_DIR",
    ):
        before = {"os.environ": {}}
        after = {"os.environ": {key: "whatever"}}
        assert _global_state_diff(before, after) == [], key
    # …but a variable that merely starts similarly is still reported.
    before = {"os.environ": {"KMPX": "a"}}
    after = {"os.environ": {"KMPX": "b"}}
    assert _global_state_diff(before, after) != []


def test_diff_ignores_a_module_imported_for_the_first_time():
    """A key appearing only in `after` means a module was imported during the test —
    ordinary, and not something the next test trips over."""
    before = {"os.environ": {}}
    after = {"os.environ": {}, "kill_switch.id": 123}
    assert _global_state_diff(before, after) == []


def test_diff_reports_a_config_leak_with_both_values():
    """The message has to be actionable: which knob, from what, to what."""
    before = {"config.COMPLIANCE_MAX_ORDER_VALUE": 10000.0, "os.environ": {}}
    after = {"config.COMPLIANCE_MAX_ORDER_VALUE": 1000.0, "os.environ": {}}
    diffs = _global_state_diff(before, after)
    assert len(diffs) == 1
    assert "COMPLIANCE_MAX_ORDER_VALUE" in diffs[0]
    assert "10000.0" in diffs[0] and "1000.0" in diffs[0]


def test_diff_reports_env_changes_per_key_not_as_one_blob():
    """A whole-dict diff would print ~60 variables and hide the one that changed."""
    before = {"os.environ": {"KEEP": "1", "GONE": "x"}}
    after = {"os.environ": {"KEEP": "1", "NEW": "y"}}
    diffs = _global_state_diff(before, after)
    assert len(diffs) == 2, diffs
    joined = " ".join(diffs)
    assert "'GONE'" in joined and "'NEW'" in joined
    assert "KEEP" not in joined, "unchanged variables must not be reported"


def test_diff_never_prints_a_secret_value():
    """MEASURED NEED, not caution. The first full run of this canary printed
    `ALPACA_LIVE_SECRET_KEY` and four more with their real values, because
    `config.oss.py:11-13` hydrates the OS keychain into `os.environ` at import. That a
    credential entered the environment is worth reporting; its value is not — a CI log or
    a diagnostics bundle would carry it out of the machine.
    """
    secret = "AKIAsupersecretvalue1234567890"
    before = {"os.environ": {}}
    after = {"os.environ": {"ALPACA_LIVE_SECRET_KEY": secret}}
    # The key must be reported (the leak is real) …
    before2 = {"os.environ": {"ALPACA_LIVE_SECRET_KEY": "old"}}
    diffs = _global_state_diff(before2, after)
    assert len(diffs) == 1
    assert "ALPACA_LIVE_SECRET_KEY" in diffs[0]
    # … but never its value.
    assert secret not in diffs[0], diffs[0]
    assert "old" not in diffs[0], diffs[0]
    assert "redacted" in diffs[0]


def test_redaction_also_covers_config_knobs_not_just_env():
    """`GEMINI_API_KEY` is a `config` attribute too, so a future entry in
    `_CANARY_CONFIG_KEYS` would print a secret if redaction were env-only."""
    before = {"config.GEMINI_API_KEY": "old-secret-value", "os.environ": {}}
    after = {"config.GEMINI_API_KEY": "new-secret-value", "os.environ": {}}
    diffs = _global_state_diff(before, after)
    assert len(diffs) == 1
    assert "GEMINI_API_KEY" in diffs[0]
    assert "secret-value" not in diffs[0], diffs[0]
    assert "redacted" in diffs[0]


def test_diff_still_prints_harmless_env_values():
    """Redaction must not blind the canary: a non-secret variable keeps its values, or the
    report stops being actionable."""
    before = {"os.environ": {"PAPER_TRADING": "True"}}
    after = {"os.environ": {"PAPER_TRADING": "False"}}
    diffs = _global_state_diff(before, after)
    assert "True" in diffs[0] and "False" in diffs[0]


def test_diff_detects_a_split_singleton():
    """Case 3: the module attribute is fine, but an importer holds the old instance.

    Identity alone is not enough — a reload can hand the module a fresh object while the
    executor keeps the stale one, which is precisely how a kill-switch trip stopped
    stopping anything.
    """
    before = {"kill_switch.id": 1, "kill_switch.stale_importers": ()}
    after = {
        "kill_switch.id": 1,
        "kill_switch.stale_importers": ("core.engine.order_executor",),
    }
    diffs = _global_state_diff(before, after)
    assert len(diffs) == 1
    assert "order_executor" in diffs[0]


def test_diff_is_silent_when_nothing_changed():
    snap = _global_state_snapshot()
    assert _global_state_diff(snap, dict(snap)) == []


def test_known_leak_entry_suppresses_only_the_listed_key():
    """The allowlist exists so NEW leaks fail while the measured backlog stays visible.

    It is keyed per (test, leaked key) on purpose: a blanket per-test exemption would let a
    listed test start leaking something else unnoticed — which is the failure mode the whole
    canary exists to close.
    """
    from tests import conftest as ct

    original = dict(ct._CANARY_KNOWN_LEAKS)
    try:
        ct._CANARY_KNOWN_LEAKS.clear()
        ct._CANARY_KNOWN_LEAKS["test_some_offender"] = {"config.USE_LIMIT_ORDERS"}

        node = "tests/unit/test_x.py::test_some_offender"
        assert ct._leak_is_known(node, "config.USE_LIMIT_ORDERS") is True
        # a DIFFERENT key from the same test is still a failure
        assert ct._leak_is_known(node, "config.MAX_POSITIONS") is False
        # the same key from a different test is still a failure
        assert (
            ct._leak_is_known(
                "tests/unit/test_y.py::test_other", "config.USE_LIMIT_ORDERS"
            )
            is False
        )
    finally:
        ct._CANARY_KNOWN_LEAKS.clear()
        ct._CANARY_KNOWN_LEAKS.update(original)


@pytest.mark.mutates_global_state
def test_marker_is_registered_and_exempts_a_deliberate_mutation():
    """`--strict-markers` is on, so an unregistered marker would be a collection error and
    this test would not run at all. It then leaks **on purpose** and does NOT clean up:
    reaching teardown without the canary failing is the only way to prove the exemption
    path works. Cleaning up would make the test vacuous — the canary would have had
    nothing to complain about either way.

    The leak is one harmless variable that nothing reads. It is deliberately NOT a `config`
    attribute: assigning one creates a real module-level attribute that permanently shadows
    the edition's PEP-562 `__getattr__` resolution, and `importlib.reload` does not clear
    the namespace — so an earlier version of this test pinned COMPLIANCE_MAX_ORDER_VALUE
    for the whole session and broke `test_config_flags`'s env overrides two files later.
    Exactly the class of defect this canary exists to catch, caused by its own test.
    """
    os.environ["CANARY_MARKER_PROBE"] = "1"
    assert os.environ["CANARY_MARKER_PROBE"] == "1"
