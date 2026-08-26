import allure
import pytest

from scripts.sync_ci_requirements import filter_requirements


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_filter_requirements_removes_heavy_deps():
    input_reqs = [
        "numpy==1.26.4\n",
        "torch==2.12.1+cpu\n",
        "pandas==2.2.0\n",
        "torchvision==0.27.1+cpu\n",
        "stable-baselines3==2.7.1\n",
        "torchaudio==2.11.0+cpu\n",
        "sb3-contrib==2.7.1\n",
    ]

    expected_output = ["numpy==1.26.4\n", "pandas==2.2.0\n"]

    assert filter_requirements(input_reqs) == expected_output


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_filter_requirements_removes_gui_deps():
    input_reqs = [
        "PyQt6==6.10.2\n",
        "pyqt6-charts==6.10.0\n",
        "requests==2.32.5\n",
        "fastapi==0.135.1\n",
    ]

    expected_output = ["requests==2.32.5\n", "fastapi==0.135.1\n"]

    assert filter_requirements(input_reqs) == expected_output


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_filter_requirements_keeps_test_deps():
    input_reqs = [
        "pytest==9.0.2\n",
        "pytest-cov==5.0.0\n",
        "pytest-anyio==0.0.0\n",
        "fakeredis==2.34.1\n",
    ]

    expected_output = [
        "pytest==9.0.2\n",
        "pytest-cov==5.0.0\n",
        "pytest-anyio==0.0.0\n",
        "fakeredis==2.34.1\n",
    ]

    assert filter_requirements(input_reqs) == expected_output


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
def test_filter_requirements_handles_comments_and_empty_lines():
    input_reqs = [
        "# This is a comment\n",
        "\n",
        "   \n",
        "numpy==1.26.4  # Inline comment\n",
    ]

    expected_output = [
        "# This is a comment\n",
        "\n",
        "   \n",
        "numpy==1.26.4  # Inline comment\n",
    ]

    assert filter_requirements(input_reqs) == expected_output
