# R6-3c (#1677): compliance regression. A PUBLIC financial figure (the drawdown limit)
# must be DATA-DERIVED and fail-closed (omit if absent), never a hardcoded literal.

import re
from pathlib import Path

import pytest

SCENES_REST = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "shorts_generator"
    / "video_scenes_rest.py"
)


def test_no_hardcoded_percentage_literal():
    src = SCENES_REST.read_text(encoding="utf-8")
    assert '"98.50%"' not in src
    # No hardcoded "NN.NN%" / "NN%" public figure anywhere in the scene module.
    assert not re.search(
        r'"\d+(?:\.\d+)?%"', src
    ), "public figures must be data-derived, not hardcoded"


def test_drawdown_is_read_from_data_fail_closed():
    src = SCENES_REST.read_text(encoding="utf-8")
    assert 'data.get("daily_drawdown_limit_pct")' in src
    assert "if drawdown_limit_pct is not None:" in src


def test_scene3_and_scenes_rest_import():
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    import scripts.shorts_generator.video_scene_3  # noqa: F401
    import scripts.shorts_generator.video_scenes_rest  # noqa: F401
