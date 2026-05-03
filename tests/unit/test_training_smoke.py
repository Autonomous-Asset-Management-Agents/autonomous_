# tests/unit/test_training_smoke.py
# Epic 4.5 — LightGBM Training Script Port: import-only smoke test
#
# Gherkin:
#   Given: scripts/train_v4_lightgbm.py exists in the project
#   When:  The module constants are imported (no training executed)
#   Then:  All expected constants exist with correct types/values

from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import MagicMock


def _load_training_module():
    """Load train_v4_lightgbm.py as a module without executing main().

    Heavy module-level deps are always overwritten with MagicMocks so the
    smoke test is hermetic: no system libraries (libgomp.so.1), no network,
    no sklearn version/state issues from earlier tests in the session.
    The tests only inspect constants — no ML execution occurs.
    """
    stubs = (
        "lightgbm",
        "optuna",
        "optuna.logging",
        "sklearn",
        "sklearn.metrics",
        "sklearn.model_selection",
    )

    # Save original modules to prevent global pollution
    original_modules = {}
    for stub in stubs:
        original_modules[stub] = sys.modules.get(stub)
        sys.modules[stub] = MagicMock()

    try:
        script_dir = os.path.join(
            os.path.dirname(__file__),  # tests/unit/
            "..",  # tests/
            "..",  # AI Trading Bot/
            "scripts",
            "train_v4_lightgbm.py",
        )
        script_path = os.path.abspath(script_dir)
        spec = importlib.util.spec_from_file_location("train_v4_lightgbm", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Restore sys.modules so downstream tests like test_engine_boot can use real sklearn
        for stub in stubs:
            if original_modules[stub] is None:
                del sys.modules[stub]
            else:
                sys.modules[stub] = original_modules[stub]


class TestTrainingScriptSmoke:
    """Import-only smoke tests — no training data, no network calls."""

    def test_script_exists(self):
        """Training script file must exist at scripts/train_v4_lightgbm.py."""
        base = os.path.join(os.path.dirname(__file__), "..", "..", "scripts")
        path = os.path.abspath(os.path.join(base, "train_v4_lightgbm.py"))
        assert os.path.isfile(path), f"Training script not found at {path}"

    def test_agent_horizons_has_eight_agents(self):
        mod = _load_training_module()
        assert hasattr(mod, "AGENT_HORIZONS")
        expected = {
            "regime",
            "momentum",
            "drawdown",
            "squeeze",
            "catalyst",
            "specialist",
            "contrary",
            "construction",
        }
        assert set(mod.AGENT_HORIZONS.keys()) == expected

    def test_agent_horizons_all_positive(self):
        mod = _load_training_module()
        for agent, horizon in mod.AGENT_HORIZONS.items():
            assert (
                isinstance(horizon, int) and horizon > 0
            ), f"Agent {agent} horizon must be positive int"

    def test_target_auc_is_0_75(self):
        mod = _load_training_module()
        assert hasattr(mod, "TARGET_AUC")
        assert mod.TARGET_AUC == 0.75

    def test_n_optuna_trials_at_least_100(self):
        mod = _load_training_module()
        assert hasattr(mod, "N_OPTUNA_TRIALS")
        assert mod.N_OPTUNA_TRIALS >= 100

    def test_models_dir_path_contains_data(self):
        mod = _load_training_module()
        assert hasattr(mod, "MODELS_DIR")
        assert "data" in mod.MODELS_DIR and "models" in mod.MODELS_DIR

    def test_make_labels_function_exists(self):
        mod = _load_training_module()
        assert hasattr(mod, "_make_labels") or hasattr(mod, "make_labels")

    def test_main_function_exists(self):
        mod = _load_training_module()
        assert hasattr(mod, "main"), "training script must have a main() entry point"
