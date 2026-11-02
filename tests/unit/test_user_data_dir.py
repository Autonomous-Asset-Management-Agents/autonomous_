"""USER_DATA_DIR seam (fusion S0-E3) — per-user mutable account-state out of the bundle.

The desktop bundle would otherwise ship the dev box's account state (app.db,
checkpoints.db) so a fresh install inherits it. The seam:
``config.USER_DATA_DIR = AAA_USER_DATA_DIR env or DATA_DIR`` — account-state files
resolve there; read-only models/static stay in DATA_DIR. Defaults to DATA_DIR when
the env is unset → dev + Cloud Run byte-identical (BORA).

Both cases run in a FRESH subprocess: ``conftest.py`` mocks both ``DATA_DIR`` and
``USER_DATA_DIR`` to the same fixture dir for isolation, which would make an
in-process default assertion vacuous and a per-user override impossible to observe.
"""

import os
import subprocess
import sys


def _engine_root() -> str:
    # tests/unit/<this_file> -> ai_trading_bot/
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(code: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        cwd=_engine_root(),
    )


def test_user_data_dir_defaults_to_data_dir():
    """AAA_USER_DATA_DIR unset → USER_DATA_DIR == DATA_DIR (BORA byte-identity)."""
    env = {k: v for k, v in os.environ.items()}
    env.pop("AAA_USER_DATA_DIR", None)
    code = r"""
import config
assert config.USER_DATA_DIR == config.DATA_DIR, (
    "USER_DATA_DIR", config.USER_DATA_DIR, "DATA_DIR", config.DATA_DIR)
print("OK")
"""
    r = _run(code, env)
    assert r.returncode == 0 and "OK" in r.stdout, (r.stdout, r.stderr[-1500:])


def test_account_state_redirects_under_override(tmp_path):
    """With AAA_USER_DATA_DIR set: app.db (DATABASE_URL default) AND the LangGraph
    checkpoints.db move under the per-user dir; models/static (DATA_DIR) stay put."""
    ud = str(tmp_path / "ud")
    env = {k: v for k, v in os.environ.items()}
    env["AAA_USER_DATA_DIR"] = ud
    code = r"""
import os
import config

u = os.environ["AAA_USER_DATA_DIR"].replace("\\", "/")
assert config.USER_DATA_DIR.replace("\\", "/") == u, ("USER_DATA_DIR", config.USER_DATA_DIR)
assert config.DATA_DIR.replace("\\", "/") != u, "models/static must stay in DATA_DIR"

dburl = (config.DATABASE_URL or "").replace("\\", "/")
assert u in dburl and dburl.endswith("aaagents.db"), ("app.db not redirected", dburl)

# graph.py LangGraph SQLite checkpointer path (via the testable helper)
from core.orchestration.graph import _checkpoint_db_path
cp = _checkpoint_db_path().replace("\\", "/")
assert u in cp and cp.endswith("checkpoints.db"), ("checkpoints.db not redirected", cp)

print("OK")
"""
    r = _run(code, env)
    assert r.returncode == 0 and "OK" in r.stdout, (r.stdout, r.stderr[-1500:])
