import os
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.composition.root import CompositionRoot


@pytest.fixture(autouse=True)
def mock_adapters():
    """Mock adapter instantiation to prevent side-effects during tests."""
    with patch.object(
        CompositionRoot, "_build_state_port", return_value=MagicMock()
    ), patch.object(
        CompositionRoot, "_build_broker_port", return_value=MagicMock()
    ), patch.object(
        CompositionRoot, "_build_clock_port", return_value=MagicMock()
    ):
        yield
    CompositionRoot.reset()


@pytest.mark.vc0
def test_composition_root_is_cloud():
    """Test is_cloud evaluates K_SERVICE correctly."""
    CompositionRoot.reset()
    with patch.dict(os.environ, {"K_SERVICE": "1"}):
        root = CompositionRoot.get_instance()
        assert root.is_cloud is True

    CompositionRoot.reset()
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}, clear=True):
        root = CompositionRoot.get_instance()
        assert root.is_cloud is False
    CompositionRoot.reset()


@pytest.mark.vc0
@pytest.mark.mutates_global_state
def test_composition_root_is_local_desktop():
    """Test is_local_desktop evaluates correctly."""
    CompositionRoot.reset()
    with patch.dict(os.environ, {"K_SERVICE": "1"}):
        root = CompositionRoot.get_instance()
        assert root.is_local_desktop is False

    CompositionRoot.reset()
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}, clear=True):
        root = CompositionRoot.get_instance()
        assert root.is_local_desktop is True
    CompositionRoot.reset()


@pytest.mark.vc0
def test_composition_root_singleton_thread_safety():
    """Test that get_instance is thread safe."""
    CompositionRoot.reset()

    instances = []

    # Mocking __init__ to avoid importing heavy adapters during threading
    with patch.object(CompositionRoot, "__init__", lambda self: None):

        def get_inst():
            instances.append(CompositionRoot.get_instance())

        threads = [threading.Thread(target=get_inst) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        first_instance = instances[0]
        for inst in instances[1:]:
            assert inst is first_instance

    CompositionRoot.reset()


@pytest.mark.vc0
def test_composition_root_ports_initialization():
    """Test that ports are correctly initialized."""
    CompositionRoot.reset()
    root = CompositionRoot.get_instance()

    assert root.state_port is not None
    assert root.broker_port is not None
    assert root.clock_port is not None

    CompositionRoot.reset()
