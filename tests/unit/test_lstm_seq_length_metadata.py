# tests/unit/test_lstm_seq_length_metadata.py
# Issue #1878 (MLA-4) — Fix 1: the serve-side sequence length MUST come from the
# model's own metadata, never from a hardcode.
#
# The shipped v1 model was trained/validated with sequence_length=20
# (data/model_metadata.json) while the serve code hardcoded 60 — every live
# prediction ran in an unvalidated window configuration. The v2 metadata
# (model_metadata_v2.json) ships with sequence_length=60.
#
# Gherkin:
#   Given model metadata with a valid sequence_length
#   When the strategy loads its model assets / runs inference
#   Then the serve window equals the metadata value
#   And a missing/invalid value falls back to the module default with a WARNING

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _make_lstm_strategy():
    """Minimal LSTMDynamicStrategy double bypassing heavy ML init (see #938 tests)."""
    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()
    strategy.scaler_x = MagicMock()
    strategy.torch = MagicMock()
    strategy.np = MagicMock()
    strategy.pd = MagicMock()
    strategy.joblib = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close", "volume", "rsi_14"]
    strategy._initialized = True
    strategy.device = "cpu"
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.client.__class__ = MagicMock  # not SimulationAdapter
    return strategy


def _make_rl_strategy():
    """Minimal RLStrategy double for the RLSignalMixin inference path."""
    from core.strategies.rl_strategy import RLStrategy

    strategy = RLStrategy.__new__(RLStrategy)
    strategy.torch_model = MagicMock()
    strategy.scaler_x = MagicMock()
    strategy.scaler_y = None
    strategy.features_list = ["close"]
    strategy.client = MagicMock()
    strategy.data_provider = MagicMock()
    strategy.device = "cpu"
    strategy.log_thought = MagicMock()
    return strategy


def _hist(rows: int):
    import pandas as pd

    dates = pd.date_range("2024-01-01", periods=rows)
    return pd.DataFrame(
        {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1e6},
        index=dates,
    )


def _now():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# resolve_sequence_length — the single serve-side metadata reader
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestResolveSequenceLength:
    def test_reads_valid_sequence_length_from_metadata(self):
        from models.torch_model import resolve_sequence_length

        assert resolve_sequence_length({"sequence_length": 20}) == 20
        assert resolve_sequence_length({"sequence_length": 60}) == 60

    def test_missing_key_falls_back_with_warning(self, caplog):
        from models.torch_model import resolve_sequence_length

        with caplog.at_level(logging.WARNING):
            assert resolve_sequence_length({}, fallback=60, context="TestCtx") == 60
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "fallback MUST be logged at WARNING (CODING_POLICY §5.6)"
        assert any("sequence_length" in r.getMessage() for r in warnings)

    def test_none_metadata_falls_back_with_warning(self, caplog):
        from models.torch_model import resolve_sequence_length

        with caplog.at_level(logging.WARNING):
            assert resolve_sequence_length(None, fallback=60) == 60
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    @pytest.mark.parametrize("bad", [0, -5, "60", None, True, 59.0])
    def test_invalid_values_fall_back_with_warning(self, bad, caplog):
        from models.torch_model import resolve_sequence_length

        with caplog.at_level(logging.WARNING):
            assert resolve_sequence_length({"sequence_length": bad}, fallback=60) == 60
        assert any(
            r.levelno == logging.WARNING for r in caplog.records
        ), f"invalid sequence_length {bad!r} must WARN, not silently fall back"


# ---------------------------------------------------------------------------
# Shipped metadata regression — the actual files this fix is about
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestShippedMetadataRegression:
    @pytest.mark.skipif(
        not (_DATA_DIR / "model_metadata.json").is_file(),
        reason="shipped v1 metadata not present",
    )
    def test_shipped_v1_metadata_yields_20(self):
        """#1878 root finding: v1 was trained with seq 20, serve hardcoded 60."""
        from models.torch_model import resolve_sequence_length

        md = json.loads((_DATA_DIR / "model_metadata.json").read_text(encoding="utf-8"))
        assert resolve_sequence_length(md) == 20

    @pytest.mark.skipif(
        not (_DATA_DIR / "model_metadata_v2.json").is_file(),
        reason="v2 metadata not present",
    )
    def test_shipped_v2_metadata_yields_60(self):
        from models.torch_model import resolve_sequence_length

        md = json.loads(
            (_DATA_DIR / "model_metadata_v2.json").read_text(encoding="utf-8")
        )
        assert resolve_sequence_length(md) == 60


# ---------------------------------------------------------------------------
# Inference honours the instance window (metadata-driven), not the hardcode
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestServeWindowFollowsMetadata:
    @pytest.mark.anyio
    async def test_lstm_inference_uses_metadata_window_not_hardcode(self):
        """v1 scenario: sequence_length=20 (metadata), 55 rows of history.

        55 rows satisfy the feature warm-up floor (FEATURE_WARMUP_ROWS=50) but
        NOT the old hardcode (60): with the hardcode the inference early-returns
        before feature generation; with the metadata window (20) it MUST proceed.
        """
        strategy = _make_lstm_strategy()
        strategy.sequence_length = 20  # what _load_torch_model_assets sets for v1

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(55))
            await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_called_once()

    @pytest.mark.anyio
    async def test_lstm_without_metadata_window_uses_module_fallback(self):
        """A bare double without self.sequence_length keeps the module default (60):
        30 rows are insufficient, feature generation is never reached and the
        result is an abstention (None, None) — never a 0.0 pseudo-prediction."""
        strategy = _make_lstm_strategy()
        assert not hasattr(strategy, "sequence_length")

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(30))
            score, features = await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_not_called()
        assert score is None, (
            "short history must be an abstention (None), not a 0.0 pseudo-"
            "prediction that would rank a held position to the tail"
        )
        assert features is None

    @pytest.mark.anyio
    async def test_rl_inference_uses_metadata_window_not_hardcode(self):
        """Same v1 scenario for the RLSignalMixin serve path (rl_signal.py):
        55 rows ≥ warm-up floor (50) but < old hardcode (60)."""
        strategy = _make_rl_strategy()
        strategy.sequence_length = 20

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(55))
            await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_called_once()

    def test_lstm_asset_loading_stores_metadata_window(self, tmp_path):
        """_load_torch_model_assets must persist the metadata window on the instance.

        Uses a stubbed torch/scaler load — only the metadata plumbing is under test.
        """
        strategy = _make_lstm_strategy()
        # Minimal on-disk asset set
        metadata = {
            "features_list": ["close", "volume", "rsi_14"],
            "sequence_length": 20,
            "model_params": {
                "input_dim": 3,
                "hidden_dim": 4,
                "num_layers": 1,
                "output_dim": 1,
            },
        }
        for name in ("m.pth", "sx.pkl", "sy.pkl"):
            (tmp_path / name).write_bytes(b"x")
        (tmp_path / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")

        paths = (
            str(tmp_path / "m.pth"),
            str(tmp_path / "sx.pkl"),
            str(tmp_path / "sy.pkl"),
            str(tmp_path / "meta.json"),
        )

        fake_state = {"lstm.weight_ih_l0": MagicMock(shape=(16, 3))}
        strategy.torch.load = MagicMock(return_value=fake_state)

        with patch("models.torch_model.get_lstm_paths", return_value=paths), patch(
            "models.torch_model.LSTMModel"
        ) as mock_model_cls, patch(
            "core.strategies.lstm_strategy.safe_joblib_load",
            return_value=MagicMock(n_features_in_=3),
        ):
            mock_model_cls.return_value.to.return_value = MagicMock()
            strategy._load_torch_model_assets()

        assert strategy.sequence_length == 20


# ---------------------------------------------------------------------------
# Legacy SEQUENCE_LENGTH must not be re-exported — #1878 review Finding 4
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestLegacySequenceLengthNotExported:
    def test_sequence_length_not_in_strategies_all(self):
        """core.strategies re-exports (package + shim) must not advertise the
        legacy SEQUENCE_LENGTH=60 — zero importers, pure drift trap; the serve
        window comes from model metadata (resolve_sequence_length, #1878)."""
        import ast

        root = Path(__file__).resolve().parents[2]
        for rel in ("core/strategies/__init__.py", "core/strategies.py"):
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == "__all__" for t in node.targets
                ):
                    exported = [elt.value for elt in node.value.elts]
                    assert "SEQUENCE_LENGTH" not in exported, (
                        f"{rel}: legacy SEQUENCE_LENGTH re-exported in __all__ — "
                        f"drift trap (#1878 review Finding 4)"
                    )


# ---------------------------------------------------------------------------
# Feature warm-up floor — #1878 review Finding 1
# ---------------------------------------------------------------------------
#
# create_live_features() needs ~50 raw history rows before its slowest
# indicators carry real signal (sma_50: 50 closes; MACD 12/26/9: 26+9=35).
# With v1's metadata window of 20, gating on seq_len alone would accept
# 20–49 rows and serve placeholder features — which can be BOUGHT.
# Serve paths must gate on max(seq_len, FEATURE_WARMUP_ROWS).


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestFeatureWarmupFloor:
    def test_warmup_floor_covers_slowest_indicators(self):
        """The shared constant must cover sma_50 (50) and MACD 26+9 (35)."""
        from models.torch_model import FEATURE_WARMUP_ROWS

        assert FEATURE_WARMUP_ROWS == 50

    @pytest.mark.anyio
    async def test_lstm_short_history_below_warmup_floor_abstains(self):
        """Finding-1 acceptance: 30 rows + seq=20 → abstention (None, None),
        NOT a prediction on placeholder features."""
        strategy = _make_lstm_strategy()
        strategy.sequence_length = 20  # v1 metadata window

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(30))
            score, features = await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_not_called()
        assert score is None, (
            f"30 rows < warm-up floor (50) MUST abstain — got {score!r}. "
            f"sma_50/MACD would be fillna() placeholders, not signal."
        )
        assert features is None

    @pytest.mark.anyio
    async def test_lstm_history_at_warmup_floor_proceeds(self):
        """Boundary: exactly FEATURE_WARMUP_ROWS rows with seq=20 must proceed
        to feature generation (floor is inclusive)."""
        strategy = _make_lstm_strategy()
        strategy.sequence_length = 20

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(50))
            await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_called_once()

    @pytest.mark.anyio
    async def test_rl_short_history_below_warmup_floor_skips_inference(self):
        """RL serve path: 30 rows + seq=20 must never reach feature generation.

        (The RL return value stays numeric — abstention harmonisation for the
        RL path is coupled with Fix 2 and explicitly out of scope of #1878.)
        """
        strategy = _make_rl_strategy()
        strategy.sequence_length = 20

        with patch(
            "models.torch_model.create_live_features", return_value=None
        ) as mock_create, patch("asyncio.get_running_loop") as mock_loop:
            mock_loop.return_value.run_in_executor = AsyncMock(return_value=_hist(30))
            await strategy._get_torch_prediction(
                "AAPL", _now(), {"vix": 20.0, "latest_news_sentiment": 0.0}
            )

        mock_create.assert_not_called()
