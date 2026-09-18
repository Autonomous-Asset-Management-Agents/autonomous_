"""Inference Factory (S1-E2 / DD-4) — bounded-concurrency seam for per-symbol TFT.

Dormant by default: ``IC_INFERENCE_BACKEND`` defaults to ``legacy`` (the literal
``model_registry.get_or_train`` path); no caller on the live path instantiates
the factory clients until an explicit, walk-forward-gated flag flip.
"""

from core.ml.inference.factory import (  # noqa: F401
    InferenceClient,
    LegacyInferenceClient,
    LocalSemaphoreInferenceClient,
    get_inference_client,
)

__all__ = [
    "InferenceClient",
    "LegacyInferenceClient",
    "LocalSemaphoreInferenceClient",
    "get_inference_client",
]
