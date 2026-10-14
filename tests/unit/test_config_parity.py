import importlib.util
import sys

import pytest

from settings import RuntimeConfigState


@pytest.mark.mutates_global_state
@pytest.mark.vc0
def test_config_parity():
    # Load Enterprise config
    config_ent = importlib.import_module("config")
    importlib.reload(config_ent)
    config_ent.__dict__.pop("DATA_DIR", None)
    config_ent.__dict__.pop("USER_DATA_DIR", None)

    # Load OSS config (since it's named config.oss.py, we have to load it from path)
    import os

    oss_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.oss.py"
    )
    spec = importlib.util.spec_from_file_location("config_oss", oss_path)
    config_oss = importlib.util.module_from_spec(spec)
    sys.modules["config_oss"] = config_oss
    spec.loader.exec_module(config_oss)

    # Get all the keys from the shared RuntimeConfigState model
    settings_keys = RuntimeConfigState.model_fields.keys()

    for key in settings_keys:
        # 1. Assert that both modules export the key (PEP-562 delegation works)
        val_ent = getattr(config_ent, key)
        val_oss = getattr(config_oss, key)

        # 2. Assert values are identical (BORA doctrine)
        assert val_ent == val_oss, f"BORA Parity Bruch bei Schlüssel {key}"
