"""
Root conftest.py — systemische Guardrails für die Test-Suite.

ADR-CI-XDIST01: pytest-xdist und pytest-cov dürfen NICHT gleichzeitig
verwendet werden. xdist forkt Worker-Prozesse, die parallel in dieselbe
.coverage-Datei schreiben → Race Conditions → falsche Coverage-Werte.
Ref: https://pytest-cov.readthedocs.io/en/latest/subprocess-support.html
"""

import os

# Ensure baseline environment variables for test execution to prevent interference from local .env files
os.environ["REQUIRE_SIG"] = "true"
os.environ["USE_LIMIT_ORDERS"] = "false"

import pytest  # noqa: E402


def pytest_configure(config):
    """Fail fast if -n/--numprocesses and --cov are both active."""
    num_processes = config.getoption("numprocesses", default=None)
    cov_source = config.getoption("cov_source", default=None)

    # Check if --no-cov was explicitly passed to disable coverage
    try:
        no_cov = config.getoption("no_cov", default=False)
    except ValueError:
        no_cov = False

    if num_processes and cov_source and not no_cov:
        raise pytest.UsageError(
            "ADR-CI-XDIST01: Cannot combine pytest-xdist (-n) with "
            "pytest-cov (--cov). Use --no-cov when running parallel tests, "
            "or remove -n for coverage jobs."
        )
