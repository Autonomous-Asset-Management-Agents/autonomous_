# tests/unit/test_config_flags.py
# Epic 4.1 — Config Sync: smoke test for new flags and env-configurable compliance settings
#
# Gherkin:
#   Given: config.py is imported
#   When:  flag attributes are accessed
#   Then:  they have the correct types and default values

import os
import importlib


def _reload_config_with_env(**overrides):
    """Reload config module with temporary env var overrides."""
    original = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: str(v) for k, v in overrides.items()})
    try:
        import config

        importlib.reload(config)
        return config
    finally:
        for k, orig in original.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


class TestConfigFlags:
    def test_intelligent_exit_enabled_exists(self):
        import config

        assert hasattr(config, "INTELLIGENT_EXIT_ENABLED")
        assert isinstance(config.INTELLIGENT_EXIT_ENABLED, bool)

    def test_intelligent_exit_enabled_default_true(self):
        cfg = _reload_config_with_env()
        assert cfg.INTELLIGENT_EXIT_ENABLED is True

    def test_round_table_use_ml_models_exists(self):
        import config

        assert hasattr(config, "ROUND_TABLE_USE_ML_MODELS")
        assert isinstance(config.ROUND_TABLE_USE_ML_MODELS, bool)

    def test_round_table_use_ml_models_default_false(self):
        cfg = _reload_config_with_env()
        assert cfg.ROUND_TABLE_USE_ML_MODELS is False

    def test_round_table_ml_models_env_override(self):
        cfg = _reload_config_with_env(ROUND_TABLE_USE_ML_MODELS="True")
        assert cfg.ROUND_TABLE_USE_ML_MODELS is True

    def test_compliance_max_order_value_is_float(self):
        import config

        assert isinstance(config.COMPLIANCE_MAX_ORDER_VALUE, float)

    def test_compliance_max_order_value_default(self):
        cfg = _reload_config_with_env()
        assert cfg.COMPLIANCE_MAX_ORDER_VALUE == 50000.0

    def test_compliance_max_order_value_env_override(self):
        cfg = _reload_config_with_env(COMPLIANCE_MAX_ORDER_VALUE="1000.0")
        assert cfg.COMPLIANCE_MAX_ORDER_VALUE == 1000.0

    def test_compliance_max_daily_trades_is_int(self):
        import config

        assert isinstance(config.COMPLIANCE_MAX_DAILY_TRADES, int)

    def test_lstm_model_version_env_override(self):
        cfg = _reload_config_with_env(LSTM_MODEL_VERSION="v2")
        assert cfg.LSTM_MODEL_VERSION == "v2"

    def test_rl_model_version_env_override(self):
        cfg = _reload_config_with_env(RL_MODEL_VERSION="rl_agent_v10")
        assert cfg.RL_MODEL_VERSION == "rl_agent_v10"
