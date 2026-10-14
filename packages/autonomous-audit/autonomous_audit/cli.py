"""CLI for autonomous-audit (MOD-3a-1 / T4). uvx-runnable: ``uvx autonomous-audit verify log.jsonl``.

Subcommands:
  verify PATH            full-chain integrity check (exit 0 = intact, 1 = broken)
  report PATH [-o OUT]   render a human-readable HTML decision report
  demo [DIR]             write a sample chain, verify it, and render a report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import AuditChainWriter, get_anchor, verify_chain
from .report import render_html_report


def _cmd_verify(args: argparse.Namespace) -> int:
    ok, err = verify_chain(args.path, expected_anchor=args.against_anchor)
    if ok:
        if args.against_anchor:
            print(
                f"OK  chain intact and matches anchor {args.against_anchor}: {args.path}"
            )
        else:
            print(f"OK  chain intact: {args.path}")
        return 0
    print(f"FAIL  {err}", file=sys.stderr)
    return 1


def _cmd_anchor(args: argparse.Namespace) -> int:
    ok, err = verify_chain(args.path)
    if not ok:
        print(f"FAIL  Cannot anchor corrupt log: {err}", file=sys.stderr)
        return 1
    anchor = get_anchor(args.path)
    print(anchor)
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    cols = args.columns.split(",") if args.columns else None
    out_html = render_html_report(args.path, schema_columns=cols)
    if args.output in (None, "-"):
        sys.stdout.write(out_html)
    else:
        Path(args.output).write_text(out_html, encoding="utf-8")
        print(f"wrote report: {args.output}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    out_dir = Path(args.dir or "autonomous_audit_demo")
    out_dir.mkdir(parents=True, exist_ok=True)
    log = out_dir / "audit_log_demo.jsonl"
    if log.exists():
        log.unlink()
    w = AuditChainWriter(log)
    w.append(
        {
            "session_id": "demo-1",
            "symbol": "AAPL",
            "timestamp": "2026-07-02T09:30:00+00:00",
            "consensus_score": "0.71",
            "gatekeeper_approved": "true",
            "gatekeeper_reason": "within risk limits",
            "signal_action": "BUY",
            "votes": [{"agent": "DrawdownGuard"}, {"agent": "VIXAwareRisk"}],
        }
    )
    w.append(
        {
            "event_type": "hitl_execution",
            "timestamp": "2026-07-02T09:31:00+00:00",
            "symbol": "AAPL",
            "action": "buy",
            "branch": "approved",
            "policy_hash": "abc123",
            "order_value": "4200.0",
        }
    )
    w.append(
        {
            "event_type": "live_enablement",
            "timestamp": "2026-07-02T09:32:00+00:00",
            "actor": "operator",
            "action": "enable",
            "acknowledgment": "I accept live trading on my own account",
            "nonce": "demo-nonce",
        }
    )
    ok, err = verify_chain(log)
    report = out_dir / "audit_report_demo.html"
    report.write_text(
        render_html_report(log, title="autonomous_ Decision Audit (demo)"),
        encoding="utf-8",
    )
    print(f"log:    {log}")
    print(f"report: {report}")
    print(f"verify: {'OK (intact)' if ok else 'FAIL ' + str(err)}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="autonomous-audit",
        description="Tamper-evident audit log for AI trading agents.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("verify", help="full-chain integrity check")
    pv.add_argument("path")
    pv.add_argument(
        "--against-anchor",
        default=None,
        help="verify against a prior anchor (e.g. 42:abc123hash) to detect truncation",
    )
    pv.set_defaults(func=_cmd_verify)

    pa = sub.add_parser("anchor", help="compute and print the current chain anchor")
    pa.add_argument("path")
    pa.set_defaults(func=_cmd_anchor)

    pr = sub.add_parser("report", help="render an HTML decision report")
    pr.add_argument("path")
    pr.add_argument(
        "-o", "--output", default=None, help="output HTML file (default: stdout)"
    )
    pr.add_argument(
        "--columns",
        default=None,
        help="comma-separated list of typed fields to render as columns (or 'auto' for auto-inference)",
    )
    pr.set_defaults(func=_cmd_report)

    pd = sub.add_parser("demo", help="write + verify + report a sample chain")
    pd.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="output directory (default: ./autonomous_audit_demo)",
    )
    pd.set_defaults(func=_cmd_demo)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
