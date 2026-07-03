# R6-3b (#1676): regression — the scene/chart modules previously used `from ... import *`,
# which masked NameErrors and missing siblings. With explicit imports every name must
# resolve, so simply importing the modules must not raise.

import pytest

pytest.importorskip("matplotlib")
pytest.importorskip("PIL")


def test_video_charts_imports():
    import scripts.shorts_generator.video_charts  # noqa: F401


def test_video_scene_2_imports():
    import scripts.shorts_generator.video_scene_2  # noqa: F401
