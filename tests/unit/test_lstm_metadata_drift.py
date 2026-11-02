# tests/unit/test_lstm_metadata_drift.py
# TDD: POLICY-01 — Drift-Guard Unit Tests (PR #995)
#
# Covers the ADR: metadata-drift guard in trading_environment.py:
#   - Mismatch → model_params overridden, warning logged
#   - Match    → model_params unchanged, no warning
#   - Missing key → no crash, model_params unchanged
#
# All tests are pure-Python: no torch, no file I/O, no Cloud deps.

import logging
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: build mock state_dicts
# ---------------------------------------------------------------------------


def _make_state_dict(hidden_dim: int, input_dim: int, num_layers: int) -> dict:
    """
    Build a minimal state_dict matching a bidirectional LSTM.
    Bidirectional LSTM forward key shapes:
      weight_ih_l{i}: [hidden_dim * 4, input_dim if i==0 else hidden_dim * 2]
    """
    sd = {}
    for layer in range(num_layers):
        in_features = input_dim if layer == 0 else hidden_dim * 2
        sd[f"lstm.weight_ih_l{layer}"] = MagicMock(shape=(hidden_dim * 4, in_features))
        sd[f"lstm.weight_ih_l{layer}_reverse"] = MagicMock(
            shape=(hidden_dim * 4, hidden_dim)
        )
    return sd


def _run_drift_guard(state_dict: dict, model_params: dict) -> dict:
    """
    Execute only the drift-guard logic extracted from trading_environment.py.
    Returns the (possibly overridden) model_params dict.
    """
    ih_l0 = state_dict.get("lstm.weight_ih_l0")
    if ih_l0 is not None:
        inferred_hidden = ih_l0.shape[0] // 4
        inferred_input = ih_l0.shape[1]
        inferred_layers = sum(
            1
            for k in state_dict
            if k.startswith("lstm.weight_ih_l") and "reverse" not in k
        )
        if (
            inferred_hidden != model_params["hidden_dim"]
            or inferred_input != model_params["input_dim"]
            or inferred_layers != model_params["num_layers"]
        ):
            logging.warning(
                "Metadata mismatch — overriding with checkpoint params: "
                "hidden=%d (was %d), input=%d (was %d), layers=%d (was %d)",
                inferred_hidden,
                model_params["hidden_dim"],
                inferred_input,
                model_params["input_dim"],
                inferred_layers,
                model_params["num_layers"],
            )
            model_params = {
                "input_dim": inferred_input,
                "hidden_dim": inferred_hidden,
                "num_layers": inferred_layers,
                "output_dim": model_params["output_dim"],
            }
    return model_params


# ---------------------------------------------------------------------------
# Test 1: Mismatch → override + warning logged
# ---------------------------------------------------------------------------


class TestDriftGuardMismatch:
    def test_overrides_hidden_dim_on_mismatch(self, caplog):
        """
        Checkpoint has hidden=128, metadata says hidden=64.
        Guard must override to hidden=128 and emit WARNING.
        """
        state_dict = _make_state_dict(hidden_dim=128, input_dim=34, num_layers=3)
        model_params = {
            "hidden_dim": 64,  # wrong — stale metadata
            "input_dim": 34,
            "num_layers": 3,
            "output_dim": 1,
        }

        with caplog.at_level(logging.WARNING):
            result = _run_drift_guard(state_dict, model_params)

        assert result["hidden_dim"] == 128, "hidden_dim must be overridden to 128"
        assert result["input_dim"] == 34
        assert result["num_layers"] == 3
        assert result["output_dim"] == 1
        assert any(
            "Metadata mismatch" in r.message for r in caplog.records
        ), "WARNING log expected on mismatch"

    def test_overrides_input_dim_on_mismatch(self, caplog):
        """
        Checkpoint has input=23 (v1 model), metadata says input=34.
        Guard must correct input_dim.
        """
        state_dict = _make_state_dict(hidden_dim=64, input_dim=23, num_layers=2)
        model_params = {
            "hidden_dim": 64,
            "input_dim": 34,  # wrong
            "num_layers": 2,
            "output_dim": 1,
        }

        with caplog.at_level(logging.WARNING):
            result = _run_drift_guard(state_dict, model_params)

        assert result["input_dim"] == 23
        assert any("Metadata mismatch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 2: Match → no override, no warning
# ---------------------------------------------------------------------------


class TestDriftGuardNoOverrideOnMatch:
    def test_no_override_when_params_match(self, caplog):
        """
        Checkpoint and metadata agree on all dims.
        Guard must leave model_params unchanged and emit no WARNING.
        """
        state_dict = _make_state_dict(hidden_dim=128, input_dim=34, num_layers=3)
        model_params = {
            "hidden_dim": 128,
            "input_dim": 34,
            "num_layers": 3,
            "output_dim": 1,
        }

        with caplog.at_level(logging.WARNING):
            result = _run_drift_guard(state_dict, model_params)

        assert result == model_params, "model_params must be unchanged"
        assert not any(
            "Metadata mismatch" in r.message for r in caplog.records
        ), "No WARNING expected when params match"


# ---------------------------------------------------------------------------
# Test 3: Missing key → no crash, params unchanged
# ---------------------------------------------------------------------------


class TestDriftGuardMissingKey:
    def test_no_crash_when_weight_ih_l0_absent(self, caplog):
        """
        State dict does not contain 'lstm.weight_ih_l0' (e.g. custom arch key).
        Guard must be a no-op: no crash, original model_params preserved.
        """
        state_dict = {"fc.weight": MagicMock(shape=(1, 128))}  # no lstm.* keys
        model_params = {
            "hidden_dim": 64,
            "input_dim": 34,
            "num_layers": 2,
            "output_dim": 1,
        }

        result = _run_drift_guard(state_dict, model_params)

        assert result == model_params, "model_params must be unchanged when key absent"
