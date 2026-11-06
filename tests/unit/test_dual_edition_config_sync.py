import importlib
import os
import sys


def test_dual_edition_config_sync():
    """
    Test that config.oss.py exposes the same configuration symbols as config.py.
    This ensures the Dual-Edition-Invariante is maintained and the OSS snapshot build does not crash.
    """
    # Import the enterprise config
    # We must load config.oss.py manually since 'config' is already imported
    import importlib.util

    import config as config_full

    oss_path = os.path.join(os.path.dirname(config_full.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss", oss_path)
    config_oss = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_oss)

    # 1. Check get_config exists
    assert hasattr(
        config_oss, "get_config"
    ), "config.oss.py must implement get_config()"

    # 2. Check get_config() returns an object with all RuntimeConfigState fields
    full_cfg = config_full.get_config()
    oss_cfg = config_oss.get_config()

    # Get all fields from RuntimeConfigState (Pydantic model)
    fields = config_full.RuntimeConfigState.model_fields.keys()

    missing_fields = []
    for field in fields:
        if not hasattr(oss_cfg, field):
            missing_fields.append(field)

    assert (
        not missing_fields
    ), f"config.oss.py's get_config() is missing fields: {missing_fields}"

    # 3. Check specific ML fields mentioned in PR 1159
    assert hasattr(oss_cfg, "ML_PREDICTION_ENABLED"), "ML_PREDICTION_ENABLED missing"
    assert hasattr(
        oss_cfg, "ML_SENTIMENT_BLEND_ENABLED"
    ), "ML_SENTIMENT_BLEND_ENABLED missing"
