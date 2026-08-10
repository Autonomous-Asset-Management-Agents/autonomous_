---
name: autonomous-decision-audit
description: >
  Record AI trading-agent decisions to a tamper-evident SHA-256 hash-chain audit log,
  verify an existing log's integrity, and export a human-readable decision report. Use
  when the user wants an auditable, tamper-evident record of agent decisions, needs to check an
  audit log was not altered, or must produce a human-readable report of what an agent decided.
  Read-only and offline - it never places orders.
---

# Decision Audit - Tamper-Evident Hash Chain

Use this skill when your AI agent should make its trading decisions **auditable and
tamper-evident**: every decision is appended to an append-only log under a SHA-256 hash
chain, so an in-place edit, mid-chain deletion, or reorder is detectable (truncating the
newest records or rewriting from genesis is not, unless the chain head is anchored
externally). The skill can also verify an existing log and export a readable report.

**You** means the trader, developer, researcher, or compliance owner asking your agent to
record, verify, or report on agent decisions. Your agent should address you directly.

```text
decision record -> hash-chain append -> verify (full chain) -> human-readable report
```

It proves the **integrity** of the decision log - that entries were not altered after the
fact. It does **not** judge whether the decisions were good, nor does it place or manage
trades.

## Required disclosures

Every exported report and summary should make clear:

> **Important disclosure**
> This audit log establishes tamper-evidence (integrity) of recorded decisions via a
> SHA-256 hash chain. It is tamper-evident, not tamper-proof durable storage, and does not on
> its own satisfy statutory record-keeping (e.g. MiFID II). It does not evaluate, endorse, or
> guarantee the quality, legality, or profitability of any decision, and it is not investment
> advice, a recommendation, or a solicitation. Chain integrity is not a regulatory approval, and
> it is not model explainability. Verify independently and retain the raw log as the source of
> truth.

## Prerequisites

No API keys, network access, or broker credentials are required - the tool is offline,
read-only, and depends only on the Python standard library.

Run it straight from PyPI with `uv`, or install it:

```bash
uvx autonomous-audit demo               # write + verify + report a sample chain
pip install autonomous-audit            # or install into the current environment
```

## Core concepts

- **Append-only chain.** Each entry stores `prev_hash` (the previous entry's hash) plus its
  own `hash`. The first entry links to the genesis hash (`"0" * 64`).
- **Canonical pre-image.** An entry's hash is `SHA-256(json.dumps(entry, sort_keys=True))`
  over the entry **without** its own `hash` field (`prev_hash` is included). This exact form
  is what makes a Python-written chain verifiable in other languages.
- **Float-free records.** If the same chain will be verified outside Python (e.g. a
  JavaScript verifier), keep record values as **strings** - float formatting can differ
  between `json.dumps` and `JSON.stringify` and would break verification.

## Required workflow

Pick the task you were asked for.

### A. Record decisions
1. Create (or resume) a writer on the target log file.
2. For each agent decision, append a record dict (symbol, action, score, reason, an ISO
   timestamp, votes, ...).
3. The writer stamps `prev_hash` + `hash` and appends one JSON line. It **fails loud** if
   disk space is low - it never silently drops an audit record.

```python
from autonomous_audit import AuditChainWriter

w = AuditChainWriter("audit_log.jsonl")
w.append({
    "symbol": "AAPL", "signal_action": "BUY", "consensus_score": "0.71",
    "gatekeeper_approved": "true", "timestamp": "2026-07-02T09:30:00+00:00",
})
```

### B. Verify integrity
```bash
uvx autonomous-audit verify audit_log.jsonl    # exit 0 = intact; 1 = problem (tampered/missing/invalid/empty)
```
or

```python
from autonomous_audit import verify_chain

ok, err = verify_chain("audit_log.jsonl")    # (True, None) if intact
```
Report the result plainly: on failure, state the failing line and whether it was a hash
mismatch (an edited entry) or a `prev_hash` break (a deleted / reordered entry).

### C. Export a report
```bash
uvx autonomous-audit report audit_log.jsonl -o report.html
```
The report leads with the integrity banner, then one readable row per decision (subject,
verdict, reason, short hash), and ends with the disclosure. It is self-contained HTML -
print it to PDF from any browser (no PDF dependency).

## In-chat response standard

When you verify or report, lead with:

1. integrity result (intact / broken, and where);
2. number of records;
3. the time span covered;
4. artifact paths (log + report).

Then include the disclosure and any caveats (e.g. records that were not float-free).

## Safety and quality guardrails

Your agent must avoid:

- editing the JSONL log by hand - the log is append-only; hand-edits break the chain;
- treating chain integrity as proof that the **decisions** were correct or compliant;
- placing, modifying, or cancelling any orders - this skill is read-only;
- writing floats into records that will be verified cross-language (use strings);
- suppressing a low-disk failure - a dropped audit record defeats the purpose;
- deleting or truncating the raw log to "fix" a broken chain - investigate instead;
- exporting a report without the required disclosure block;
- claiming regulatory approval - integrity is not authorization.

## Troubleshooting

```text
verify reports "hash mismatch"
  An entry was edited after it was written. Restore the raw log; do not re-hash.

verify reports "prev_hash break"
  An entry was deleted or reordered. The chain is authoritative - find the missing entry.

AuditDiskFullError on append
  Free disk is below the guard (default 100 MB; configurable via
  AuditChainWriter(..., min_free_bytes=...)). Free space; the tool refuses to drop records.

hashes do not match a JavaScript verifier
  A record contained a float. Re-record with string values (canonical parity requires it).
```

## Related files

- [reference.md](reference.md) - API, record schema, CLI reference, canonical pre-image spec.
