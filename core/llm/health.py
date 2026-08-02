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
