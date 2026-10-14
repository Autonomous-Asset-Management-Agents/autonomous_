# core/xai/trading_history.py
# XAI-1 / XAI-T3 (#1332) — Trading-History "Glass Box" domain provider.
#
# Answers "why did the Round Table decide X" STRICTLY from the recorded audit trail —
# ZERO-HALLUCINATION: every rendered value comes from a senate_log entry; nothing is
# invented, rounded-into-a-different-meaning, or defaulted into an affirmative claim. No
# creative LLM touches this path. The OSS read-seam (JsonlSenateLogReader) reads the
# LocalJSONAuditLogger JSONL trail (core/round_table/senate_log.py: oss_audit_logs/
# audit_log_<date>.jsonl, SHA-256 hash chained). Enterprise can inject a DB-backed reader.
#
# Import-light: no torch, no senate_log import (the JSON entry shape is read defensively).
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from core.xai.interfaces import IDomainProvider, ISenateLogReader

logger = logging.getLogger(__name__)

# Conservative ticker extraction: a standalone all-caps token of 2-5 letters, minus a
# stoplist of common words / finance acronyms. A miss only narrows the filter (data stays
# real); a false positive just yields an explicit "no decisions found for <X>".
_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
_STOP = frozenset(
    {
        "AND",
        "THE",
        "WHY",
        "HOW",
        "DID",
        "WAS",
        "ALL",
        "FOR",
        "NOW",
        "WHO",
        "ARE",
        "HAS",
        "OUR",
        "API",
        "AI",
        "ML",
        "PR",
        "CI",
        "US",
        "OK",
        "ID",
        "URL",
        "FAQ",
        "BUY",
        "SELL",
        "HOLD",
        "CEO",
        "CFO",
        "CTO",
        "EPS",
        "GAAP",
        "ETF",
        "IPO",
        "USD",
        "EUR",
        "GBP",
        "YTD",
        "ATH",
        "ESG",
        "SEC",
        "ROI",
        "KPI",
        "NYSE",
    }
)

_GENESIS_HASH = "0" * 64  # LocalJSONAuditLogger chain seed


def extract_symbol(text: str) -> Optional[str]:
    """First plausible ticker (2-5 uppercase letters, not a common word/acronym)."""
    for token in _TICKER_RE.findall(text or ""):
        if token not in _STOP:
            return token
    return None


def _fmt_num(value: Any) -> Optional[str]:
    """Faithful, LOSSLESS string for a number — never rounds (a rounded score could cross a
    BUY/SELL threshold and contradict the recorded signal). None for non-numbers/bools.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return repr(value) if isinstance(value, float) else str(value)


def compute_entry_hash(entry: dict) -> str:
    """Recompute an entry's SHA-256 the way LocalJSONAuditLogger does
    (senate_log.py:235-239): sha256 over json.dumps(entry-without-'hash', sort_keys=True).
    """
    payload = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def entry_integrity(entry: dict) -> bool:
    """True iff the entry carries a hash matching its own content (tamper-evident).
    Per-entry only — cross-file chain linkage resets per process, so isn't asserted here.
    """
    stored = entry.get("hash")
    return isinstance(stored, str) and stored == compute_entry_hash(entry)


def _ts_key(entry: dict) -> datetime:
    """Aware-datetime sort key. Parses ISO (incl. 'Z' and offsets) so ordering is by true
    instant, not text; absent/unparseable timestamps sort oldest (deterministic)."""
    ts = entry.get("timestamp")
    floor = datetime.min.replace(tzinfo=timezone.utc)
    if not isinstance(ts, str) or not ts:
        return floor
    candidate = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return floor
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _render_vote(v: dict) -> str:
    name = v.get("agent_name") or v.get("name") or "?"
    signal = v.get("signal") or "?"
    s = f"{name} {signal}"
    bits = []
    sc = _fmt_num(v.get("score"))
    wt = _fmt_num(v.get("weight"))
    if sc is not None:
        bits.append(f"score {sc}")
    if wt is not None:
        bits.append(f"weight {wt}")
    if bits:
        s += " (" + ", ".join(bits) + ")"
    if v.get("vetoed"):
        s += " [VETOED]"
    reasoning = (v.get("reasoning") or "").strip()
    if reasoning:
        s += f' — "{reasoning}"'
    return s


def render_decision(entry: dict) -> str:
    """Deterministic, zero-hallucination rendering of ONE audit entry."""
    symbol = entry.get("symbol") or "?"
    ts = entry.get("timestamp") or "?"
    action = entry.get("signal_action") or "no recorded action"
    head = f"[{ts}] {symbol} — Round Table decided {action}"
    score = _fmt_num(entry.get("consensus_score"))
    if score is not None:
        head += f" (consensus {score})"
    head += "."

    # Tri-state: NEVER fabricate an affirmative "BLOCKED" from missing/None data.
    approved = entry.get("gatekeeper_approved")
    if approved is True:
        gate = "APPROVED"
    elif approved is False:
        gate = "BLOCKED"
    else:
        gate = "UNKNOWN (not recorded)"
    reason = (entry.get("gatekeeper_reason") or "").strip()
    gate_line = f"Gatekeeper: {gate}" + (f" — {reason}" if reason else "") + "."

    votes = [v for v in (entry.get("votes") or []) if isinstance(v, dict)]
    vetoed = [v for v in votes if v.get("vetoed")]
    others = sorted(
        (v for v in votes if not v.get("vetoed")),
        key=lambda v: v.get("weight") or 0,
        reverse=True,
    )
    # A veto is decisive regardless of weight -> ALWAYS shown; truncation only drops
    # lower-weight non-vetoes, and any omission is disclosed truthfully.
    shown = vetoed + others[: max(0, 3 - len(vetoed))]
    if shown:
        factors = "Top factors (by weight): " + "; ".join(
            _render_vote(v) for v in shown
        )
        omitted = len(votes) - len(shown)
        if omitted > 0:
            factors += f" (+{omitted} more vote(s) not shown)"
    else:
        factors = "No recorded votes."

    return "\n".join([head, gate_line, factors])


def render_answer(entries: list, *, symbol: Optional[str] = None) -> str:
    """Render a list of decisions (or an explicit no-data message)."""
    if not entries:
        suffix = f" for {symbol}" if symbol else ""
        return f"No Senate decisions found{suffix}."
    header = (
        f"Found {len(entries)} Senate decision(s)"
        + (f" for {symbol}" if symbol else "")
        + ":"
    )
    return "\n\n".join([header] + [render_decision(e) for e in entries])


class JsonlSenateLogReader(ISenateLogReader):
    """OSS read-seam: reads the LocalJSONAuditLogger JSONL trail (append-only, hash-chained).

    Robust by design (audit reads must never crash the caller): a missing directory, a
    non-UTF-8 byte, or a malformed line yields fewer rows, never an exception. Bounded: it
    reads date-stamped files newest-first and stops once ``limit`` rows are gathered, so a
    long compliance history is never fully loaded into memory to return a handful of rows.
    """

    def __init__(self, *, log_dir: Optional[str] = None) -> None:
        # ADR-014 env-boundary seam: SENATE_LOG_DIR is read directly here (exactly as the
        # writer core/round_table/senate_log.py reads it), NOT via get_config() — this keeps
        # the OSS audit-trail path in one place and avoids config.oss.py dual-edition drift.
        self._log_dir = log_dir or os.getenv("SENATE_LOG_DIR", "oss_audit_logs")

    async def read_decisions(
        self, *, symbol: Optional[str] = None, limit: int = 20
    ) -> list[dict]:
        return await asyncio.to_thread(self._read_recent, symbol, limit)

    def _read_recent(self, symbol: Optional[str], limit: int) -> list[dict]:
        if limit <= 0:
            return []
        wanted = symbol.upper() if symbol else None
        try:
            names = sorted(os.listdir(self._log_dir), reverse=True)  # newest date first
        except OSError:
            return []
        collected: list[dict] = []
        for name in names:
            if not (name.startswith("audit_log_") and name.endswith(".jsonl")):
                continue
            rows = self._read_file(os.path.join(self._log_dir, name))
            if wanted:
                rows = [r for r in rows if str(r.get("symbol") or "").upper() == wanted]
            collected.extend(rows)
            # Files are date-partitioned and scanned newest-first: once the newer files
            # already yield >= limit rows, older files can only hold older entries -> stop.
            if len(collected) >= limit:
                break
        collected.sort(key=_ts_key, reverse=True)
        return collected[:limit]

    @staticmethod
    def _read_file(path: str) -> list[dict]:
        out: list[dict] = []
        try:
            # errors="replace": a non-UTF-8 byte must not crash the audit reader.
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (ValueError, TypeError):
                        # Skip the line (never crash) but SURFACE it — a malformed line in
                        # a hash-chained audit trail may signal corruption or tampering.
                        logger.warning(
                            "XAI trading-history: skipping malformed audit line in %s",
                            path,
                            exc_info=True,
                        )
                        continue
                    if isinstance(obj, dict):
                        out.append(obj)
        except OSError:
            # A listed file that cannot be read is abnormal (disk/permissions) — surface it.
            logger.exception(
                "XAI trading-history: failed to read audit log file %s", path
            )
            return out
        return out


class TradingHistoryProvider(IDomainProvider):
    """Glass-Box domain handler: extract a symbol, read its decisions, render them.

    Returns a structured payload — ``text`` (the zero-hallucination explanation), the raw
    ``decisions`` rows, ``count``, and ``chain_verified`` (per-entry SHA-256 tamper-evidence)
    so the UI can surface "audit integrity could not be verified" instead of presenting
    possibly-tampered rows as clean."""

    def __init__(self, *, reader: Optional[ISenateLogReader] = None) -> None:
        self._reader = reader or JsonlSenateLogReader()

    async def answer(self, request: Any) -> dict:
        text = getattr(request, "text", "") or ""
        symbol = extract_symbol(text)
        decisions = await self._reader.read_decisions(symbol=symbol, limit=10)
        verified = all(entry_integrity(d) for d in decisions) if decisions else True
        return {
            "text": render_answer(decisions, symbol=symbol),
            "decisions": decisions,
            "count": len(decisions),
            "chain_verified": verified,
        }
