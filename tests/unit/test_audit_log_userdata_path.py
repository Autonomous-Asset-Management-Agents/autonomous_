# tests/unit/test_audit_log_userdata_path.py
"""#2586: compliance_audit.log / kill_switch_audit.log died on every desktop
upgrade because both were opened RELATIVE to the process CWD — the app bundle
(<install>\\app\\ai_trading_bot), which the bootstrap replaces wholesale on
upgrade. audit_chain.jsonl (already under USER_DATA_DIR) survived correctly.

These tests lock the fix (core/audit_paths.py):
  (a) both audit sinks resolve into AAA_USER_DATA_DIR when set (desktop),
      and stay CWD-relative byte-identical when unset (cloud/dev — BORA);
  (b) a legacy file left in the app dir is migrated ONCE to the new location
      at module import (the bootstrap's atomic swap carries it into the new
      bundle; the engine then moves it to the durable per-user dir);
  (c) migration/resolution are best-effort and can NEVER raise into module
      import (a broken audit-path must not block engine boot).
"""

import importlib
import logging
import os
import sys

import pytest

from core import audit_paths


def _restore_audit_modules():
    """Re-establish the legacy CWD-relative audit wiring after a reload test.

    Extracted from ``clean_audit_modules`` so the singleton-identity invariant below can
    be asserted directly instead of relying on fixture teardown order.
    """
    import core.compliance
    import core.kill_switch

    for logger_name in ("ComplianceGuardian", "KillSwitchAudit"):
        lg = logging.getLogger(logger_name)
        for handler in list(lg.handlers):
            lg.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
    importlib.reload(core.compliance)
    importlib.reload(core.kill_switch)

    # Reloading a module that exports a SINGLETON splits object identity: every module
    # that did `from core.kill_switch import kill_switch` still holds the pre-reload
    # instance, so tripping the switch no longer stops them. Re-point them at the fresh
    # one. Identified by the type's defining module, which is precise (only instances of
    # THIS class are touched) and needs no list of importers to keep in sync.
    fresh_instance = core.kill_switch.kill_switch
    fresh_class = core.kill_switch.KillSwitch
    for module in list(sys.modules.values()):
        # Patch the instance
        stale_instance = getattr(module, "kill_switch", None)
        if (
            stale_instance is not None
            and stale_instance is not fresh_instance
            and type(stale_instance).__module__ == "core.kill_switch"
        ):
            try:
                module.kill_switch = fresh_instance
            except Exception:  # frozen/immutable module objects
                pass

        # Patch the class
        stale_class = getattr(module, "KillSwitch", None)
        if (
            stale_class is not None
            and stale_class is not fresh_class
            and getattr(stale_class, "__module__", None) == "core.kill_switch"
        ):
            try:
                module.KillSwitch = fresh_class
            except Exception:
                pass


@pytest.fixture
def clean_audit_modules():
    """Restore the module-level audit wiring after a reload test.

    Teardown runs AFTER monkeypatch's env/cwd undo (fixture finalization is
    LIFO and this fixture comes first in the test signature), so the closing
    reload re-establishes the legacy CWD-relative state other tests expect.
    """
    yield
    _restore_audit_modules()


def test_restoring_audit_modules_keeps_the_kill_switch_singleton_shared():
    """The teardown reload must not SPLIT the kill-switch singleton.

    ``core/engine/order_executor.py:23`` and ``core/cycle_watchdog.py:22`` bind the
    instance with ``from core.kill_switch import kill_switch`` at import time, so
    ``importlib.reload(core.kill_switch)`` leaves them pointing at the PRE-reload object.
    A later test then trips the module's switch while the executor still consults the old
    one — measured: ``bound is module attr`` was True before the reload and False after.

    Observed consequence:
    ``test_live_disable_halt.py::SubmitBoundaryHalt::test_fallback_sell_guard_trip_before_sell_blocks_all_submits``
    ("a trip before the fallback SELL must block the SELL") passed through with 2 submits
    where 0 are allowed, whenever a file that imports the executor ran before this one.

    Production never reloads modules, so this is test integrity rather than a live defect
    — but an unreliable SAFETY assertion is worse than a noisy failure, because it fails
    open: the suite would not catch a real kill-switch regression on that path.
    """
    import core.cycle_watchdog
    import core.engine.order_executor as oe
    import core.kill_switch

    # Precondition — without this the assertions below could pass vacuously.
    assert oe.kill_switch is core.kill_switch.kill_switch
    assert core.cycle_watchdog.kill_switch is core.kill_switch.kill_switch

    _restore_audit_modules()

    fresh = core.kill_switch.kill_switch
    assert oe.kill_switch is fresh, (
        "order_executor still holds the pre-reload kill switch — a trip would not stop "
        "its submits"
    )
    assert core.cycle_watchdog.kill_switch is fresh


# ── resolve_audit_log_path ──────────────────────────────────────────────────


def test_resolve_prefers_user_data_dir(monkeypatch, tmp_path):
    udd = tmp_path / "userdata"
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(udd))
    got = audit_paths.resolve_audit_log_path("compliance_audit.log")
    assert got == os.path.join(str(udd), "compliance_audit.log")
    # the dir must exist afterwards so logging.FileHandler can open the file
    assert os.path.isdir(str(udd))


def test_resolve_without_user_data_dir_stays_cwd_relative(monkeypatch):
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    got = audit_paths.resolve_audit_log_path("compliance_audit.log")
    assert got == "compliance_audit.log"


def test_resolve_ignores_whitespace_only_env(monkeypatch):
    monkeypatch.setenv("AAA_USER_DATA_DIR", "   ")
    got = audit_paths.resolve_audit_log_path("kill_switch_audit.log")
    assert got == "kill_switch_audit.log"


def test_resolve_falls_back_to_cwd_and_warns_when_dir_uncreatable(
    monkeypatch, caplog, tmp_path
):
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path / "blocked"))

    def _boom(*args, **kwargs):
        raise OSError("access denied")

    monkeypatch.setattr(audit_paths.os, "makedirs", _boom)
    with caplog.at_level(logging.WARNING):
        got = audit_paths.resolve_audit_log_path("compliance_audit.log")
    # fallback value + WARNING (never DEBUG, never an exception)
    assert got == "compliance_audit.log"
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


# ── migrate_legacy_audit_log ────────────────────────────────────────────────


def test_migrate_moves_legacy_when_target_absent(monkeypatch, tmp_path):
    appdir = tmp_path / "bundle"
    appdir.mkdir()
    monkeypatch.chdir(appdir)
    (appdir / "compliance_audit.log").write_text("old-entry\n", encoding="utf-8")
    target_dir = tmp_path / "userdata"
    target_dir.mkdir()
    target = target_dir / "compliance_audit.log"

    moved = audit_paths.migrate_legacy_audit_log("compliance_audit.log", str(target))

    assert moved is True
    assert target.read_text(encoding="utf-8") == "old-entry\n"
    assert not (appdir / "compliance_audit.log").exists()


def test_migrate_appends_when_target_exists(monkeypatch, tmp_path):
    appdir = tmp_path / "bundle"
    appdir.mkdir()
    monkeypatch.chdir(appdir)
    (appdir / "compliance_audit.log").write_text("legacy-entry\n", encoding="utf-8")
    target_dir = tmp_path / "userdata"
    target_dir.mkdir()
    target = target_dir / "compliance_audit.log"
    target.write_text("already-migrated-entry\n", encoding="utf-8")

    moved = audit_paths.migrate_legacy_audit_log("compliance_audit.log", str(target))

    assert moved is True
    content = target.read_text(encoding="utf-8")
    # nothing lost: both the existing and the legacy entries survive
    assert "already-migrated-entry" in content
    assert "legacy-entry" in content
    assert not (appdir / "compliance_audit.log").exists()


def test_migrate_noop_when_paths_equal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "compliance_audit.log").write_text("stay\n", encoding="utf-8")
    moved = audit_paths.migrate_legacy_audit_log(
        "compliance_audit.log", "compliance_audit.log"
    )
    assert moved is False
    assert (tmp_path / "compliance_audit.log").read_text(encoding="utf-8") == "stay\n"


def test_migrate_noop_when_legacy_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    moved = audit_paths.migrate_legacy_audit_log(
        "compliance_audit.log", str(tmp_path / "userdata" / "compliance_audit.log")
    )
    assert moved is False


def test_migrate_never_raises_and_warns_on_failure(monkeypatch, tmp_path, caplog):
    appdir = tmp_path / "bundle"
    appdir.mkdir()
    monkeypatch.chdir(appdir)
    (appdir / "compliance_audit.log").write_text("legacy\n", encoding="utf-8")
    target_dir = tmp_path / "userdata"
    target_dir.mkdir()
    target = target_dir / "compliance_audit.log"

    def _boom(*args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(audit_paths.os, "replace", _boom)
    monkeypatch.setattr(audit_paths.os, "remove", _boom)
    with caplog.at_level(logging.WARNING):
        moved = audit_paths.migrate_legacy_audit_log(
            "compliance_audit.log", str(target)
        )
    assert moved is False
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


# ── module wiring: the producers actually use the durable path ──────────────


def test_compliance_module_migrates_and_logs_to_userdata(
    clean_audit_modules, monkeypatch, tmp_path
):
    """Reloading core.compliance with AAA_USER_DATA_DIR set must (1) point the
    audit sink into the user-data dir, (2) migrate a bundle-relative leftover
    there, (3) attach a FileHandler on the durable path."""
    appdir = tmp_path / "bundle" / "ai_trading_bot"
    appdir.mkdir(parents=True)
    (appdir / "compliance_audit.log").write_text(
        "pre-upgrade-entry\n", encoding="utf-8"
    )
    udd = tmp_path / "userdata"
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(udd))
    monkeypatch.chdir(appdir)

    import core.compliance as compliance

    importlib.reload(compliance)

    target = udd / "compliance_audit.log"
    assert compliance._audit_log_path == str(target)
    assert "pre-upgrade-entry" in target.read_text(encoding="utf-8")
    assert not (appdir / "compliance_audit.log").exists()
    lg = logging.getLogger("ComplianceGuardian")
    assert any(
        isinstance(h, logging.FileHandler)
        and os.path.abspath(h.baseFilename) == str(target)
        for h in lg.handlers
    )


def test_kill_switch_module_migrates_and_logs_to_userdata(
    clean_audit_modules, monkeypatch, tmp_path
):
    """kill_switch_audit.log lives in the SAME cwd-relative pattern (its own
    module comment says it mirrors compliance.py) — treated identically."""
    appdir = tmp_path / "bundle" / "ai_trading_bot"
    appdir.mkdir(parents=True)
    (appdir / "kill_switch_audit.log").write_text("halt-entry\n", encoding="utf-8")
    udd = tmp_path / "userdata"
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(udd))
    monkeypatch.chdir(appdir)

    import core.kill_switch as kill_switch

    importlib.reload(kill_switch)

    target = udd / "kill_switch_audit.log"
    assert kill_switch._ks_audit_path == str(target)
    assert "halt-entry" in target.read_text(encoding="utf-8")
    assert not (appdir / "kill_switch_audit.log").exists()
    lg = logging.getLogger("KillSwitchAudit")
    assert any(
        isinstance(h, logging.FileHandler)
        and os.path.abspath(h.baseFilename) == str(target)
        for h in lg.handlers
    )


def test_cloud_dev_reload_stays_cwd_relative(
    clean_audit_modules, monkeypatch, tmp_path
):
    """Without AAA_USER_DATA_DIR (cloud/dev) the resolved path stays the bare
    legacy filename — byte-identical behavior (BORA)."""
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    import core.compliance as compliance

    importlib.reload(compliance)

    assert compliance._audit_log_path == "compliance_audit.log"
