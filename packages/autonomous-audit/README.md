# autonomous-audit

**Tamper-evident audit log for AI trading agents** — a SHA-256 hash chain plus a
human-readable decision report. Give any agent-driven trading workflow (Alpaca or otherwise) a
lightweight **integrity + traceability** record for its decisions — with **zero dependencies**.

Extracted from the [autonomous_](https://github.com/Autonomous-Asset-Management-Agents/autonomous_)
"Senate Protocol" decision-audit log into a standalone, stdlib-only package.

## Why

An AI agent that trades makes decisions you may later need to **review and check were not
altered**. `autonomous-audit` appends every decision to an append-only JSONL file under a SHA-256
hash chain: each record carries the hash of the previous one, so an **in-place edit, mid-chain
deletion or reorder breaks the chain and is detected**. (It does **not** by itself detect
truncation of the newest records or a wholesale rewrite from genesis — anchor the chain head
externally if you need that.) A one-command report turns the machine log into a readable HTML
document.

- **Zero third-party dependencies** — pure Python stdlib (no external supply-chain surface).
- **Language-agnostic pre-image** — the canonical pre-image is `json.dumps(entry, sort_keys=True)`
  (Python defaults: `ensure_ascii=True`, `", "`/`": "` separators); a verifier in another language
  that reproduces it **byte-for-byte** validates the same chain — a Python-emulating serializer is
  required (naive `JSON.stringify` differs). See [reference.md](reference.md) for the exact byte contract.
- **Fail-loud** — refuses to write when disk is low, rather than silently dropping evidence.

## Install / run

```bash
# no install — run straight from PyPI with uv
uvx autonomous-audit demo                       # write + verify + report a sample chain
uvx autonomous-audit verify path/to/audit.jsonl # exit 0 = intact; 1 = problem (see message)
uvx autonomous-audit report audit.jsonl -o report.html

# or install it
pip install autonomous-audit
```

## Use as a library

```python
from autonomous_audit import AuditChainWriter, verify_chain

w = AuditChainWriter("audit_log.jsonl")
w.append({"symbol": "AAPL", "signal_action": "BUY", "consensus_score": "0.71",
          "timestamp": "2026-07-02T09:30:00+00:00"})

ok, err = verify_chain("audit_log.jsonl")   # (True, None) if intact
```

Keep record values **float-free** (use strings) if the same chain will be verified in JavaScript —
float formatting can differ between `json.dumps` and `JSON.stringify`.

**Async & throughput.** `append()` does one blocking, synchronous write plus a disk check per
record (pure stdlib, no async). In an event loop, run it in a thread — e.g.
`await loop.run_in_executor(None, writer.append, record)` — so a fast market's order path is
never blocked by audit I/O. It targets per-decision logging, not high-frequency (hundreds/sec)
streaming.

## Report

`report` / `render_html_report()` produces a self-contained HTML page: an integrity banner, one
readable row per decision (subject, verdict, reason, short hash) and the required disclosure.
Print it to PDF from any browser — no PDF dependency required.

## Scope, limits & regulatory context

This tool gives you **tamper-evidence** and a readable trail of what your agent decided —
**integrity and traceability**. It does **not** judge whether decisions were correct, and it is
**not** investment advice, a regulatory control, or a compliance certification.

- **Tamper-evident, not tamper-proof.** A local JSONL file is inherently mutable; the chain lets
  you *detect* alteration (in-place edits, reorders, mid-chain deletions — not tail-truncation or
  a genesis rewrite), not prevent it. It does **not** on its own satisfy MiFID II statutory
  record-keeping (Art. 16 MiFID II; Art. 72/76 Reg. (EU) 2017/565; RTS 6). For regulated
  retention, pair it with WORM / durable-medium storage and anchor the chain head externally.
- **Not explainability.** Recording *what* was decided is not explaining *why*. Model
  explainability/interpretability (e.g. EU AI Act Art. 13/14; MiFID II algo-governance) is a
  separate obligation this tool does not meet.
- **AI Act orientation.** The EU AI Act's event-logging concept is **Art. 12 (record-keeping)**,
  which binds **high-risk** systems; an AI trading agent is **not** high-risk merely by trading
  (Annex III's finance items are credit scoring and life/health-insurance pricing). Assess your
  own use case.

Chain integrity is not authorization. Keep the raw log as the source of truth.

## License

Apache-2.0.
