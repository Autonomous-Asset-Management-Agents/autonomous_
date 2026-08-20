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
import pytest


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


def _restore_config_module():
    """Rebuild `config` from the RESTORED environment.

    ``_reload_config_with_env`` restores the env vars and the hidden ``.env`` files in its
    ``finally`` — but not the module it reloaded. The live ``config`` therefore keeps the
    override, and every later test in the session reads it. It cannot be done inside that
    ``finally``: the helper returns the live module, so restoring before the caller's
    assertion would hand back values the test never asked for. Hence a fixture, which
    runs after the assertions.
    """
    import config

    importlib.reload(config)


@pytest.fixture(autouse=True)
def _config_module_is_restored_after_each_test():
    """Contain the reload leak to the test that wants it.

    The leak was invisible for a long time because a LATER test in this file called the
    helper again, and that reload — run after the env restore — happened to rebuild the
    defaults. A ``-k`` filter that deselected those tests removed the accidental cleanup,
    and ``COMPLIANCE_MAX_ORDER_VALUE=1000.0`` (vs the 10,000 default) rode into
    ``risk_manager``'s ESMA order-value cap: ``1000 * 0.9999 / price``. That is the exact
    factor-10 discrepancy seen in ``test_risk_manager`` and
    ``test_position_size_vol_targeting`` (9.999 instead of 99.99 at price 100).
    """
    yield
    _restore_config_module()


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


class TestReloadLeakContainment:
    """The reload helper must not leak an override into the rest of the session."""

    def test_restore_reverses_an_override(self):
        """Behavioural: the restore rebuilds the default the helper overrode."""
        import config

        default = config.COMPLIANCE_MAX_ORDER_VALUE
        assert default != 1000.0, "pick a canary the test does not already equal"

        cfg = _reload_config_with_env(COMPLIANCE_MAX_ORDER_VALUE="1000.0")
        assert cfg.COMPLIANCE_MAX_ORDER_VALUE == 1000.0  # visible DURING the test...

        _restore_config_module()
        assert config.COMPLIANCE_MAX_ORDER_VALUE == default  # ...and gone after

    def test_restore_is_wired_as_an_autouse_fixture(self, request):
        """Structural: the restore is worthless unless it runs without being requested.

        Every test that overrode something would otherwise have to remember to clean up —
        which is exactly what did not happen for the whole life of this file. This test
        does NOT request the fixture, so finding it in `request.fixturenames` is proof
        that it is autouse (public API, unlike the fixture's private marker attribute).
        """
        assert (
            "_config_module_is_restored_after_each_test" in request.fixturenames
        ), f"restore fixture is not autouse; active fixtures: {request.fixturenames}"

    def test_config_is_clean_here_although_an_earlier_test_overrode_it(self):
        """End-to-end: several tests above ran `_reload_config_with_env` with overrides.

        Ordering within a file is deterministic, so reaching this point with the default
        intact proves the autouse restore actually fired between them. This is the
        assertion the four sizing tests in other files were implicitly making.
        """
        import config

        assert config.COMPLIANCE_MAX_ORDER_VALUE != 1000.0
        assert config.USE_LIMIT_ORDERS is False
        assert config.LIMIT_ORDER_SPREAD_BUFFER_PCT == 0.001
