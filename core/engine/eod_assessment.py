"""core/engine/eod_assessment.py — EOD sector-constraint proposal + magic link (#2652, Epic #2655).

Weg 1 of the HITL-Wege: at the end of the trading day the engine derives a RELATIVE sector-cap
proposal from the current portfolio state, signs an approval token (local Ed25519, 12h TTL) and
delivers a notification through the operator's own private channels (the existing
``core.daily_report`` delivery — SMTP/webhooks, nothing new, nothing hosted). The link targets
the LOCAL console (BaFin decentralization constraint: no hosted approval page, no central auth);
the actual mutation happens only via the four-eyes ``POST /api/admin/portfolio-constraints``
path (#2653), which verifies this token offline.

Honesty / safety rules (Archon audit 2026-08-04):
- The proposal payload contains ONLY relative caps (0 < cap <= 1) — never absolute $ amounts,
  so a proposal approved hours later cannot mis-trim against a moved portfolio.
- The token embeds ``exp`` (default 12h); #2653 rejects expired tokens with 400.
- No sector data this cycle (the portfolio_context sector brick is pending) ⇒ WARN + skip,
  never an empty-but-signed proposal.
- Everything is gated behind ``EOD_CONSTRAINT_ASSESSMENT_ENABLED`` (default **False**) —
  dormant by default, BORA-mirrored in both config editions.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.governance.portfolio_constraints import save_pending_proposal

logger = logging.getLogger(__name__)

_KEY_FILENAME = "eod_approval_key.bin"  # raw 32-byte Ed25519 seed, local-only
_HARD_SECTOR_CAP = (
    0.30  # never propose past the gatekeeper's static concentration limit
)
_DEFAULT_TTL_HOURS = 12


# ── proposal ─────────────────────────────────────────────────────────────────


def build_constraint_proposal(
    sector_weights: Dict[str, float], hard_cap: float = _HARD_SECTOR_CAP
) -> Dict[str, float]:
    """Deterministic, explainable v1 heuristic: give each sector 5pp headroom over its current
    weight, rounded UP to the next 5%-step, capped at the 30% gatekeeper concentration limit.
    RELATIVE caps only — the operator reviews '%', never stale absolute dollars."""
    proposal: Dict[str, float] = {}
    for sector, weight in sector_weights.items():
        try:
            w = float(weight)
        except (TypeError, ValueError):
            continue
        if not 0 < w <= 1:
            continue
        # epsilon-tolerant ceil: 0.10+0.05 is 0.15000000000000002 in floats — without the
        # epsilon that would round UP an exact 5%-step to the next one (0.15 -> 0.20).
        cap = min(hard_cap, math.ceil((w + 0.05) * 20 - 1e-9) / 20)
        proposal[str(sector)] = round(cap, 4)
    return proposal


# ── local Ed25519 approval token ─────────────────────────────────────────────


def _key_path() -> str:
    base = os.environ.get("AAA_USER_DATA_DIR", "").strip() or os.getcwd()
    return os.path.join(base, _KEY_FILENAME)


def _load_or_create_key() -> Ed25519PrivateKey:
    """Local keypair, generated once on this device — mints AND verifies locally (no shared
    secret, no central auth server; consistent with the entitlement Ed25519 stance)."""
    path = _key_path()
    try:
        with open(path, "rb") as fh:
            seed = fh.read()
        if len(seed) == 32:
            return Ed25519PrivateKey.from_private_bytes(seed)
    except OSError:
        pass
    key = Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization

    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    with open(path, "wb") as fh:
        fh.write(seed)
    return key


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint_approval_token(
    caps: Dict[str, float], ttl_hours: float = _DEFAULT_TTL_HOURS
) -> str:
    """``<b64url(payload)>.<b64url(sig)>`` — payload {caps, iat, exp, nonce}, Ed25519-signed."""
    now = datetime.now(timezone.utc).timestamp()
    payload = {
        "caps": {str(s): float(c) for s, c in caps.items()},
        "iat": now,
        "exp": now + ttl_hours * 3600,
        "nonce": secrets.token_hex(8),
    }
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    sig = _load_or_create_key().sign(raw)
    return f"{_b64e(raw)}.{_b64e(sig)}"


def verify_approval_token(token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(payload, None) on success; (None, reason) with reason in
    {"invalid", "expired", "non_relative"} — #2653 maps expired/non_relative to HTTP 400.
    """
    try:
        payload_b64, sig_b64 = token.split(".")
        raw = _b64d(payload_b64)
        pub: Ed25519PublicKey = _load_or_create_key().public_key()
        pub.verify(_b64d(sig_b64), raw)
        payload = json.loads(raw)
    except (
        Exception
    ):  # noqa: BLE001 — any structural/signature failure is just "invalid"
        return None, "invalid"
    if float(payload.get("exp", 0)) < datetime.now(timezone.utc).timestamp():
        return None, "expired"
    caps = payload.get("caps")
    if not isinstance(caps, dict) or not caps:
        return None, "invalid"
    for cap in caps.values():
        if (
            isinstance(cap, bool)
            or not isinstance(cap, (int, float))
            or not 0 < cap <= 1
        ):
            return (
                None,
                "non_relative",
            )  # absolute $ / junk — Archon stale-proposal guard
    return payload, None


# ── EOD dispatch (flag-gated, dormant by default) ────────────────────────────

_last_dispatch_day: Optional[str] = None


def maybe_run_eod_assessment(sector_weights: Dict[str, float]) -> Dict[str, Any]:
    """The once-per-day EOD step. Integration is one line at the market-close transition
    (closed_market_mode arming point); dormant while EOD_CONSTRAINT_ASSESSMENT_ENABLED=False.

    Returns a small status dict (ran/skipped + reason) so the caller can log honestly.
    """
    global _last_dispatch_day
    from config import get_config

    cfg = get_config()
    if not bool(getattr(cfg, "EOD_CONSTRAINT_ASSESSMENT_ENABLED", False)):
        return {"ran": False, "reason": "disabled"}
    today = datetime.now(timezone.utc).date().isoformat()
    if _last_dispatch_day == today:
        return {"ran": False, "reason": "already_ran_today"}
    if not sector_weights:
        logger.warning(
            "eod_assessment: no sector weights this cycle (sector-source brick pending) - "
            "skipping the EOD constraint proposal"
        )
        return {"ran": False, "reason": "no_sector_data"}

    proposal = build_constraint_proposal(sector_weights)
    if not proposal:
        return {"ran": False, "reason": "empty_proposal"}
    ttl = float(getattr(cfg, "EOD_CONSTRAINT_TOKEN_TTL_HOURS", _DEFAULT_TTL_HOURS))
    token = mint_approval_token(proposal, ttl_hours=ttl)

    # Stage the proposal for the console's read-only review (#2666 GETs it) + the four-eyes
    # approve (#2653). The token hash binds pending<->token without storing the token itself.
    save_pending_proposal(
        {
            "caps": proposal,
            "actor": "eod_assessment",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl_hours": ttl,
            "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        }
    )

    # Deliver over the operator's own private channels (existing daily_report infra). The link
    # targets the LOCAL console route — nothing hosted (BaFin decentralization constraint).
    from core.daily_report import dispatch_report
    from core.daily_report import load_config as load_report_config

    lines = [
        "AAAgents — tomorrow's portfolio plan awaits your approval.",
        "",
        "Proposed sector caps (relative, from today's closing portfolio):",
    ]
    lines += [f"  - {s}: {c:.0%}" for s, c in sorted(proposal.items())]
    lines += [
        "",
        f"Review & approve in your desktop console (Settings -> Portfolio constraints). "
        f"The approval link/token expires in {ttl:.0f}h.",
        f"Token: {token}",
    ]
    report_cfg = load_report_config()
    results = (
        dispatch_report("\n".join(lines), report_cfg)
        if getattr(report_cfg, "any_channel", False)
        else {}
    )
    _last_dispatch_day = today
    logger.info(
        "eod_assessment: proposal staged for %d sectors; delivery=%s",
        len(proposal),
        results or "no channel enabled",
    )
    return {"ran": True, "sectors": len(proposal), "delivery": results}
