"""
TFT inference wrapper for the per-symbol pytorch-forecasting checkpoints.

Each symbol has a trained model artefact at::

    core/ml/models/<SYMBOL>/
        checkpoint.pt          # TemporalFusionTransformer state_dict
        training_ds.pkl        # TimeSeriesDataSet (drives schema + scaling)
        scaler.pkl             # sklearn StandardScaler (legacy)
        metadata.json          # optional (newer training runs)

This module exposes a single class — ``TFTInferenceEngine`` — that loads the
artefacts for a symbol, materialises an inference-mode TimeSeriesDataSet from
the persisted training schema + the latest feature window, and returns a
quantile dict (bear/base/bull) plus a derived direction + confidence.

Stays read-only: never trains, never persists.
Failure semantics: any exception → returns ``None``. The caller (registry)
logs and falls through to ``ml_direction="unavailable"``.
"""

from __future__ import annotations

import io
import json
import logging
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd

from config import get_config

logger = logging.getLogger(__name__)


@dataclass
class TFTPrediction:
    """Public output of a single TFT inference call.

    Field names match the dict contract produced by
    ``stock_specialist._fetch_ml_prediction()``.
    """

    direction: str
    base_return_pct: float
    bear_return_pct: float
    bull_return_pct: float
    confidence: float
    attention_weights: Optional[List[float]] = None


# Dead-band: |base_return| < this → "neutral". 0.3% over 5d ≈ 0.06%/day noise floor.
_DIRECTION_DEAD_BAND_PCT = 0.3

# Confidence saturation: a quantile spread of this many % corresponds to ~0 confidence.
# 5d realised vol on S&P stocks averages ~4-6%, so use 4% as saturation point.
_CONFIDENCE_SATURATION_PCT = 4.0


class TFTInferenceEngine:
    """Load + predict for a single per-symbol pytorch-forecasting TFT checkpoint."""

    def __init__(self, symbol: str, model_dir: Path) -> None:
        self.symbol = symbol
        self.model_dir = Path(model_dir)
        self._model = None
        self._training_ds = None
        self._loaded = False
        self._load_error: Optional[str] = None
        # RF-3 / D3 (TOCTOU close): when the model_registry verify gate has SHA-256-verified
        # the artifacts, it pins the READ-ONCE VERIFIED BYTES here. load() then unpickles
        # exactly these buffers (never re-opens the path), so a swap of the underlying file
        # AFTER the gate's hash — real over a read-only GCS-FUSE mount — is wirkungslos.
        # Both None on the desktop/OSS direct-use path (no gate) → load() falls back to
        # path-based loading.
        self._pinned_ckpt_bytes: Optional[bytes] = None
        self._pinned_ds_bytes: Optional[bytes] = None

    @property
    def loaded(self) -> bool:
        return self._loaded

    def _resolve_training_ds_path(self) -> Optional[Path]:
        """Pick the training_ds.pkl whose feature schema matches the promoted
        checkpoint (ADR-ML-DS-01).

        Maps metadata.json's ``promoted_from`` basename to the sibling dataset,
        e.g. ``checkpoint_v2_seed0_10y_full491.pt`` →
        ``training_ds_v2_seed0_10y_full491.pkl``. Falls back to the legacy
        top-level ``training_ds.pkl`` when no promotion record exists.
        """
        # The gate calls this to learn WHICH ds file to read + hash; load()'s path-fallback
        # (no pinned bytes) calls it too. D3 pins the verified BYTES, not this path, so the
        # earlier _pinned_ds_path short-circuit is gone — the TOCTOU is closed by load()
        # consuming the pinned buffers, not by pinning the resolved path here.
        default = self.model_dir / "training_ds.pkl"
        meta_path = self.model_dir / "metadata.json"
        try:
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                promoted = meta.get("promoted_from")
                if promoted:
                    ckpt_name = Path(str(promoted).replace("\\", "/")).name
                    ds_name = ckpt_name.replace("checkpoint", "training_ds")
                    if ds_name.endswith(".pt"):
                        ds_name = ds_name[:-3] + ".pkl"
                    candidate = self.model_dir / ds_name
                    if candidate.exists():
                        return candidate
        except Exception as exc:
            logger.warning(
                "[TFT %s] dataset resolution fell back to default: %s", self.symbol, exc
            )
        return default

    def load(self) -> bool:
        """Lazy-load checkpoint + training_ds. Returns True on success.

        Returns False (and stores the reason on ``self._load_error``) when:
        - pytorch_forecasting not importable
        - training_ds.pkl missing or unpicklable
        - checkpoint.pt missing or doesn't match model architecture
        """
        if self._loaded:
            return True
        try:
            import torch
            from pytorch_forecasting import TemporalFusionTransformer
        except ImportError as exc:
            self._load_error = f"pytorch_forecasting not installed: {exc}"
            return False

        # D3 (TOCTOU close): if the verify gate pinned the read-once VERIFIED buffers,
        # unpickle EXACTLY those — never re-open the path. Otherwise (desktop/OSS direct
        # use, no gate) fall back to path-based loading (back-compat).
        if self._pinned_ckpt_bytes is not None and self._pinned_ds_bytes is not None:
            try:
                # nosec rationale: the RF-3 gate SHA-256-verified these exact bytes
                # against the signed manifest before pinning them (see ADR-SEC-RF3).
                self._training_ds = pickle.loads(self._pinned_ds_bytes)  # nosec B301
                state_dict = torch.load(
                    io.BytesIO(self._pinned_ckpt_bytes),
                    map_location="cpu",
                    weights_only=False,
                )
            except Exception as exc:
                self._load_error = f"verified-buffer load failed: {exc}"
                return False
            finally:
                # release the ~6 MB transient peak the moment the buffers are consumed
                self._pinned_ckpt_bytes = None
                self._pinned_ds_bytes = None
        else:
            ckpt_path = self.model_dir / "checkpoint.pt"
            if not ckpt_path.exists():
                self._load_error = f"checkpoint.pt missing at {ckpt_path}"
                return False
            # ADR-ML-DS-01: rebuild from the dataset matching the PROMOTED checkpoint.
            # Promotion copies the chosen seed checkpoint to checkpoint.pt but can
            # leave a stale top-level training_ds.pkl from an earlier feature-set
            # version (v1: 22 reals, no symbol embedding) while checkpoint.pt is v2
            # (27 reals + symbol embedding). Rebuilding from the stale dataset then
            # fails load_state_dict with a shape mismatch — which silently disabled
            # TFT for every symbol. Resolve the dataset named by metadata.json's
            # promoted_from; fall back to training_ds.pkl when no record exists.
            ds_path = self._resolve_training_ds_path()
            if ds_path is None or not ds_path.exists():
                self._load_error = (
                    f"training_ds (matching checkpoint) missing in {self.model_dir}"
                )
                return False
            try:
                # nosec rationale: the NO-GATE path (desktop/OSS direct use, or
                # manifest-not-required dev) — a checkpoint trusted by deployment
                # provenance, not the RF-3 hash gate (see ADR-SEC-RF3).
                with open(ds_path, "rb") as f:
                    self._training_ds = pickle.load(f)  # nosec B301
            except Exception as exc:
                self._load_error = f"training_ds.pkl load failed: {exc}"
                return False
            try:
                state_dict = torch.load(
                    ckpt_path, map_location="cpu", weights_only=False
                )
            except Exception as exc:
                self._load_error = f"checkpoint load failed: {exc}"
                return False

        try:
            # Infer the training hparams from the state_dict shapes so the
            # rebuilt model matches the persisted weights. The 491 per-symbol
            # checkpoints were all trained with the same hyperparameters
            # (hidden_size=64, attention_head_size=4, output_size=3, lstm_layers=1)
            # — confirmed via lightning_logs/version_5471/hparams.yaml. We
            # introspect the state_dict in case future training runs change them.
            hparams = _infer_hparams_from_state_dict(state_dict)
            # Rebuild the model from the persisted dataset schema PLUS the
            # introspected hparams. Without the hparams override, from_dataset()
            # falls back to hidden_size=16 default → state_dict shape mismatch.
            self._model = TemporalFusionTransformer.from_dataset(
                self._training_ds,
                **hparams,
            )
            self._model.load_state_dict(state_dict)
            # Set inference mode (no dropout, no batchnorm updates).
            self._model.train(False)
        except Exception as exc:
            self._load_error = f"model build/load failed: {type(exc).__name__}: {exc}"
            self._model = None
            return False

        self._loaded = True
        return True

    def predict(self, features_df: pd.DataFrame) -> Optional[TFTPrediction]:
        """Run inference on the last encoder_length + prediction_length rows.

        Args:
            features_df: DataFrame produced by ``FeatureBuilder.build()``,
                already containing all columns the training_ds expects.
                The function appends required ``group_id`` and ``target``
                placeholder columns if absent.

        Returns:
            TFTPrediction with quantile-derived direction/confidence, or
            None on any failure.
        """
        if not self.load():
            return None
        if self._model is None or self._training_ds is None:
            return None

        try:
            return self._predict_inner(features_df)
        except Exception as exc:
            logger.warning(
                "[TFT %s] inference failed: %s: %s",
                self.symbol,
                type(exc).__name__,
                exc,
            )
            return None

    def _predict_inner(self, features_df: pd.DataFrame) -> Optional[TFTPrediction]:
        import torch
        from pytorch_forecasting import TimeSeriesDataSet

        if features_df is None or features_df.empty:
            return None

        # Schema-introspect the training_ds to learn what columns + group_id it expects.
        ds_params = (
            self._training_ds.get_parameters()
            if hasattr(self._training_ds, "get_parameters")
            else {}
        )
        target_col = ds_params.get("target", "target")
        group_ids = ds_params.get("group_ids") or ["group_id"]
        max_encoder = int(ds_params.get("max_encoder_length", 60))
        max_pred = int(ds_params.get("max_prediction_length", 5))

        # The training data carries a categorical/numerical group_id. For
        # per-symbol checkpoints this collapses to a single literal value;
        # use the symbol string so the column type matches whatever was
        # persisted.
        df = features_df.copy()
        for gid in group_ids:
            if gid not in df.columns:
                df[gid] = self.symbol

        # Placeholder target — inference doesn't need real labels, but
        # TimeSeriesDataSet requires the column to exist.
        if target_col not in df.columns:
            df[target_col] = 0.0

        # Ensure time_idx exists + is monotonic int (pytorch-forecasting requirement).
        if "time_idx" not in df.columns:
            df["time_idx"] = range(len(df))
        df["time_idx"] = df["time_idx"].astype(int)

        # Reindex to the FULL training schema so the inference dataset can be
        # built even when the live feature pipeline could not produce every
        # column. On a laptop without a Polygon key the macro block
        # (vix_close, vix_term_premium, spy_ret_5d, tnx_change_5d,
        # sector_ret_5d, sector_relative_ret_5d) is absent, so FeatureBuilder
        # omits it. TimeSeriesDataSet.from_dataset() then raises
        # KeyError('vix_close') and EVERY per-symbol TFT returns None — even
        # for gate-passing models. We introspect the persisted schema and
        # backfill any MISSING expected column with a neutral default:
        #   * reals  → 0.0 (a centred/standardised macro feature of 0 is the
        #              "no signal" value; GroupNormalizer scaling is unaffected
        #              by a constant column)
        #   * categoricals/group_ids → this symbol (matches how group_id is
        #              already injected above; keeps the column dtype valid)
        # This is a graceful-degradation reindex, NOT a math change: columns
        # that ARE present pass through untouched, so a fully-fed pipeline
        # behaves identically. Only genuinely-missing columns are defaulted.
        real_cols: list = []
        for key in (
            "time_varying_unknown_reals",
            "time_varying_known_reals",
            "static_reals",
        ):
            real_cols.extend(ds_params.get(key) or [])
        cat_cols: list = []
        for key in (
            "time_varying_unknown_categoricals",
            "time_varying_known_categoricals",
            "static_categoricals",
        ):
            cat_cols.extend(ds_params.get(key) or [])

        defaulted_reals: list = []
        for col in real_cols:
            # time_idx is handled above; group_id/target handled below/above.
            if col in df.columns:
                continue
            df[col] = 0.0
            defaulted_reals.append(col)

        defaulted_cats: list = []
        for col in cat_cols:
            if col in df.columns:
                continue
            # group_ids were already injected above; this covers any extra
            # static/known categorical the schema declares.
            df[col] = self.symbol
            defaulted_cats.append(col)

        if defaulted_reals or defaulted_cats:
            logger.warning(
                "[TFT %s] defaulted %d missing schema column(s) — reals=%s cats=%s",
                self.symbol,
                len(defaulted_reals) + len(defaulted_cats),
                defaulted_reals,
                defaulted_cats,
            )

        # Need at least encoder+pred rows for the dataset to produce a window.
        min_rows = max_encoder + max_pred
        if len(df) < min_rows:
            logger.warning(
                "[TFT %s] insufficient feature rows (%d, need >=%d)",
                self.symbol,
                len(df),
                min_rows,
            )
            return None

        # Build inference dataset from the persisted training schema.
        try:
            infer_ds = TimeSeriesDataSet.from_dataset(
                self._training_ds,
                df,
                predict=True,
                stop_randomization=True,
            )
            loader = infer_ds.to_dataloader(train=False, batch_size=64, num_workers=0)
        except Exception as exc:
            logger.warning("[TFT %s] dataset build failed: %s", self.symbol, exc)
            return None

        # Quantile prediction. Returns tensor [N, prediction_length, n_quantiles].
        with torch.no_grad():
            try:
                raw = self._model.predict(loader, mode="quantiles", return_index=False)
            except TypeError:
                raw = self._model.predict(loader, mode="quantiles")

        return self._postprocess_quantiles(raw)

    def _postprocess_quantiles(self, raw) -> Optional[TFTPrediction]:
        """Turn the raw ``[N, H, Q]`` quantile tensor into a ``TFTPrediction``.

        Pure (no model / no pytorch_forecasting) so the numeric contract is unit-testable
        in CI. Reads the LAST sample (most recent window), reduces the horizon H to one
        step, picks three quantile indices and derives direction + confidence.

        The horizon-reduction and unit-scaling are gated by ``TFT_SERVING_FIX``
        (config, default OFF). OFF reproduces the historical behaviour byte-for-byte:
        AVERAGE across H and read the value as-is. ON applies the adversarially-verified
        M1+M3a fix — see the inline comments at each gated step.
        """
        _fix = get_config().TFT_SERVING_FIX
        try:
            arr = raw.detach().cpu().numpy() if hasattr(raw, "detach") else raw
        except Exception:
            arr = raw

        if hasattr(arr, "shape") and len(arr.shape) >= 2:
            sample = arr[-1]
        else:
            sample = arr
        if len(getattr(sample, "shape", [])) == 2:
            if _fix:
                # M3a: read decoder STEP 0 — the step the gate's walkforward_ic was
                # validated on (train_tft_per_symbol._score_fold) — not the horizon mean.
                sample = sample[0]
            else:
                sample = sample.mean(axis=0)
        q_count = int(getattr(sample, "shape", [3])[-1])
        if q_count == 7:
            bear_i, base_i, bull_i = 1, 3, 5
        elif q_count == 3:
            bear_i, base_i, bull_i = 0, 1, 2
        else:
            bear_i = 0
            base_i = q_count // 2
            bull_i = q_count - 1

        try:
            # The 2026-05-11 note said "if a future training run uses raw log-return,
            # switch to `* 100.0`". v2 IS raw decimal log-return (serving GroupNormalizer
            # scale ≈ 0.04), so the ×100 switch is now active behind TFT_SERVING_FIX (M1).
            # The trainer's own scoring multiplies by 100 (train_tft_per_symbol.
            # _score_fold); serving matches its unit. Revision: if a future training run
            # emits already-percent targets, drop the ×100 below and document.
            base_pct = float(sample[base_i])
            bear_pct = float(sample[bear_i])
            bull_pct = float(sample[bull_i])
        except Exception as exc:
            logger.warning("[TFT %s] quantile read failed: %s", self.symbol, exc)
            return None

        if _fix:
            # M1: decimal log-return → percent (the trainer's own scoring unit). The
            # ±0.3 dead-band and 4.0 saturation are already percent-calibrated.
            base_pct *= 100.0
            bear_pct *= 100.0
            bull_pct *= 100.0

        # Sanity check: quantiles should be monotonic. If not, sort by value.
        bear_pct, base_pct, bull_pct = sorted([bear_pct, base_pct, bull_pct])

        # Direction from base with dead-band.
        if base_pct > _DIRECTION_DEAD_BAND_PCT:
            direction = "up"
        elif base_pct < -_DIRECTION_DEAD_BAND_PCT:
            direction = "down"
        else:
            direction = "neutral"

        # Confidence = inverse normalised spread, clamped [0,1].
        spread = max(bull_pct - bear_pct, 1e-6)
        confidence = max(
            0.0, min(1.0, 1.0 - spread / (2.0 * _CONFIDENCE_SATURATION_PCT))
        )

        return TFTPrediction(
            direction=direction,
            base_return_pct=round(base_pct, 4),
            bear_return_pct=round(bear_pct, 4),
            bull_return_pct=round(bull_pct, 4),
            confidence=round(confidence, 4),
            attention_weights=None,
        )


def _infer_hparams_from_state_dict(state_dict: dict) -> dict:
    """Read TFT hyperparameters from a state_dict to rebuild a matching model.

    pytorch-forecasting's TemporalFusionTransformer.from_dataset() uses
    hidden_size=16 / attention_head_size=4 / output_size=7 by default. Our
    trained checkpoints used hidden_size=64 / attention_head_size=4 /
    output_size=3. Without these overrides, load_state_dict fails with
    shape mismatches throughout the model. We introspect:
      - hidden_size: shape of output_layer.weight = [output_size, hidden_size]
      - output_size: same tensor's first dim
      - attention_head_size: number of multihead_attn.k_layers (heads)
      - lstm_layers: count of lstm_encoder.weight_ih_l{i} keys
    """
    hp: dict = {}
    try:
        ol = state_dict.get("output_layer.weight")
        if ol is not None and hasattr(ol, "shape") and len(ol.shape) == 2:
            hp["output_size"] = int(ol.shape[0])
            hp["hidden_size"] = int(ol.shape[1])
    except Exception:
        pass
    try:
        # multihead_attn.k_layers.0.weight, ..., k_layers.N.weight
        head_indices = set()
        for k in state_dict.keys():
            if k.startswith("multihead_attn.k_layers."):
                parts = k.split(".")
                if len(parts) >= 3 and parts[2].isdigit():
                    head_indices.add(int(parts[2]))
        if head_indices:
            hp["attention_head_size"] = len(head_indices)
    except Exception:
        pass
    try:
        # hidden_continuous_size: prescalers map each real feature (1-dim)
        # into a [hidden_continuous_size]-dim vector. Read from any
        # decoder_variable_selection.prescalers.<name>.weight shape [HCS, 1].
        for k, v in state_dict.items():
            if (
                k.startswith("decoder_variable_selection.prescalers.")
                and k.endswith(".weight")
                and hasattr(v, "shape")
                and len(v.shape) == 2
                and v.shape[1] == 1
            ):
                hp["hidden_continuous_size"] = int(v.shape[0])
                break
    except Exception:
        pass
    try:
        lstm_layer_indices = set()
        for k in state_dict.keys():
            if k.startswith("lstm_encoder.weight_ih_l"):
                suffix = k[len("lstm_encoder.weight_ih_l") :]
                # may be "0", "1", "0_reverse" — take the numeric prefix
                idx_str = ""
                for ch in suffix:
                    if ch.isdigit():
                        idx_str += ch
                    else:
                        break
                if idx_str:
                    lstm_layer_indices.add(int(idx_str))
        if lstm_layer_indices:
            hp["lstm_layers"] = max(lstm_layer_indices) + 1
    except Exception:
        pass
    return hp


__all__ = ["TFTPrediction", "TFTInferenceEngine"]
