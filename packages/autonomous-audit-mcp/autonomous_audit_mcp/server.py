"""MCP server wrapping ``autonomous-audit``'s tamper-evident decision log.

Exposes the audit tool as native, agent-callable MCP tools (record / verify / report /
chain-head) over stdio. No new audit logic lives here — it delegates to ``autonomous_audit``.

The tool bodies are thin wrappers around the plain ``_do_*`` functions so the logic is testable
without an MCP client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from autonomous_audit import AuditChainWriter, verify_chain
from autonomous_audit.report import render_html_report
from fastmcp import FastMCP

DISCLOSURE = (
    "For educational and informational purposes only; not investment advice, a recommendation, "
    "or a solicitation. Provides tamper-EVIDENCE (a SHA-256 hash chain that detects in-place "
    "edits, reorders and mid-chain deletions) - NOT tamper-proof durable storage; it does not on "
    "its own satisfy statutory record-keeping and is not model explainability or a regulatory "
    "approval. Read-only with respect to any broker or market - it never places, modifies, or "
    "cancels orders. autonomous-audit is independent third-party open-source software."
)

mcp = FastMCP("autonomous-audit-mcp")


def _scan(log_path: str) -> tuple[int, Optional[str], Optional[str], Optional[str]]:
    """Best-effort read of the JSONL log → (record_count, first_ts, last_ts, head_hash)."""
    p = Path(log_path)
    count = 0
    first: Optional[str] = None
    last: Optional[str] = None
    head: Optional[str] = None
    if not p.exists():
        return 0, None, None, None
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            ts = entry.get("timestamp")
            if ts:
                if first is None:
                    first = ts
                last = ts
            head = entry.get("hash", head)
    return count, first, last, head


def _do_record(log_path: str, decision: dict) -> dict:
    writer = AuditChainWriter(log_path)
    digest = writer.append(dict(decision))
    count, _first, _last, _head = _scan(log_path)
    return {"hash": digest, "record_count": count, "log_path": str(log_path)}


def _do_verify(log_path: str) -> dict:
    ok, err = verify_chain(log_path)
    count, first, last, _head = _scan(log_path)
    return {
        "intact": ok,
        "error": err,
        "record_count": count,
        "first_timestamp": first,
        "last_timestamp": last,
        "disclosure": DISCLOSURE,
    }


def _do_report(log_path: str, output_path: str = "audit_report.html") -> dict:
    Path(output_path).write_text(render_html_report(log_path), encoding="utf-8")
    ok, err = verify_chain(log_path)
    count, first, last, _head = _scan(log_path)
    return {
        "report_path": str(output_path),
        "intact": ok,
        "error": err,
        "record_count": count,
        "time_span": [first, last],
        "disclosure": DISCLOSURE,
    }


def _do_head(log_path: str) -> dict:
    ok, err = verify_chain(log_path)
    count, _first, _last, head = _scan(log_path)
    return {
        "head_hash": head,
        "record_count": count,
        "intact": ok,
        "error": err,
        "disclosure": DISCLOSURE,
    }


@mcp.tool
def record_decision(log_path: str, decision: dict) -> dict:
    """Append ONE trading-agent decision to a tamper-evident SHA-256 hash-chain log.

    ``decision`` is a free-form dict of what the agent decided (e.g. ``symbol``, ``side``,
    ``qty``, ``reason``, ``timestamp``, ``alpaca_order_id``). Keep values string-typed if the log
    will also be verified outside Python. Read-only w.r.t. the broker/market: this only appends to
    the local log — it never places orders. Returns the new entry hash + record count.
    """
    return _do_record(log_path, decision)


@mcp.tool
def verify_log(log_path: str) -> dict:
    """Verify the full integrity of a decision log.

    Detects in-place edits (hash mismatch) and deletions/reorders (prev_hash break). It does NOT
    by itself detect truncation of the newest records or a wholesale rewrite from genesis — use
    ``chain_head`` plus external anchoring for that. Returns intact/error + record count + span.
    """
    return _do_verify(log_path)


@mcp.tool
def export_report(log_path: str, output_path: str = "audit_report.html") -> dict:
    """Render the decision log as a self-contained, human-readable HTML report.

    The report leads with an integrity banner, one row per decision, and the required disclosure;
    print it to PDF from any browser. Writes to ``output_path`` and returns a summary.
    """
    return _do_report(log_path, output_path)


@mcp.tool
def chain_head(log_path: str) -> dict:
    """Return the current chain head hash and record count.

    This is the value to publish/sign EXTERNALLY (e.g. to a WORM store) so that truncation of the
    newest records or a genesis rewrite — which the local chain alone cannot catch — becomes
    detectable by comparing a later log against the anchored head.
    """
    return _do_head(log_path)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
