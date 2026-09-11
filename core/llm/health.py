"""LLM health probes (G4a-3).

A single reusable Ollama reachability check, imported by both the engine's
startup health check (``core/engine/base.py``) and the shadow-boot pre-flight
(``scripts/shadow_boot.py``) — DRY (no duplicated network probe), and kept in a
lightweight module so the boot path pulls no heavy transitive deps.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def resolved_provider_name() -> str:
    """The canonical LLM provider name from ``LLM_PROVIDER`` (ADR-014).

    The single read of ``LLM_PROVIDER`` for the **non-seam** boot modules
    (``core/engine/base.py``, ``scripts/shadow_boot.py``) so they don't read the
    env directly; ``core/llm/provider.py`` (the seam) uses it too — one source of
    truth. Recognised values: ``"gemini"`` (default when unset), ``"ollama"``
    (local, desktop opt-in), and the cloud providers ``"openai"`` / ``"anthropic"``
    (P2, #1406). Any unrecognised value is returned verbatim and the seam falls
    back to the gemini path.
    """
    return (os.getenv("LLM_PROVIDER") or "gemini").strip().lower()


def resolved_model_name(provider: Optional[str] = None) -> Optional[str]:
    """#2962 — the provider-TRUE model identity, one source of truth.

    Resolves EXACTLY like the seam's providers do (``core/llm/provider.py``
    consumes this helper), so a diagnostics read can never drift from what the
    engine actually asks for again: /engine-diagnostics reported the GEMINI
    model while LLM_PROVIDER=ollama queried ``llama3.2`` (177 anonymous fails).
    Env/config reads only — NO live probe, NO provider construction.
    """
    p = (provider or resolved_provider_name()).strip().lower()
    if p == "ollama":
        return os.getenv("LOCAL_LLM_MODEL") or "llama3.2"
    if p == "openai":
        return os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
    if p == "anthropic":
        return os.getenv("ANTHROPIC_MODEL") or "claude-3-5-haiku-latest"
    try:  # gemini (default) — edition-aware config read
        import config

        return getattr(config, "GEMINI_MODEL_NAME", None)
    except Exception:  # noqa: BLE001 — a config fault must not break a probe
        return None


async def ollama_model_present(
    model: str, base_url: Optional[str] = None, timeout: float = 5.0
) -> Optional[bool]:
    """#2962 — is ``model`` pulled on the local daemon? None = probe failed.

    ``GET /api/tags`` lists installed models; a reachable daemon WITHOUT the
    configured model boots green and then fails every generate with http_404.
    Tag-insensitive match (``phi3.5`` matches ``phi3.5:latest``). Never raises —
    any error → None so callers can only WARN, never abort the boot on it.
    """
    url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip(
        "/"
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/tags")
        if resp.status_code != 200:
            return None
        names = [str(m.get("name") or "") for m in (resp.json().get("models") or [])]
        base = model.split(":", 1)[0]
        return any(n == model or n.split(":", 1)[0] == base for n in names)
    except Exception:  # noqa: BLE001 — a probe fault must never break the boot
        return None


async def ollama_reachable(
    base_url: Optional[str] = None, timeout: float = 5.0
) -> bool:
    """True iff the local Ollama daemon answers ``GET {base_url}/api/tags`` with 200.

    Native async via ``httpx.AsyncClient``. Never raises — any error → False, so
    a caller in a startup health check can branch on a clean bool without an
    escaped exception bringing the check down.
    """
    url = (base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip(
        "/"
    )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{url}/api/tags")
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Ollama reachability probe failed (%s) at %s.", exc, url)
        return False
