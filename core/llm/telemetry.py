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

import re
import time
from typing import Any, Dict, Optional

# #2962: the ONLY free-text ever admitted into the detail field — the seam's own
# synthetic http-status marker (provider.py raises RuntimeError("http_404") on a
# non-200). Everything else stays class-name-only (privacy: never message/args).
_HTTP_DETAIL_RE = re.compile(r"http_\d{3}")

_LLM_COUNTERS: Dict[str, Any] = {
    "llm_last_latency_ms": None,
    "llm_last_error": None,  # exception CLASS name only — never the message
    "llm_last_error_detail": None,  # #2962: http_NNN marker only — never text
    "llm_last_ok_ts": None,
    "llm_ok_count": 0,
    "llm_fail_count": 0,
}


def _record(
    latency_ms: Optional[float],
    error_class: Optional[str],
    error_detail: Optional[str] = None,
) -> None:
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
            _LLM_COUNTERS["llm_last_error_detail"] = None
        else:
            _LLM_COUNTERS["llm_fail_count"] = (
                int(_LLM_COUNTERS.get("llm_fail_count", 0) or 0) + 1
            )
            _LLM_COUNTERS["llm_last_error"] = str(error_class)
            # #2962: pattern-gated — only the seam's http_NNN marker passes,
            # so "model missing" (http_404) is distinguishable from "daemon
            # down" (ConnectError) without ever storing free-form text.
            _LLM_COUNTERS["llm_last_error_detail"] = (
                error_detail
                if error_detail is not None and _HTTP_DETAIL_RE.fullmatch(error_detail)
                else None
            )
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
        # #2962: extract ONLY the seam's synthetic http_NNN marker; any other
        # message shape is dropped here (never even offered to the store).
        error_detail = None
        if exc is not None:
            _msg = str(exc)
            if _HTTP_DETAIL_RE.fullmatch(_msg):
                error_detail = _msg
        _record(latency_ms, error_class, error_detail)
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
            "llm_last_error_detail": None,
            "llm_last_ok_ts": None,
            "llm_ok_count": 0,
            "llm_fail_count": 0,
        }
    )
