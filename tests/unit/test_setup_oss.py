# tests/unit/test_setup_oss.py
# Adversarial-review fix coverage for the OSS setup helper at <repo-root>/setup.py.
#
# Focus: _rewrite_alpaca_line must tolerate any whitespace / tab variant
# between the optional leading '#' and the KEY= token. The earlier
# 3-element prefix tuple ("KEY=", "# KEY=", "#KEY=") silently dropped
# captured Alpaca keys when .env.oss.example used two spaces or a tab,
# while the success message at the end of setup.py kept claiming the
# keys had been written.

import importlib.util
import sys
from pathlib import Path

import allure
import pytest


def _load_setup_module():
    """Load <repo-root>/setup.py as a fresh module under a unique name.

    setup.py lives at the repository root, not under "ai_trading_bot/", so
    we resolve relative to this test file: tests/unit -> tests -> AI Trading
    Bot -> repo root.
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[3]
    setup_path = repo_root / "setup.py"
    assert setup_path.exists(), f"setup.py not found at {setup_path}"

    spec = importlib.util.spec_from_file_location(
        "_oss_setup_under_test", str(setup_path)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def setup_module():
    return _load_setup_module()


@pytest.mark.parametrize(
    "input_line,key,value,expected",
    [
        # 1. Bare uncommented form
        ("ALPACA_API_KEY=\n", "ALPACA_API_KEY", "PKKEY", "ALPACA_API_KEY=PKKEY\n"),
        # 2. Single-space comment form (most common in env templates)
        ("# ALPACA_API_KEY=\n", "ALPACA_API_KEY", "PKKEY", "ALPACA_API_KEY=PKKEY\n"),
        # 3. Two-space comment form — REGRESSION case for the prefix-tuple bug
        (
            "#  ALPACA_SECRET_KEY=\n",
            "ALPACA_SECRET_KEY",
            "PKSEC",
            "ALPACA_SECRET_KEY=PKSEC\n",
        ),
        # 4. Tab between '#' and key — REGRESSION case for the prefix-tuple bug
        (
            "#\tALPACA_SECRET_KEY=\n",
            "ALPACA_SECRET_KEY",
            "PKSEC",
            "ALPACA_SECRET_KEY=PKSEC\n",
        ),
    ],
)
@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_rewrite_alpaca_line_tolerates_whitespace_variants(
    setup_module, input_line, key, value, expected
):
    """Each comment-prefix variant must rewrite to the uncommented KEY=value form."""
    assert setup_module._rewrite_alpaca_line(input_line, key, value) == expected


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_rewrite_alpaca_line_leaves_unrelated_lines_alone(setup_module):
    """A non-Alpaca line must pass through unchanged."""
    line = "POSTGRES_PASSWORD=keep-me\n"
    assert setup_module._rewrite_alpaca_line(line, "ALPACA_API_KEY", "PKKEY") is line


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_rewrite_alpaca_line_does_not_cross_match_keys(setup_module):
    """An ALPACA_SECRET_KEY line must not be rewritten when key=ALPACA_API_KEY."""
    line = "# ALPACA_SECRET_KEY=\n"
    assert setup_module._rewrite_alpaca_line(line, "ALPACA_API_KEY", "PKKEY") is line
