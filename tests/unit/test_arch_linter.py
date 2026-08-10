import os
import subprocess
import sys
import tempfile
import textwrap

import allure


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_arch_linter_valid_enterprise():
    """Test that a pure enterprise pipeline with GCP auth passes."""
    content = textwrap.dedent(
        """
    name: Enterprise Deploy
    jobs:
      deploy:
        runs-on: ubuntu-latest
        steps:
          - uses: google-github-actions/auth@v3
            with:
              workload_identity_provider: staging-wif
    """
    )
    _run_linter_test(content, "deploy.yml", expected_exit_code=0)


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_arch_linter_invalid_oss_job_name():
    """Test that GCP auth inside an OSS-named job fails."""
    content = textwrap.dedent(
        """
    name: Build
    jobs:
      build_oss:
        runs-on: ubuntu-latest
        steps:
          - uses: google-github-actions/auth@v3
            with:
              workload_identity_provider: staging-wif
    """
    )
    _run_linter_test(content, "build.yml", expected_exit_code=1)


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_arch_linter_invalid_oss_file_name():
    """Test that GCP auth inside an OSS-named file fails."""
    content = textwrap.dedent(
        """
    name: Build
    jobs:
      build:
        runs-on: ubuntu-latest
        steps:
          - uses: google-github-actions/auth@v3
            with:
              workload_identity_provider: staging-wif
    """
    )
    _run_linter_test(content, "build_oss.yml", expected_exit_code=1)


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_arch_linter_valid_oss():
    """Test that an OSS pipeline without GCP auth passes."""
    content = textwrap.dedent(
        """
    name: Build OSS
    jobs:
      build_oss:
        runs-on: ubuntu-latest
        steps:
          - run: echo "No GCP auth here"
    """
    )
    _run_linter_test(content, "build_oss.yml", expected_exit_code=0)


def _run_linter_test(yaml_content, filename, expected_exit_code):
    # test file is in ai_trading_bot/tests/unit/test_arch_linter.py
    # root is 3 levels up
    current_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.path.join(current_dir, "..", "..", ".."))
    script_path = os.path.join(root_dir, "scripts", "arch_linter.py")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fake .github/workflows structure
        workflows_dir = os.path.join(tmpdir, ".github", "workflows")
        os.makedirs(workflows_dir)

        filepath = os.path.join(workflows_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        # Run the linter in the tmpdir so glob.glob picks up the fake file
        result = subprocess.run(
            [sys.executable, script_path],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )

        assert result.returncode == expected_exit_code, (
            f"Expected {expected_exit_code}, got {result.returncode}. "
            f"STDERR: {result.stderr}"
        )
