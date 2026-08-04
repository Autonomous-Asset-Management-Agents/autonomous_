"""ADR-OBS-01 / PR D: LLM call instrumentation (PURE OBSERVATION, VC-1).

Fail-safe module-level counters around the sanctioned LLM ``generate`` calls
(Ollama in ``core/llm/provider.py``, Gemini in ``core/gemini_client.py``). Every
mutation is DOUBLE-guarded (``_record`` swallows its own errors AND ``record_call``
guards the call site) so a timing/counter failure can NEVER raise into — or alter
the result of — the real LLM call used by the round-table agents + market scanner.

MACHINE-only: latencies (ms), exception CLASS names, counts, a last-ok timestamp.
NEVER stores or exposes prompt/response TEXT, API keys, or any PII (privacy —
machine view only).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

_LLM_COUNTERS: Dict[str, Any] = {
    "llm_last_latency_ms": None,
    "llm_last_error": None,  # exception CLASS name only — never the message
    "llm_last_ok_ts": None,
    "llm_ok_count": 0,
    "llm_fail_count": 0,
}


def _record(latency_ms: Optional[float], error_class: Optional[str]) -> None:
    """Fail-safe counter mutation — swallows EVERY error (observation must never
    perturb the LLM call). ``error_class`` is the exception CLASS name or None on
    success; on success the ok-count + ok-timestamp advance, on failure the
    fail-count + last-error advance. NEVER receives prompt/response text."""
    try:
        if latency_ms is not None:
            _LLM_COUNTERS["llm_last_latency_ms"] = round(float(latency_ms), 2)
        if error_class is None:
            _LLM_COUNTERS["llm_ok_count"] = (
                int(_LLM_COUNTERS.get("llm_ok_count", 0) or 0) + 1
            )
            _LLM_COUNTERS["llm_last_ok_ts"] = time.time()
            _LLM_COUNTERS["llm_last_error"] = None
        else:
            _LLM_COUNTERS["llm_fail_count"] = (
                int(_LLM_COUNTERS.get("llm_fail_count", 0) or 0) + 1
            )
            _LLM_COUNTERS["llm_last_error"] = str(error_class)
    except Exception:  # noqa: BLE001 — a broken counter must never break an LLM call
        pass


def record_call(start_perf: float, exc: Optional[BaseException]) -> None:
    """Call-site guard: DOUBLE fail-safe so even a wholly-replaced ``_record``
    (adversarial test / monkeypatch) can NEVER raise into the LLM call path.

    ``start_perf`` is a ``time.perf_counter()`` taken just before the generate
    call; ``exc`` is the exception that escaped the call (or None on success).
    Only the exception CLASS name is retained — never its message/args."""
    try:
        latency_ms = (time.perf_counter() - start_perf) * 1000.0
        error_class = type(exc).__name__ if exc is not None else None
        _record(latency_ms, error_class)
    except Exception:  # noqa: BLE001 — observation must never alter the LLM result
        pass


def get_llm_counters() -> Dict[str, Any]:
    """Read-only snapshot of the LLM counters (machine-only fields)."""
    try:
        return dict(_LLM_COUNTERS)
    except Exception:  # noqa: BLE001
        return {}


def reset_llm_counters() -> None:
    """Test/daily-reset helper — zeroes the LLM counters."""
    _LLM_COUNTERS.update(
        {
            "llm_last_latency_ms": None,
            "llm_last_error": None,
            "llm_last_ok_ts": None,
            "llm_ok_count": 0,
            "llm_fail_count": 0,
        }
    )
