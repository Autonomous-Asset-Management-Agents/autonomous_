"""Human-readable decision-report export for an autonomous-audit hash chain (MOD-3a-1 / T3).

Turns the machine JSONL audit log into a self-contained, readable HTML report:
an integrity banner (from :func:`autonomous_audit.verify_chain`), one readable row per decision
(votes / verdict / reason / short hash), and the required disclosure. Stdlib-only - no
templating or PDF dependency; the HTML prints to PDF from any browser.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import List, Union

from . import verify_chain

_COMPLIANCE_NOTE = (
    "Tamper-evident SHA-256 hash chain (append-only, prev_hash-linked): detects in-place "
    "edits, mid-chain deletions and reorders (not tail-truncation or a genesis rewrite). "
    "Integrity and traceability only; it is not a regulatory control or legal advice."
)

# Required disclosure — rendered verbatim in every report footer (SKILL.md guardrail).
_DISCLOSURE = (
    "Important disclosure: this audit log establishes tamper-evidence (integrity) of "
    "recorded decisions via a SHA-256 hash chain. It is tamper-evident, not tamper-proof "
    "durable storage, and does not on its own satisfy statutory record-keeping (e.g. MiFID II). "
    "It does not evaluate, endorse, or guarantee the quality, legality, or profitability of any "
    "decision; it is not investment advice, a recommendation, or a solicitation; and it is not "
    "model explainability. Chain integrity is not a regulatory approval. Verify independently "
    "and retain the raw log as the source of truth."
)


def _entries(path: Union[str, Path]) -> List[dict]:
    out: List[dict] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def summarize_entry(entry: dict) -> dict:
    """Map a heterogeneous audit entry to a readable row: when / kind / subject / verdict / detail.

    Mirrors the console verdict-mapping (``src/console/live/audit.ts``) so the exported report
    reads the same as the in-app audit view.
    """
    when = str(entry.get("timestamp", ""))
    short = str(entry.get("hash", ""))[:12]
    etype = entry.get("event_type")

    if etype == "live_enablement":
        row = (
            "Live-Trading",
            str(entry.get("actor", "")),
            str(entry.get("action", "")),
            str(entry.get("acknowledgment", "")),
        )
    elif etype == "hitl_execution":
        row = (
            "HITL Execution",
            f"{entry.get('action', '')} {entry.get('symbol', '')}".strip(),
            str(entry.get("branch", "")),
            str(entry.get("reason") or f"order_value={entry.get('order_value', '')}"),
        )
    elif etype == "hitl_policy":
        row = (
            "HITL Policy Change",
            str(entry.get("actor", "")),
            "policy updated",
            f"{entry.get('old_policy', '')} -> {entry.get('new_policy', '')}",
        )
    elif etype == "eula_acceptance":
        row = (
            "EULA Acceptance",
            str(entry.get("actor", "")),
            "accepted",
            f"{entry.get('document', '')} v{entry.get('version', '')}",
        )
    elif "consensus_score" in entry or "votes" in entry:
        gate = "approved" if entry.get("gatekeeper_approved") else "blocked"
        votes = entry.get("votes") or []
        row = (
            "Round-Table Decision",
            str(entry.get("symbol", "")),
            f"{entry.get('signal_action') or 'NONE'} / gatekeeper {gate}",
            f"consensus {entry.get('consensus_score', '')}, {len(votes)} votes"
            + (
                f"; {entry.get('gatekeeper_reason')}"
                if entry.get("gatekeeper_reason")
                else ""
            ),
        )
    else:
        meta = {"prev_hash", "hash"}
        body = {k: v for k, v in entry.items() if k not in meta}
        row = ("Record", "", "", json.dumps(body, ensure_ascii=False))

    kind, subject, verdict, detail = row
    return {
        "when": when,
        "kind": kind,
        "subject": subject,
        "verdict": verdict,
        "detail": detail,
        "hash": short,
    }


def render_html_report(
    path: Union[str, Path], title: str = "autonomous_ Decision Audit"
) -> str:
    """Render the audit log at ``path`` as a self-contained HTML report string."""
    entries = _entries(path)
    ok, err = verify_chain(path)
    banner = (
        '<div class="ok">&#10003; Chain intact &mdash; '
        + str(len(entries))
        + " records verified</div>"
        if ok
        else '<div class="bad">&#10007; Chain BROKEN &mdash; '
        + html.escape(str(err))
        + "</div>"
    )
    rows = []
    for e in entries:
        s = summarize_entry(e)
        rows.append(
            "<tr><td>{when}</td><td>{kind}</td><td>{subject}</td><td>{verdict}</td>"
            "<td>{detail}</td><td class=hash>{hash}</td></tr>".format(
                when=html.escape(s["when"]),
                kind=html.escape(s["kind"]),
                subject=html.escape(s["subject"]),
                verdict=html.escape(s["verdict"]),
                detail=html.escape(s["detail"]),
                hash=html.escape(s["hash"]),
            )
        )
    body = "\n".join(rows) or "<tr><td colspan=6>(no records)</td></tr>"
    return _TEMPLATE.format(
        title=html.escape(title),
        banner=banner,
        rows=body,
        note=html.escape(_COMPLIANCE_NOTE),
        disclosure=html.escape(_DISCLOSURE),
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:2rem;color:#1a1a1a}}
 h1{{font-size:1.3rem}}
 .ok{{background:#e6f4ea;color:#137333;padding:.5rem .8rem;border-radius:6px;font-weight:600}}
 .bad{{background:#fce8e6;color:#c5221f;padding:.5rem .8rem;border-radius:6px;font-weight:600}}
 table{{border-collapse:collapse;width:100%;margin-top:1rem;font-size:.9rem}}
 th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top}}
 th{{background:#f5f5f5}}
 td.hash{{font-family:ui-monospace,Consolas,monospace;color:#666}}
 footer{{margin-top:1.5rem;font-size:.8rem;color:#666}}
 footer .disclosure{{margin-top:.6rem;font-style:italic}}
</style></head><body>
<h1>{title}</h1>
{banner}
<table><thead><tr><th>When (UTC)</th><th>Kind</th><th>Subject</th><th>Verdict</th>
<th>Detail</th><th>Hash</th></tr></thead>
<tbody>
{rows}
</tbody></table>
<footer><p class=note>{note}</p><p class=disclosure>{disclosure}</p></footer>
</body></html>
"""
