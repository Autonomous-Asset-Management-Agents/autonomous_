"""
Pytest/unittest conftest – runs before any test module is imported.

If neither pandas_ta nor pandas_ta_classic is installed (e.g. in a bare
CI environment where the vendored wheel failed to build), inject a minimal
stub so that test_health.py can import core.engine without crashing.
The stub exposes only the surface that our code uses at module-load time
(the 'strategy' accessor) – actual TA calculations are not exercised by
test_health at all.
"""

import os
import sys

# Force AUTO_START_STRATEGY and HEARTBEAT to False in tests to prevent background threads
os.environ["AUTO_START_STRATEGY"] = "False"
os.environ["ENABLE_HEARTBEAT"] = "False"

# --- FIX: Import torch before any other modules to prevent DLL conflict (WinError 1114) ---
try:
    import torch
except ImportError:
    pass
# --- END FIX ---

from unittest.mock import MagicMock, patch

import pytest

# Force all tests to use the mock_models directory to prevent network downloads and Eviction Loops
try:
    import config

    mock_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "fixtures", "mock_models")
    )
    config.DATA_DIR = mock_dir
    # Keep USER_DATA_DIR (per-user account-state) consistent with the mocked DATA_DIR
    # so account-state writes in tests stay in the fixture dir, not the real data/.
    # Prod defaults equal (AAA_USER_DATA_DIR unset → USER_DATA_DIR == DATA_DIR).
    config.USER_DATA_DIR = mock_dir
except ImportError:
    pass


# Disable GCP Secret Manager for all tests to prevent hanging on auth refresh
try:
    import secrets_loader

    secrets_loader.load_secrets = lambda *args, **kwargs: None
except ImportError:
    pass


# ---------------------------------------------------------------------------
# OTEL Cloud Trace guard
# ---------------------------------------------------------------------------
# The self-hosted GKE runner inherits K_SERVICE from the GKE node, which
# causes core/telemetry.py to register CloudTraceSpanExporter + BatchSpan-
# Processor. At pytest teardown the background thread attempts batch_write_
# spans() → GCP returns 403 (runner SA lacks roles/cloudtrace.agent) → the
# logging system tries to log the error on a closed stderr stream → process
# exits with code 1, even though all unit tests passed.
#
# We patch CloudTraceSpanExporter at import time (before any test module
# loads) to a no-op MagicMock. This is strictly scoped to the test process
# and has no effect on production or staging Cloud Run deployments.
try:
    import opentelemetry.exporter.cloud_trace as _ct_mod  # noqa: F401

    _ct_mod.CloudTraceSpanExporter = MagicMock  # type: ignore[attr-defined]
except ImportError:
    pass  # package not installed — nothing to patch


@pytest.fixture
def anyio_backend():
    """Force anyio to use asyncio backend only."""
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
def configure_torch_single_thread():
    """Limit torch to a single thread to prevent SIGSEGV on process exit.

    torch creates OpenMP/MKL thread pools on import. When the asyncio event
    loop is torn down after tests, torch C++ threads may still be running,
    causing a SIGSEGV (exit 139). Limiting to 1 thread prevents pool creation.
    Defence-in-depth alongside OMP_NUM_THREADS=1 in ci.yml (which acts before
    torch is imported) and MKL_NUM_THREADS=1.

    Note: set_num_interop_threads raises RuntimeError if the interop pool is
    already initialised (happens when torch is imported during test collection).
    Wrap separately so the main set_num_threads(1) still applies.
    """
    try:
        import torch

        torch.set_num_threads(1)
    except Exception:
        pass
    try:
        import torch

        torch.set_num_interop_threads(1)
    except Exception:
        pass  # RuntimeError if pool already initialised — acceptable


@pytest.fixture(autouse=True)
def reset_kill_switch():
    """Reset the KillSwitch singleton after every test.

    KillSwitch is a module-level singleton with mutable global state
    (_halted, _user_halts). Tests in test_kill_switch.py that call
    kill_switch.trip() set this state permanently for subsequent tests.
    Without this fixture, any test that runs after a trip() call (e.g.
    test_otel_bce, test_strftime_safeguards) sees is_halted()=True and
    takes the early-return path, never reaching the code under test.
    """
    yield
    try:
        from core.kill_switch import kill_switch

        # Reset the local state and any lingering Redis state
        kill_switch.reset()
        if kill_switch.redis_client:
            kill_switch.redis_client.flushall()
    except Exception:
        pass  # Fail silently – if the module cannot be imported, no state to clean


@pytest.fixture(autouse=True)
def mock_redis_global():
    """Globally mock RedisClient to avoid timeouts during tests.

    Uses fakeredis for standard state tests. Complex tests (e.g. Streams)
    should use explicit AsyncMocks or skip this fixture by setting USE_REAL_REDIS=1.
    """
    if os.environ.get("USE_REAL_REDIS") == "1":
        yield
        return

    import fakeredis

    fake_sync = fakeredis.FakeRedis(decode_responses=True)
    fake_async = fakeredis.FakeAsyncRedis(decode_responses=True)

    with patch(
        "core.redis_client.RedisClient.get_redis", return_value=fake_async
    ), patch("core.redis_client.RedisClient.get_sync_redis", return_value=fake_sync):
        yield


def _force_mock_torch() -> None:
    """Force-replace torch with MagicMock in sys.modules (for CI native runners with broken torch)."""
    torch_mods = [
        "torch",
        "torch.nn",
        "torch.nn.functional",
        "torch.nn.modules",
        "torch.optim",
        "torch.optim.lr_scheduler",
        "torch.utils",
        "torch.utils.data",
        "torch.cuda",
        "torch.distributed",
    ]
    # Remove any existing (broken) torch modules
    for m in list(sys.modules.keys()):
        if m == "torch" or m.startswith("torch."):
            del sys.modules[m]
    # Replace with MagicMock stubs
    for m in torch_mods:
        sys.modules[m] = MagicMock()
    # Wire up parent-child relationships
    for m in torch_mods:
        if "." in m:
            parent, child = m.rsplit(".", 1)
            if parent in sys.modules:
                setattr(sys.modules[parent], child, sys.modules[m])


def _ensure_stubs() -> None:
    """Insert minimal stubs for packages that might fail to import locally (e.g. DLL errors)."""
    import os

    # Core packages to mock if missing
    mods_to_mock = [
        "pandas_ta",
        "pandas_ta_classic",
        "gymnasium",
        "gymnasium.spaces",
    ]

    for mod_name in mods_to_mock:
        # Check if we should force a mock or if it's already missing/broken
        is_broken = False
        if mod_name in sys.modules:
            # If it's already in sys.modules, check if it's a real module we want to replace
            # or if it's already a mock.
            pass

        try:
            __import__(mod_name)
        except Exception:
            is_broken = True

        if is_broken:
            # Clean up partial imports
            for m in list(sys.modules.keys()):
                if m == mod_name or m.startswith(mod_name + "."):
                    del sys.modules[m]

                # Create stub
                stub = MagicMock()
                # Special cases for callable submodules or expected patterns
                if "pandas_ta" in mod_name:
                    stub.strategy = lambda *a, **kw: None

                # Inject into sys.modules
                sys.modules[mod_name] = stub

    # Ensure parent modules have references to their children if they were mocked
    for mod_name in mods_to_mock:
        if "." in mod_name:
            parent, child = mod_name.rsplit(".", 1)
            if parent in sys.modules and isinstance(sys.modules[parent], MagicMock):
                setattr(sys.modules[parent], child, sys.modules[mod_name])


_ensure_stubs()


@pytest.fixture(scope="session")
def seed_test_db():
    """
    Seeds the test database with Edge Cases and Personas using factory_boy.
    Must only be run in local/staging environments (handled internally by database_seeder).
    """
    import asyncio

    from tests.seeder.database_seeder import seed_database

    asyncio.run(seed_database())
