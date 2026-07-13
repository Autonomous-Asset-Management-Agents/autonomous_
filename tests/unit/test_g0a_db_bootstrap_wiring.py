"""G0a (#1050): wire ensure_local_db_ready() into engine startup + absolute fallback dir.

Pre-existing main gaps (BORA audit AUDIT-011 / INV-24, INV-29):
  1. ensure_local_db_ready() exists (session.py, postgres-guarded, idempotent,
     docstring says "called by engine startup code") but has ZERO call sites —
     a fresh desktop install creates an EMPTY SQLite file and the first INSERT
     crashes with "no such table".
  2. CloudLogger.fallback_dir is CWD-relative ("cloud_fallback_logs") — under an
     Electron install (CWD = Program Files) this is Access Denied. Fix is gated
     on AAA_USER_DATA_DIR so cloud behavior stays byte-identical.

TDD contract (red first):
  - _init_engine_async() awaits ensure_local_db_ready() BEFORE any engine
    construction (so tables exist before the first DB write).
  - CloudLogger fallback dir: absolute under AAA_USER_DATA_DIR when set;
    EXACTLY the legacy relative path when not set (cloud unchanged).
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class StartupWiringOrder(unittest.TestCase):
    """ensure_local_db_ready must run first in the background init task."""

    def test_init_engine_async_awaits_db_ready_before_engine_init(self):
        calls: list[str] = []

        import core.engine.api_routes as api_routes

        async def fake_db_ready():
            calls.append("db_ready")

        async def fake_heavy_init():
            calls.append("engine_init")

        # Patch the db-ready seam + neutralize the heavy engine init the task runs.
        # No create=True: if the _init_engine_impl seam is renamed, this must fail loudly.
        with patch.object(
            api_routes, "ensure_local_db_ready", AsyncMock(side_effect=fake_db_ready)
        ), patch.object(
            api_routes, "_init_engine_impl", AsyncMock(side_effect=fake_heavy_init)
        ):
            asyncio.run(api_routes._init_engine_async())

        self.assertIn("db_ready", calls, "startup must await ensure_local_db_ready()")
        self.assertEqual(calls[0], "db_ready", "db bootstrap must precede engine init")


class FreshInstallFunctional(unittest.TestCase):
    """Fresh SQLite file → ensure_local_db_ready → tables exist AND first INSERT works.

    Uses patch.object on the module globals instead of importlib.reload (review W3):
    reload would leak a poisoned engine/AsyncSessionLocal pointing at a deleted
    tempdir into the shared test process; patch.object auto-restores.
    """

    def test_fresh_sqlite_gets_tables_and_first_insert_works(self):
        import sqlalchemy as sa
        from sqlalchemy.ext.asyncio import create_async_engine

        import core.database.session as session_mod
        from core.database.models import SystemConfig

        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "fresh.db"
            test_engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
            with patch.object(session_mod, "engine", test_engine), patch.object(
                session_mod, "_local_db_initialized", False
            ):

                async def _run():
                    await session_mod.ensure_local_db_ready()
                    async with test_engine.begin() as conn:
                        names = await conn.run_sync(
                            lambda sync_conn: sa.inspect(sync_conn).get_table_names()
                        )
                        # The audit's exact failure mode: the FIRST INSERT on a
                        # fresh install must not crash with "no such table".
                        from datetime import datetime, timezone

                        await conn.execute(
                            sa.insert(SystemConfig).values(
                                config_key="g0a_fresh_install_probe",
                                config_value={},
                                updated_at=datetime.now(timezone.utc),
                            )
                        )
                    # Windows: release the sqlite handle so tempdir cleanup works.
                    await test_engine.dispose()
                    return names

                tables = asyncio.run(_run())
            self.assertTrue(
                tables, "fresh sqlite must have tables after ensure_local_db_ready"
            )


class FallbackDirResolution(unittest.TestCase):
    """CloudLogger fallback dir: AAA_USER_DATA_DIR-aware, cloud-identical otherwise."""

    def test_absolute_under_user_data_dir_when_set(self):
        from core.cloud_logger import _resolve_fallback_dir

        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"AAA_USER_DATA_DIR": tmp}):
                d = _resolve_fallback_dir()
        self.assertTrue(os.path.isabs(d))
        self.assertTrue(d.startswith(tmp))
        self.assertTrue(d.endswith("cloud_fallback_logs"))

    def test_legacy_relative_when_unset(self):
        from core.cloud_logger import _resolve_fallback_dir

        env = dict(os.environ)
        env.pop("AAA_USER_DATA_DIR", None)
        with patch.dict(os.environ, env, clear=True):
            d = _resolve_fallback_dir()
        self.assertEqual(
            d, "cloud_fallback_logs", "cloud path must stay byte-identical"
        )


class SenateLogDirResolution(unittest.TestCase):
    """P0-1 (PR review): senate_log is a sibling CWD-relative writer — same contract
    as CloudLogger: SENATE_LOG_DIR explicit > AAA_USER_DATA_DIR > legacy relative."""

    def test_explicit_env_wins(self):
        from core.round_table.senate_log import _resolve_log_dir

        with TemporaryDirectory() as tmp:
            with patch.dict(
                os.environ, {"SENATE_LOG_DIR": tmp, "AAA_USER_DATA_DIR": "X"}
            ):
                self.assertEqual(_resolve_log_dir(), tmp)

    def test_user_data_dir_when_set(self):
        from core.round_table.senate_log import _resolve_log_dir

        with TemporaryDirectory() as tmp:
            env = {k: v for k, v in os.environ.items() if k != "SENATE_LOG_DIR"}
            env["AAA_USER_DATA_DIR"] = tmp
            with patch.dict(os.environ, env, clear=True):
                d = _resolve_log_dir()
        self.assertTrue(os.path.isabs(d) and d.startswith(tmp))

    def test_legacy_relative_when_nothing_set(self):
        from core.round_table.senate_log import _resolve_log_dir

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("SENATE_LOG_DIR", "AAA_USER_DATA_DIR")
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_resolve_log_dir(), "cloud_fallback_logs")


class CloudSqliteFallbackGuard(unittest.TestCase):
    """P0-2 (PR review): a Cloud Run instance (K_SERVICE set) without DB config
    must REFUSE the ephemeral-SQLite fallback (fail-closed) — silent data loss
    on container recycle is worse than a loud boot failure."""

    def test_raises_on_cloud_run(self):
        from core.database.session import _guard_cloud_sqlite_fallback

        with patch.dict(os.environ, {"K_SERVICE": "aaa-backend"}):
            with self.assertRaises(RuntimeError):
                _guard_cloud_sqlite_fallback()

    def test_passes_locally(self):
        from core.database.session import _guard_cloud_sqlite_fallback

        env = {k: v for k, v in os.environ.items() if k != "K_SERVICE"}
        with patch.dict(os.environ, env, clear=True):
            _guard_cloud_sqlite_fallback()  # must not raise


class ConcurrentInitSingleFlight(unittest.TestCase):
    """P1-1 (PR review): ensure_local_db_ready's check-then-act must be lock-guarded
    — two concurrent callers on one loop must trigger exactly ONE init."""

    def test_concurrent_calls_init_once(self):
        import core.database.session as session_mod

        init_calls = []

        async def fake_init(engine):
            init_calls.append(1)
            await asyncio.sleep(0.05)  # widen the race window

        async def _run():
            await asyncio.gather(
                session_mod.ensure_local_db_ready(),
                session_mod.ensure_local_db_ready(),
                session_mod.ensure_local_db_ready(),
            )

        with patch.object(session_mod, "_local_db_initialized", False), patch(
            "core.database.bootstrap.init_local_db",
            AsyncMock(side_effect=fake_init),
        ), patch.object(session_mod, "engine", session_mod.engine):
            # Only meaningful on sqlite engines (postgres path no-ops before init).
            if str(session_mod.engine.url).startswith("sqlite"):
                asyncio.run(_run())
                self.assertEqual(
                    sum(init_calls), 1, "init_local_db must run exactly once"
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
