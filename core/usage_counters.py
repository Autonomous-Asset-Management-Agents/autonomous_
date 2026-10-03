"""ADR-OBS-01 / PR F (§6): ANONYMOUS usage analytics (PURE OBSERVATION).

Machine-only, anonymous, LOCAL aggregate counters answering *how* the app is
used — read by the ``usage`` subsystem of ``/engine-diagnostics``. This is the
DSGVO-scoped instrument, so it is deliberately minimal:

  * ANONYMOUS + machine-only. We count operator ACTIONS and API-endpoint hits by
    a FIXED name only: aggregate INTEGER counters keyed by fixed action names and
    by ROUTE TEMPLATE (e.g. "/portfolio-summary") — NEVER the raw path with IDs /
    query strings, NEVER a ``user_id``, order content, symbol, IP, or any PII.
  * BOUNDED. ``bump_api_hit`` only counts KNOWN/registered route templates and
    silently ignores anything else, so the ``api_hits`` cardinality can never be
    inflated by crafted/ID-laden paths (rogue-caller safety).
  * FAIL-SAFE. The api-hit counter runs on EVERY request (hot path). It is DOUBLE-
    guarded (the private ``_record_*`` helper swallows its own error AND the public
    ``bump_*`` entry point guards its call site) so a counter failure can NEVER
    raise into — slow, or alter — a request/response or an operator endpoint.

LOCAL-ONLY: this module adds NO network egress. The opt-in egress of these
anonymous aggregates to a backend is SEPARATE epic work (#1457 / #1458) and is
intentionally NOT wired here.
"""

from __future__ import annotations

from typing import Any, Dict

# --- Operator-action + loop/decision action counters (fixed keys only) --------
# Only these fixed keys are ever admitted (an unknown key is ignored), so the map
# can never grow unbounded. Every value is a plain aggregate integer.
_ALLOWED_USAGE_KEYS = (
    "strategy_swaps",
    "panic_sells",
    "kill_switch_resets",
    "force_cycles",
    "hitl_approvals",
)

_usage_counters: Dict[str, int] = {key: 0 for key in _ALLOWED_USAGE_KEYS}

# --- API-endpoint hit counts, keyed by ROUTE TEMPLATE -------------------------
# Bounded: only route TEMPLATES registered here (a raw path with IDs / an unknown
# route is ignored) are ever inserted, so ``api_hits`` cardinality stays fixed.
# The set is (re)seeded lazily from the FastAPI app's registered routes the first
# time a hit is recorded; ``register_api_routes`` also lets the app seed it
# explicitly at import time. This is the ONLY place raw request paths are gated —
# nothing that carries an ID / query string / PII is ever admitted as a key.
_KNOWN_ROUTES: set[str] = set()
_api_hits: Dict[str, int] = {}


def register_api_routes(route_templates) -> None:
    """Register the set of countable route TEMPLATES (machine names, e.g.
    "/portfolio-summary"). Fail-safe — a bad input can never raise."""
    try:
        for template in route_templates:
            if isinstance(template, str) and template:
                _KNOWN_ROUTES.add(template)
    except Exception:  # noqa: BLE001 — registration must never break import/boot
        pass


def _record_usage(key: str) -> None:
    """Raw usage-counter mutation. Only fixed known keys are admitted (bounded)."""
    if key in _usage_counters:
        _usage_counters[key] = int(_usage_counters.get(key, 0) or 0) + 1


def bump_usage(key: str) -> None:
    """Fail-safe operator-action counter bump (double-guarded, additive).

    Increments the aggregate integer counter ``key`` (one of the fixed action
    names). Swallows EVERY error — a broken counter must never break, slow, or
    alter the endpoint that calls it. An unknown key is silently ignored.
    """
    try:
        _record_usage(key)
    except Exception:  # noqa: BLE001 — observation must never alter control flow
        pass


def _record_api_hit(route_template: str) -> None:
    """Raw api-hit mutation. BOUNDED: only KNOWN/registered route templates are
    counted; a raw path with IDs / an unknown route is ignored."""
    if route_template in _KNOWN_ROUTES:
        _api_hits[route_template] = int(_api_hits.get(route_template, 0) or 0) + 1


def bump_api_hit(route_template: str) -> None:
    """Fail-safe, BOUNDED api-endpoint hit counter (double-guarded).

    Runs on EVERY request (hot path) — pure observation. Counts ONLY a known,
    registered ROUTE TEMPLATE (e.g. "/portfolio-summary"), never the raw path with
    IDs / query strings, so no PII is ever stored and cardinality stays bounded;
    an unknown / ID-laden route is silently IGNORED. Swallows EVERY error so a
    counter failure can never break, slow, or alter a request/response.
    """
    try:
        if not isinstance(route_template, str) or not route_template:
            return
        _record_api_hit(route_template)
    except Exception:  # noqa: BLE001 — a broken counter must never break a request
        pass


def get_usage_counters() -> Dict[str, Any]:
    """Read-only snapshot of the anonymous usage counters (machine-only).

    Fail-safe: a snapshot fault degrades to empty rather than raising out of the
    diagnostics surface.
    """
    try:
        snap: Dict[str, Any] = dict(_usage_counters)
        snap["api_hits"] = dict(_api_hits)
        return snap
    except Exception:  # noqa: BLE001
        return {"api_hits": {}}


def reset_usage_counters() -> None:
    """Test/daily-reset helper — zeroes every anonymous usage counter."""
    for key in _usage_counters:
        _usage_counters[key] = 0
    _api_hits.clear()
