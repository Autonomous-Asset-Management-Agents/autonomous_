# tests/unit/test_config_flags.py
# Epic 4.1 — Config Sync: smoke test for new flags and env-configurable compliance settings
#
# Gherkin:
#   Given: config.py is imported
#   When:  flag attributes are accessed
#   Then:  they have the correct types and default values

import importlib
import os

import allure


def _reload_config_with_env(**overrides):
    """Reload config module with temporary env var overrides."""
    hidden_files = []
    # Search in current directory and parent directory to capture all env/env.oss files
    for base_dir in (".", ".."):
        for name in (".env.oss", ".env"):
            f = os.path.normpath(os.path.join(base_dir, name))
            if os.path.exists(f):
                try:
                    os.rename(f, f + ".tmp")
                    hidden_files.append(f)
                except Exception:
                    pass

    original = {k: os.environ.get(k) for k in overrides}
    os.environ.update({k: str(v) for k, v in overrides.items()})

    has_compliance_env = "COMPLIANCE_MAX_ORDER_VALUE" in os.environ
    orig_compliance_env = os.environ.get("COMPLIANCE_MAX_ORDER_VALUE")
    if "COMPLIANCE_MAX_ORDER_VALUE" not in overrides and has_compliance_env:
        os.environ.pop("COMPLIANCE_MAX_ORDER_VALUE", None)

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
        if "COMPLIANCE_MAX_ORDER_VALUE" not in overrides and has_compliance_env:
            os.environ["COMPLIANCE_MAX_ORDER_VALUE"] = orig_compliance_env

        for f in hidden_files:
            try:
                os.rename(f + ".tmp", f)
            except Exception:
                pass


@allure.feature("VC-5 Administration & Back-Office")
@allure.story("Administration")
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
        # ADR-C01: Max Order Value = 10,000 EUR (ESMA/MiFID II) — GAP5 fix locks
        # the env-default to the documented value (was 50000.0, contradicting
        # both the ADR and the ComplianceGuardian class default).
        cfg = _reload_config_with_env()
        assert cfg.COMPLIANCE_MAX_ORDER_VALUE == 10000.0

    def test_enable_compliance_guardian_default_true(self):
        # Lock-in: the guardian master switch defaults ON (opt-out, never opt-in).
        cfg = _reload_config_with_env()
        assert cfg.ENABLE_COMPLIANCE_GUARDIAN is True

    def test_enable_compliance_guardian_env_opt_out(self):
        # The explicit operator opt-out via env must keep working (rollback lever).
        cfg = _reload_config_with_env(ENABLE_COMPLIANCE_GUARDIAN="False")
        assert cfg.ENABLE_COMPLIANCE_GUARDIAN is False

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

    def test_use_limit_orders_exists(self):
        import config

        assert hasattr(config, "USE_LIMIT_ORDERS")
        assert isinstance(config.USE_LIMIT_ORDERS, bool)

    def test_use_limit_orders_default_false(self):
        cfg = _reload_config_with_env()
        assert cfg.USE_LIMIT_ORDERS is False

    def test_use_limit_orders_env_override(self):
        cfg = _reload_config_with_env(USE_LIMIT_ORDERS="True")
        assert cfg.USE_LIMIT_ORDERS is True

    def test_limit_order_spread_buffer_pct_exists(self):
        import config

        assert hasattr(config, "LIMIT_ORDER_SPREAD_BUFFER_PCT")
        assert isinstance(config.LIMIT_ORDER_SPREAD_BUFFER_PCT, float)

    def test_limit_order_spread_buffer_pct_default(self):
        cfg = _reload_config_with_env()
        assert cfg.LIMIT_ORDER_SPREAD_BUFFER_PCT == 0.001

    def test_limit_order_spread_buffer_pct_env_override(self):
        cfg = _reload_config_with_env(LIMIT_ORDER_SPREAD_BUFFER_PCT="0.005")
        assert cfg.LIMIT_ORDER_SPREAD_BUFFER_PCT == 0.005
