"""#3628 — read-only guard for the public console API.

The public live-demo serves the demo paper account through `serve_public_api.py`.
When ``PUBLIC_READ_ONLY`` is truthy (the demo deployment) every mutating request is
refused **server-side**, before auth or any upstream engine call — not by hiding
controls in the UI, and not by a per-path blacklist a newly-added POST route could
slip past, but by a **method whitelist**: only GET/HEAD/OPTIONS pass, everything
else gets 405. This is the server-side half of `src/lib/publicMode.ts` (which only
hides the controls in the browser).

Kept in its own tiny, dependency-free module so it is unit-testable without importing
the heavy `serve_public_api` app (redis / firebase / limiter). Wire it in with::

    from public_readonly import read_only_guard
    app.middleware("http")(read_only_guard)
"""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})

READ_ONLY_MESSAGE = "This public demo is read-only; write operations are disabled."


def read_only_enabled() -> bool:
    """True when the deployment pins the public API to read-only (env PUBLIC_READ_ONLY)."""
    return os.getenv("PUBLIC_READ_ONLY", "").strip().lower() in _TRUTHY


async def read_only_guard(request: Request, call_next):
    """Starlette HTTP middleware: refuse every non-safe method while read-only."""
    if read_only_enabled() and request.method.upper() not in SAFE_METHODS:
        return JSONResponse(
            status_code=405,
            content={"status": "error", "message": READ_ONLY_MESSAGE},
            headers={"Allow": "GET, HEAD, OPTIONS"},
        )
    return await call_next(request)
