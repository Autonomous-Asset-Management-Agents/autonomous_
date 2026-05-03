# tests/unit/test_lstm_lazy_imports.py
# Epic 2.3 / I-1 — Verifies that torch/joblib/numpy/pandas are NOT at module level
# in lstm_strategy.py. Uses AST inspection to avoid transitive import side-effects
# from BaseStrategy (which pulls torch via its own dependencies).

import ast
import os


_LSTM_FILE = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "core", "strategies", "lstm_strategy.py"
    )
)

HEAVY_MODULES = {"torch", "joblib", "numpy", "pandas", "models"}


def _get_module_level_imports(filepath: str) -> set:
    """Return the set of top-level imported module names in a Python file."""
    with open(filepath, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=filepath)

    imported = set()
    for node in ast.iter_child_nodes(tree):  # only top-level nodes
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    return imported


def test_no_torch_at_module_level():
    """torch must NOT appear as a top-level import in lstm_strategy.py (Epic 2.3 / I-1)."""
    imports = _get_module_level_imports(_LSTM_FILE)
    assert "torch" not in imports, (
        f"torch is still imported at module level in lstm_strategy.py. "
        f"All module-level imports: {sorted(imports)}"
    )


def test_no_joblib_at_module_level():
    """joblib must NOT appear as a top-level import in lstm_strategy.py."""
    imports = _get_module_level_imports(_LSTM_FILE)
    assert (
        "joblib" not in imports
    ), f"joblib is still at module level: {sorted(imports)}"


def test_no_numpy_at_module_level():
    """numpy must NOT appear as a top-level import in lstm_strategy.py."""
    imports = _get_module_level_imports(_LSTM_FILE)
    assert "numpy" not in imports, f"numpy is still at module level: {sorted(imports)}"


def test_no_pandas_at_module_level():
    """pandas must NOT appear as a top-level import in lstm_strategy.py."""
    imports = _get_module_level_imports(_LSTM_FILE)
    assert (
        "pandas" not in imports
    ), f"pandas is still at module level: {sorted(imports)}"


def test_no_models_torch_model_at_module_level():
    """models.torch_model (which transitively pulls torch) must NOT be at top level."""
    imports = _get_module_level_imports(_LSTM_FILE)
    assert (
        "models" not in imports
    ), f"models (torch_model) is still at module level: {sorted(imports)}"
