# core/xai/read_boundary.py
# XAI-1 / XAI-T8 (#1337) — Unified Read-Only-Auth-Boundary.
#
# The single seam EVERY XAI read goes through (round-table/senate decisions, FAQ,
# SpecialistReports, Explainability, + fixed external sources). Three guarantees, all
# FAIL-CLOSED — every error/ambiguity becomes DENY, never crash, never allow:
#   1. READ-ONLY  — only an allow-listed READ verb passes; a write/mutation (or anything
#                   unknown) is denied. Data-layer defense-in-depth to the Command-Airlock
#                   (T7), which gates at the command layer.
#   2. RLS SCOPE  — rows are filtered to the request's session_user_id; a row tagged with a
#                   DIFFERENT user is dropped (no data-bleeding). OSS is single-user
#                   (untagged rows pass via allow_untagged); Enterprise sets allow_untagged
#                   =False so untagged rows are dropped too.
#   3. ALLOW-LIST — external sources are read ONLY from a fixed allow-list
#                   (XAI_EXTERNAL_ALLOWLIST). The host is validated (http/https only, NO
#                   userinfo, port/fragment stripped) and `guard_source` RETURNS the
#                   canonical URL the caller must fetch — so this validator and the HTTP
#                   client cannot disagree on the target. Empty allow-list => deny all.
#
# Import-light: stdlib only.
from __future__ import annotations

import os
import re
from typing import Iterable, Optional, Tuple
from urllib.parse import urlparse

_TRUTHY = {"1", "true", "yes", "on"}
_ALLOWLIST_ENV = "XAI_EXTERNAL_ALLOWLIST"
_ALLOWED_SCHEMES = frozenset({"https", "http"})


class ReadAccessDenied(RuntimeError):
    """Raised (fail-closed) when an operation violates the read-only-auth boundary:
    a write/mutation, or a non-allow-listed/invalid external source."""


def is_read_boundary_enabled() -> bool:
    """Flag-gated: the boundary is ENFORCED only when XAI_RLS_BOUNDARY is truthy (OFF by
    default). Mirrors agent_core.is_agent_core_enabled(); the integration reads this to
    decide whether to route reads through the boundary during rollout."""
    return os.getenv("XAI_RLS_BOUNDARY", "").strip().lower() in _TRUTHY


# --------------------------------------------------------------------------
# 1) READ-ONLY — allow-list of read verbs (fail-closed)
# --------------------------------------------------------------------------
# A fail-closed boundary uses an ALLOW-LIST of read verbs, NOT a deny-list of writes: an
# unknown/ambiguous operation is denied. The operation name is normalized to its first
# token (camelCase + non-alphanumeric split, lowercased); only a known read verb passes.
_READ_VERBS = frozenset(
    {
        "get",
        "read",
        "list",
        "search",
        "fetch",
        "find",
        "lookup",
        "count",
        "describe",
        "explain",
        "has",
        "is",
        "view",
        "load",
    }
)


def _first_token(operation: str) -> str:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", operation)  # split camelCase
    tokens = [t for t in re.split(r"[^A-Za-z0-9]+", spaced) if t]
    return tokens[0].lower() if tokens else ""


def is_read_only(operation: object) -> bool:
    """True iff ``operation`` is a known READ verb (allow-list, fail-closed). A non-str,
    empty, unknown, or mutation verb -> False (denied)."""
    if not isinstance(operation, str):
        return False
    return _first_token(operation) in _READ_VERBS


def guard_read_only(operation: object) -> None:
    """Raise ReadAccessDenied unless ``operation`` is an allow-listed read verb."""
    if not is_read_only(operation):
        raise ReadAccessDenied(
            f"operation {operation!r} is not an allow-listed read; the boundary is read-only"
        )


# --------------------------------------------------------------------------
# 2) RLS SCOPE
# --------------------------------------------------------------------------
def scope_rows(
    rows: Optional[Iterable[dict]],
    session_user_id: Optional[object],
    *,
    user_field: str = "session_user_id",
    allow_untagged: bool = True,
) -> list:
    """Filter rows to those owned by ``session_user_id``.

    * ``session_user_id is None`` -> no scoping requested (system/local) -> pass all dicts.
      (Note: a *falsy-but-present* id like ``0``/``""`` still scopes — only ``None`` opts out.)
    * otherwise -> keep a row iff its ``user_field`` equals the id, OR the row is untagged
      (``user_field`` absent/None) AND ``allow_untagged`` (OSS single-user). Enterprise sets
      ``allow_untagged=False`` so an untagged/NULL-user row never bleeds to a tenant.
    Non-dict rows are dropped.
    """
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if session_user_id is None:
            out.append(r)
            continue
        owner = r.get(user_field)
        if owner == session_user_id or (owner is None and allow_untagged):
            out.append(r)
    return out


# --------------------------------------------------------------------------
# 3) EXTERNAL-SOURCE ALLOW-LIST (host-validated, canonical-URL returning)
# --------------------------------------------------------------------------
def _norm_allowlist(allowlist: Iterable[str]) -> frozenset:
    return frozenset(
        h.strip().lower().rstrip(".")
        for h in allowlist
        if isinstance(h, str) and h.strip()
    )


def load_allowlist() -> frozenset:
    """The fixed external-source allow-list (normalized hosts) from XAI_EXTERNAL_ALLOWLIST
    (comma-separated). Empty/unset => empty set => ALL external reads denied (fail-closed).
    """
    return _norm_allowlist(os.getenv(_ALLOWLIST_ENV, "").split(","))


def _parse_source(source: object) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(host, canonical_url)`` for a source, or ``(None, None)`` if it is invalid:
    non-str, empty, unparseable, carries userinfo (anti `user@host` confusion), or a
    non-http(s) scheme. NEVER raises — every error becomes deny (e.g. a malformed IPv6 URL,
    which urlparse raises on). The canonical URL has userinfo + fragment stripped and the
    validated host, so this validator and the downstream HTTP client cannot disagree."""
    try:
        if not isinstance(source, str):
            return None, None
        s = source.strip()
        if not s:
            return None, None
        parsed = urlparse(s if "://" in s else "https://" + s)
        scheme = (parsed.scheme or "").lower()
        if scheme not in _ALLOWED_SCHEMES:
            return None, None
        if parsed.username is not None or parsed.password is not None:
            return None, None  # no credentials in the URL
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        if not host:
            return None, None
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        canon = f"{scheme}://{netloc}{parsed.path or ''}"
        if parsed.query:
            canon += f"?{parsed.query}"
        return host, canon
    except (
        Exception
    ):  # noqa: BLE001 — fail-closed: any parse error denies, never crashes
        return None, None


def source_host(source: object) -> Optional[str]:
    """The validated host of a source (lowercased; port/userinfo/scheme/trailing-dot/
    fragment stripped), or None if invalid/denied. Never raises."""
    return _parse_source(source)[0]


def is_source_allowed(source: object, allowlist: Iterable[str]) -> bool:
    """True iff the source's validated host is EXACTLY on the allow-list (no subdomain
    wildcard). Fail-closed: invalid source, userinfo, non-http(s) scheme, or empty
    allow-list -> denied."""
    host = source_host(source)
    return bool(host) and host in _norm_allowlist(allowlist)


# --------------------------------------------------------------------------
# The per-request boundary
# --------------------------------------------------------------------------
class XaiReadBoundary:
    """The single read-only-auth boundary for all XAI reads. Constructed per request (it
    carries ``session_user_id``); every guard is fail-closed. The data seams (T3..T6) and
    any external fetch route through this instance."""

    def __init__(
        self,
        *,
        session_user_id: Optional[object] = None,
        allowlist: Optional[Iterable[str]] = None,
        allow_untagged: bool = True,
    ) -> None:
        self.session_user_id = session_user_id
        self.allow_untagged = allow_untagged
        self._allowlist = (
            _norm_allowlist(allowlist) if allowlist is not None else load_allowlist()
        )

    def guard_read_only(self, operation: object) -> None:
        guard_read_only(operation)

    def source_allowed(self, source: object) -> bool:
        host = source_host(source)
        return bool(host) and host in self._allowlist

    def guard_source(self, source: object) -> str:
        """Validate ``source`` against the allow-list and RETURN the canonical, safe URL the
        caller MUST fetch (scheme + host validated, userinfo/fragment stripped). Raises
        ReadAccessDenied if the host is not allow-listed or the source is invalid."""
        host, canon = _parse_source(source)
        if not host or host not in self._allowlist:
            raise ReadAccessDenied(
                f"external source {source!r} is not on the XAI allow-list; denied"
            )
        return canon

    def scope(self, rows: Optional[Iterable[dict]]) -> list:
        """RLS-scope rows to this request's session_user_id (+ this boundary's
        allow_untagged policy)."""
        return scope_rows(
            rows, self.session_user_id, allow_untagged=self.allow_untagged
        )
