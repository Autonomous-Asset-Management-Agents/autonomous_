"""Minimal end-to-end example: write a hash-chained audit log, verify it, export a report.

Run:  python examples/quickstart.py   (or:  uvx autonomous-audit demo)
"""

from pathlib import Path

from autonomous_audit import AuditChainWriter, verify_chain
from autonomous_audit.report import render_html_report

out = Path("quickstart_audit.jsonl")
if out.exists():
    out.unlink()

writer = AuditChainWriter(out)
writer.append(
    {
        "symbol": "AAPL",
        "signal_action": "BUY",
        "consensus_score": "0.71",
        "gatekeeper_approved": "true",
        "gatekeeper_reason": "within risk limits",
        "votes": [{"agent": "DrawdownGuard"}, {"agent": "VIXAwareRisk"}],
        "timestamp": "2026-07-02T09:30:00+00:00",
    }
)
writer.append(
    {
        "event_type": "live_enablement",
        "actor": "operator",
        "action": "enable",
        "acknowledgment": "I accept live trading on my own account",
        "nonce": "example-1",
        "timestamp": "2026-07-02T09:31:00+00:00",
    }
)

ok, err = verify_chain(out)
print("verify:", "OK (intact)" if ok else f"FAIL {err}")

Path("quickstart_report.html").write_text(render_html_report(out), encoding="utf-8")
print("report: quickstart_report.html")
