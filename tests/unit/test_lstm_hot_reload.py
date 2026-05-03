# tests/unit/test_lstm_hot_reload.py
# Epic 2.3-Pre / PR-C — TDD Red-Phase
# Issue E: LSTM Live-Reload — reload_weights(), atomares torch.load()
#
# Alle Tests ROT bis reload_weights() in lstm_strategy.py implementiert ist.
# Updated: Epic 2.3 / I-1 — strategy.torch set as instance attr (lazy imports)
# Updated: Epic 2.3 / I-2 — torch replaced with MagicMock for native CI runner
#   (torch is broken on ubuntu-latest without ML deps — all tests use MagicMock only)

import pickle
import threading
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_state_dict():
    """Baut ein minimales state_dict mit picklable Werten (keine MagicMock)."""
    return {"fc.weight": b"mock_weight_tensor_data"}


def _make_weights_file(tmp_path, state_dict):
    """Schreibt eine minimale state_dict-Datei mit pickle (kein torch nötig)."""
    path = str(tmp_path / "weights.pt")
    with open(path, "wb") as fh:
        pickle.dump(state_dict, fh)
    return path


def _make_lstm_strategy(state_dict=None):
    """Baut eine LSTMDynamicStrategy mit vollständig gemocktem torch."""
    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    torch_mock = MagicMock()
    torch_mock.load = MagicMock(return_value=state_dict or _mock_state_dict())

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    # Lazy-import Attribute setzen (Epic 2.3 / I-1 — torch ist jetzt self.torch)
    strategy.torch = torch_mock
    strategy.np = None
    strategy.pd = None
    strategy.joblib = None
    # Minimale Attribute die LSTMDynamicStrategy erwartet
    strategy.torch_model = MagicMock()
    strategy.torch_model.load_state_dict = MagicMock()
    strategy.torch_model.eval = MagicMock()
    strategy.device = "cpu"
    # _model_lock wird in __init__ gesetzt — hier manuell anlegen
    strategy._model_lock = threading.Lock()
    return strategy


# ---------------------------------------------------------------------------
# 1. reload_weights() — Grundfunktionalität
# ---------------------------------------------------------------------------


class TestLSTMReloadWeights:
    def test_reload_weights_calls_load_state_dict(self, tmp_path):
        """reload_weights() lädt state_dict und ruft load_state_dict() auf."""
        state_dict = _mock_state_dict()
        strategy = _make_lstm_strategy(state_dict=state_dict)
        model_path = _make_weights_file(tmp_path, state_dict)

        result = strategy.reload_weights(model_path)

        assert result is True
        strategy.torch_model.load_state_dict.assert_called_once_with(state_dict)
        strategy.torch_model.eval.assert_called_once()

    def test_reload_weights_returns_false_on_invalid_path(self):
        """reload_weights() gibt False zurück bei ungültigem Pfad (kein Crash)."""
        strategy = _make_lstm_strategy()

        result = strategy.reload_weights("/nonexistent/path/weights.pt")

        assert result is False
        strategy.torch_model.load_state_dict.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Thread-Safety
# ---------------------------------------------------------------------------


class TestLSTMReloadWeightsThreadSafety:
    def test_reload_weights_is_threadsafe_no_crash(self, tmp_path):
        """Concurrent reload + simulated inference läuft ohne Exception oder Deadlock."""
        state_dict = _mock_state_dict()
        strategy = _make_lstm_strategy(state_dict=state_dict)
        model_path = _make_weights_file(tmp_path, state_dict)

        errors = []

        def do_reload():
            try:
                strategy.reload_weights(model_path)
            except Exception as e:
                errors.append(e)

        def do_inference():
            try:
                # Simuliert: Inferenz liest Modell unter Lock
                with strategy._model_lock:
                    _ = strategy.torch_model  # Lesezugriff
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_reload) for _ in range(3)] + [
            threading.Thread(target=do_inference) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Thread-safety Fehler: {errors}"
