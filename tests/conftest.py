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


# ---------------------------------------------------------------------------
# Global-state canary — attribute a leak to its CULPRIT, not to its victim
# ---------------------------------------------------------------------------
# Three independent incidents of the same class, none caught by a test of its own:
#   1. 2026-07: the OS keyring polluted os.environ with real credentials, so
#      test_live_without_live_keys_fails_closed failed on developer machines
#      (PR_test_isolation_and_fallback.md).
#   2. 2026-08-11: test_config_flags._reload_config_with_env restored the env but not the
#      reloaded `config`, leaving COMPLIANCE_MAX_ORDER_VALUE=1000 (vs 10,000) in the live
#      module. It rode into risk_manager's ESMA cap, 1000*0.9999/price — the factor-10
#      discrepancy in the sizing tests.
#   3. 2026-08-11: reloading core.kill_switch created a NEW singleton while
#      order_executor.py and cycle_watchdog.py kept the pre-reload instance, so
#      "a trip must block all submits" passed through with 2 submits where 0 are allowed.
#
# In every case the SYMPTOM appeared in an unrelated, downstream test — which is why all
# three were long-lived and looked like flakes. Note that `reset_kill_switch` below cannot
# catch case 3 on its own: it re-imports the singleton inside the fixture, so after a
# reload it dutifully resets the FRESH object while the victim still consults the stale
# one. Cleanup fixtures maintain state; they cannot verify it. Hence this canary.
#
# A test that means to mutate a global marks itself `@pytest.mark.mutates_global_state`.
_CANARY_CONFIG_KEYS = (
    "COMPLIANCE_MAX_ORDER_VALUE",
    "COMPLIANCE_MAX_DAILY_TRADES",
    "ENABLE_COMPLIANCE_GUARDIAN",
    "MIN_ORDER_VALUE_USD",
    "MIN_POSITION_PERCENT",
    "MAX_POSITIONS",
    "USE_LIMIT_ORDERS",
    "LIMIT_ORDER_SPREAD_BUFFER_PCT",
)


def _global_state_snapshot() -> dict:
    """A cheap, side-effect-free picture of the globals this suite is known to leak.

    Modules are read out of ``sys.modules`` and never imported: an import here would be a
    side effect of the canary itself, and would make the canary the first thing to touch
    `config`. The singleton scan is limited to our own packages — nothing else binds it.
    """
    snap: dict = {}

    cfg = sys.modules.get("config")
    if cfg is not None:
        for key in _CANARY_CONFIG_KEYS:
            try:
                snap[f"config.{key}"] = getattr(cfg, key)
            except Exception:
                pass  # a knob this edition does not define is not a leak

    ks_mod = sys.modules.get("core.kill_switch")
    if ks_mod is not None:
        current = getattr(ks_mod, "kill_switch", None)
        # NOT the singleton's id: a reload legitimately creates a new instance, and
        # test_audit_log_userdata_path reloads on purpose. Measured — the raw id produced a
        # false positive on every reload test. What matters is whether the importers still
        # SHARE the current instance; a split is the defect (#2791), a new object is not.
        stale = []
        for name, mod in list(sys.modules.items()):
            if not (name.startswith("core") or name.startswith("ai_trading_bot")):
                continue
            other = getattr(mod, "kill_switch", None)
            if (
                other is not None
                and other is not current
                and type(other).__module__ == "core.kill_switch"
            ):
                stale.append(name)
        snap["kill_switch.stale_importers"] = tuple(sorted(stale))

    snap["os.environ"] = dict(os.environ)
    return snap


# Not leaks. `PYTEST_CURRENT_TEST` is rewritten by pytest for every phase (setup/call/
# teardown) — without this the canary flagged every single test.
_CANARY_ENV_IGNORE = frozenset({"PYTEST_CURRENT_TEST"})

# Thread/cache variables stamped by native libraries (OpenMP, torch) on FIRST import. They
# land on whichever test happens to import the library first, which is an accident of
# ordering rather than a defect in that test. Measured one at a time — KMP_DUPLICATE_LIB_OK,
# TORCHINDUCTOR_CACHE_DIR, then KMP_INIT_AT_FORK — so the family is ignored by prefix instead
# of chasing each new member.
_CANARY_ENV_IGNORE_PREFIXES = ("KMP_", "OMP_", "TORCH_", "TORCHINDUCTOR_")


def _env_ignored(env_key: str) -> bool:
    return env_key in _CANARY_ENV_IGNORE or env_key.startswith(
        _CANARY_ENV_IGNORE_PREFIXES
    )


# Env names whose VALUE must never be printed. Measured need, not caution: the first full
# run of this canary reported `ALPACA_LIVE_SECRET_KEY` and friends with their real values,
# because `config.oss.py:11-13` hydrates the OS keychain into os.environ at import. The leak
# is worth reporting; the secret is not. Report that the key changed, never to what.
_CANARY_SECRET_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")


def _redact(name: str, value):
    """Mask a value whose NAME looks like a credential — env var or config knob alike.

    Applied to every reported key, not only ``os.environ``: `GEMINI_API_KEY` is also a
    `config` attribute, so a future entry in ``_CANARY_CONFIG_KEYS`` would otherwise print a
    secret. Erring toward redaction is the correct direction for a guard whose output lands
    in logs; a knob merely *containing* one of these words loses its value in the report and
    nothing else.
    """
    if value == "<absent>":
        return value
    if any(marker in name.upper() for marker in _CANARY_SECRET_MARKERS):
        return "<redacted>"
    return value


def _global_state_diff(before: dict, after: dict) -> list:
    """Human-readable differences; os.environ is reported per changed KEY, not whole.

    A key that appears only in ``after`` is NOT reported: that is a module being imported
    for the first time during the test, which is ordinary. The reverse — a key that
    disappears — is reported, because a module vanishing from ``sys.modules`` mid-session
    is exactly the kind of thing the next test would trip over.
    """
    diffs = []
    for key in sorted(set(before) | set(after)):
        if key not in before:
            continue
        b, a = before.get(key, "<absent>"), after.get(key, "<absent>")
        if b == a:
            continue
        if key == "os.environ":
            b_env, a_env = before.get(key) or {}, after.get(key) or {}
            for env_key in sorted(set(b_env) | set(a_env)):
                if _env_ignored(env_key):
                    continue
                if b_env.get(env_key) != a_env.get(env_key):
                    diffs.append(
                        f"os.environ[{env_key!r}]: "
                        f"{_redact(env_key, b_env.get(env_key, '<absent>'))!r} -> "
                        f"{_redact(env_key, a_env.get(env_key, '<absent>'))!r}"
                    )
        else:
            diffs.append(f"{key}: {_redact(key, b)!r} -> {_redact(key, a)!r}")
    return diffs


# Pre-existing leaks, measured on the full suite. They are listed so that NEW leaks fail
# immediately instead of hiding behind a red suite — the backlog is visible here rather than
# absent. Deliberately keyed per (test, leaked key): if a listed test starts leaking
# something ELSE, it still fails. An entry is a debt, not a permission; remove it with the
# fix. Tracked in #2793, and the credential entry in #2796.
_CANARY_KNOWN_LEAKS: dict = {
    # Measured on the full suite (5,229 tests): after #2791 exactly these remain, and both
    # are the SAME root cause — importing the configuration hydrates the environment (the OS
    # keychain at config.oss.py:11-13, and the .env files via load_dotenv). The test named
    # here is whichever one imported the config first, i.e. the messenger, not the culprit.
    # Root cause and fix: #2796. Remove these entries with that fix.
    # We use an empty string "" to match ANY test, because under xdist the first test
    # to import config is non-deterministic, so pinning a specific test name fails in CI.
    "": {
        "os.environ['ALPACA_API_KEY']",
        "os.environ['ALPACA_SECRET_KEY']",
        "os.environ['ALPACA_LIVE_API_KEY']",
        "os.environ['ALPACA_LIVE_SECRET_KEY']",
        "os.environ['GEMINI_API_KEY']",
        "os.environ['REDIS_URL']",
        "os.environ['ALPACA_BASE_URL']",
        "os.environ['PAPER_TRADING']",
    },
}


def _leak_is_known(node_id: str, leak_key: str) -> bool:
    return any(
        (fragment == "" or fragment in node_id) and leak_key in keys
        for fragment, keys in _CANARY_KNOWN_LEAKS.items()
    )


@pytest.fixture(autouse=True)
def global_state_canary(request):
    """Fail the test that LEAVES global state changed, so the leak is attributable.

    Declared before the other autouse fixtures on purpose: same-scope autouse fixtures
    finalise in reverse definition order, so this one tears down LAST and therefore judges
    the world *after* every cleanup fixture has run. What it reports is what the next test
    would inherit.
    """
    before = _global_state_snapshot()
    yield
    if request.node.get_closest_marker("mutates_global_state"):
        return
    diffs = [
        entry
        for entry in _global_state_diff(before, _global_state_snapshot())
        if not _leak_is_known(request.node.nodeid, entry.split(": ", 1)[0])
    ]
    assert not diffs, (
        "this test leaked global state into the rest of the session:\n  "
        + "\n  ".join(diffs)
        + "\n\nRestore it (fixture/monkeypatch), or mark the test "
        "`@pytest.mark.mutates_global_state` if the mutation is the point."
    )


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
        "gymnasium",
        "gymnasium.spaces",
    ]

    # pandas_ta family: stub ONLY when NEITHER variant imports — the
    # documented contract of this conftest ("If neither pandas_ta nor
    # pandas_ta_classic is installed"). Stubbing pandas_ta while
    # pandas_ta_classic IS installed would shadow the real fallback chain in
    # core/utils.py:4-8 with a MagicMock and turn every TA indicator into
    # object-dtype garbage — the #2409 LSTM offline/live parity tests need
    # the real feature math.
    _ta_family = ["pandas_ta", "pandas_ta_classic"]

    def _importable(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    if not any(_importable(m) for m in _ta_family):
        mods_to_mock = _ta_family + mods_to_mock

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
