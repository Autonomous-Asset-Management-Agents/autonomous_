"""#4890 (STW-3c, Epic #4625) — coded, English error responses.

A ``CodedHTTPException`` answers
``{"detail": "<English sentence>", "code": "<snake_case>", "params": {...}}``. ``detail``
stays a string, so every reader that uses it as text keeps working; ``code`` and ``params``
are what a multi-language console looks up (#4892). The handler is registered on
``api_routes.app`` only for this subclass — Starlette resolves handlers along the exception
MRO, so a plain ``HTTPException`` keeps the framework shape ``{"detail": ...}``.

**Code catalogue** (``/api/strategies*`` and ``/api/trading-settings``). Renaming a code
breaks the console catalogue that builds on it.

========================== ====== ==========================================================
code                       status params
========================== ====== ==========================================================
strategy_name_length       422    ``min``, ``max``
guardrail_in_strategy      422    ``keys``
strategy_limit_reached     422    ``limit``
unknown_setting            422    ``keys``
setting_value_not_allowed  422    ``key``, ``value``, ``allowed``
setting_value_type         422    ``key``, ``value``, ``expected`` (boolean / number /
                                  integer / finite_number / text)
setting_value_out_of_range 422    ``key``, ``value``, ``min``, ``max``
setting_text_too_long      422    ``key``, ``length``, ``max`` (never the text itself)
ranking_prompt_missing     422    ``source``
ranking_list_missing       422    —
request_invalid            422    ``fields`` (pydantic ``loc`` lists, never values)
strategy_not_found         404    ``name``
nonce_reused               409    —
hitl_required              409    —
strategy_assigned          409    ``name``, ``accounts`` (#4891: assigned → no delete)
sizing_change_unconfirmed  409    ``from``, ``to`` (#4989: live, also on assign-live)
audit_write_failed         503    —
strategy_store_unreadable  503    —
audit_chain_unreadable     503    —
simulation_input_invalid   422    — (#4917, ``/api/strategy-sim/runs``)
simulation_edition_limit   403    ``months``
simulation_corpus_missing  409    ``reason`` (``kein_korpus``)
simulation_keys_missing    409    ``reason`` (``keine_keys``)
========================== ====== ==========================================================

The 404 while ``STRATEGY_PROFILES_ENABLED`` is off stays the framework's
``{"detail": "Not Found"}`` on purpose: it must look like a missing route.

Plan: ``docs/4890-*/implementation_plan.md``.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse


class CodedHTTPException(HTTPException):
    """An ``HTTPException`` with a machine-readable ``code`` and its ``params``.

    Lives for one request only and is never pickled; ``HTTPException.__init__`` has no slot
    for ``code``/``params``, hence B042 is not applicable here.
    """

    def __init__(  # noqa: B042 — see docstring
        self,
        status_code: int,
        detail: str,
        *,
        code: str,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.params: Dict[str, Any] = dict(params or {})


async def coded_http_exception_handler(
    request: Request, exc: CodedHTTPException
) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "code": exc.code, "params": exc.params},
        headers=exc.headers,
    )
