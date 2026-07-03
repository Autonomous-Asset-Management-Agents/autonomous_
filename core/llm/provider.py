"""LLM provider seam (G4a-1, #1050) — the single sanctioned LLM entry point.

The desktop edition lets the user choose a cloud LLM (API key) OR a fully
local Ollama; the cloud edition must never depend on a local LLM runtime
(BORA). One factory resolves the provider from ``LLM_PROVIDER``:

  unset / "gemini"   -> the existing ``get_gemini_instance()`` singleton,
                        returned UNWRAPPED and UNCACHED — the cloud default
                        path is the identical object it is today, and the
                        gemini client's retry-while-None contract
                        (core/gemini_client.py L188-196) stays intact.
  "ollama"           -> ``OllamaProvider`` (desktop opt-in), refused LOUDLY
                        under Cloud Run (K_SERVICE) — fail-closed, precedent
                        ``core/database/session.py`` `_guard_cloud_sqlite_fallback`.
  anything else      -> WARNING + gemini path (anthropic/openai are Phase 2).

This module is DORMANT in G4a-1: no production code imports it until the
consumer-migration PRs (G4a-2/2c) — enforced by the grep-gate test in
tests/unit/test_llm_provider.py.

Both providers expose the same duck-typed surface consumers already use:
``generate_content(prompt, max_output_tokens) -> str`` and
``generate_content_async(...)``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from core.llm.health import resolved_provider_name
from core.llm.telemetry import record_call

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT_S = 120.0  # CPU boxes generate slowly; cap, don't hang forever


def _guard_cloud_ollama() -> None:
    """BORA fail-closed: a local LLM runtime must never be a Cloud Run dependency."""
    if os.environ.get("K_SERVICE"):
        raise RuntimeError(
            "LLM_PROVIDER=ollama is forbidden on Cloud Run (BORA: cloud-native "
            "must not depend on a local LLM runtime). Configure GEMINI_API_KEY."
        )


class OllamaProvider:
    """Local-LLM provider backed by Ollama's /api/generate endpoint.

    Error contract: returns "" on ANY failure (WARNING-logged, never DEBUG —
    CODING_POLICY §5.6, never raises) so callers see the same shape as a
    failed Gemini call and degrade gracefully.

    Decoding is DETERMINISTIC (temperature 0.0, fixed seed) — same input ⇒
    same output, so desktop insights don't re-roll on every engine restart
    (bundle-proven).
    """

    def __init__(
        self, model: Optional[str] = None, base_url: Optional[str] = None
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or os.getenv("LOCAL_LLM_MODEL") or "llama3.2"

    def _payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "seed": 7,
                "num_predict": max_output_tokens,
            },
        }

    def generate_content(self, prompt: str, max_output_tokens: int = 512) -> str:
        # ADR-OBS-01 / PR D: time the real generate call (PURE OBSERVATION). The
        # perf_counter + record_call are fail-safe — a timing failure can never
        # alter this method's result or its "" error contract.
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            import httpx

            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json=self._payload(prompt, max_output_tokens),
                timeout=_OLLAMA_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.warning(
                    "OllamaProvider: HTTP %s from %s — returning empty reply.",
                    resp.status_code,
                    self.base_url,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return str(resp.json().get("response") or "")
        except Exception as exc:
            logger.warning(
                "OllamaProvider: generate failed (%s) — returning empty reply.", exc
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)

    async def generate_content_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> str:
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            import httpx

            # Per-call client: a cached AsyncClient binds to the first event
            # loop it runs on (same bug class as the per-loop lock in
            # core/database/session.py). Connection setup to localhost is
            # negligible next to generation time.
            async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json=self._payload(prompt, max_output_tokens),
                )
            if resp.status_code != 200:
                logger.warning(
                    "OllamaProvider: HTTP %s from %s — returning empty reply.",
                    resp.status_code,
                    self.base_url,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return str(resp.json().get("response") or "")
        except Exception as exc:
            logger.warning(
                "OllamaProvider: async generate failed (%s) — returning empty reply.",
                exc,
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)


# Only the (stateless, cheap) Ollama provider is memoized; the gemini path
# delegates uncached so a None (key missing / transient build failure) is
# retried by gemini_client itself on the next call — never frozen by the seam.
_ollama_singleton: Optional[OllamaProvider] = None
_ollama_lock = threading.Lock()


def _get_ollama_singleton() -> OllamaProvider:
    global _ollama_singleton
    if _ollama_singleton is None:
        with _ollama_lock:
            if _ollama_singleton is None:
                _ollama_singleton = OllamaProvider()
    return _ollama_singleton


def get_llm_provider() -> Optional[Any]:
    """Resolve the process-wide LLM provider from ``LLM_PROVIDER``.

    Returns None exactly when today's ``get_gemini_instance()`` returns None
    (no key / SDK unavailable) — callers already handle that.
    """
    # ADR-014: LLM_PROVIDER is an env boundary read in this seam (deliberately
    # NOT exposed via get_config() — test_oss_config_parity guards only the
    # get_config() surface, and centralising it would add config.oss.py drift).
    provider = resolved_provider_name()
    if provider == "ollama":
        _guard_cloud_ollama()
        return _get_ollama_singleton()
    if provider not in ("", "gemini"):
        logger.warning(
            "Unknown LLM_PROVIDER=%r — falling back to the gemini path "
            "(anthropic/openai land in Phase 2).",
            provider,
        )
    # Lazy import: G4a-2 routes gemini_client's chat helpers through this
    # factory — a top-level import here would create an import cycle.
    from core.gemini_client import get_gemini_instance

    return get_gemini_instance()


def reset_llm_provider() -> None:
    """Test hook — drop the memoized Ollama provider."""
    global _ollama_singleton
    with _ollama_lock:
        _ollama_singleton = None


__all__ = [
    "OllamaProvider",
    "get_llm_provider",
    "reset_llm_provider",
]
