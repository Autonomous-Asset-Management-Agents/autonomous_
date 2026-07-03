# autonomous-audit - Reference

Companion to [SKILL.md](SKILL.md): API, record schema, CLI, and the canonical hash contract.

## Python API

### `AuditChainWriter(path, genesis_hash="0"*64, min_free_bytes=100*1024*1024)`
Append records to a SHA-256 hash-chained, append-only JSONL log. On init it **resumes** the
chain from an existing file, so a process restart does not fork the chain.

- `append(record: dict) -> str` - stamp `prev_hash` + `hash`, write one JSON line, return the
  new hash. Raises `AuditDiskFullError` when free disk is below `min_free_bytes`.
- `last_hash: str` - the current chain head.

### `verify_chain(path, genesis_hash="0"*64) -> tuple[bool, str | None]`
Full-chain integrity walk. Recomputes every entry's hash and checks `prev_hash` linkage from
the genesis. Returns `(True, None)` if intact, otherwise `(False, "line N: ...")` — also for a
missing file, invalid JSON, or an empty log (a non-zero exit is not always "tampered").

Detects in-place edits, mid-chain deletions and reorders. It does **not** detect truncation of
the newest records or a wholesale rewrite from genesis — the chain has no anchored head or
length commitment; anchor the head externally (e.g. sign/publish it) if you need that.

### `canonical_preimage(entry) -> str` / `entry_hash(entry) -> str`
The parity primitives. `canonical_preimage` is `json.dumps(entry_without_hash, sort_keys=True)`;
`entry_hash` is its SHA-256 hex digest.

## Record schema

A record is any JSON-serialisable dict. The writer adds two fields:

| field | meaning |
|---|---|
| `prev_hash` | hash of the previous entry (genesis `"0"*64` for the first record) |
| `hash` | `SHA-256(canonical_preimage(entry))`, added after hashing |

Recommended decision fields: `timestamp` (ISO 8601), `symbol`, `signal_action`,
`consensus_score` (e.g. a 0–1 agreement score), `gatekeeper_approved` / `gatekeeper_reason`
(an approval/risk-gate outcome), `votes`. These field names are illustrative, not required.
Keep values **string-typed** for cross-language verification.

The report recognises these entry shapes and renders a readable verdict for each:

- a Round-Table decision (`consensus_score` / `votes` present);
- `event_type` in {`live_enablement`, `hitl_execution`, `hitl_policy`, `eula_acceptance`};
- anything else renders as a generic record.

## Canonical hash contract (cross-language parity)

The pre-image hashed for an entry is:

```text
json.dumps(<entry without the "hash" field, prev_hash included>, sort_keys=True)
```

with Python's `json.dumps` defaults: `ensure_ascii=True` (non-ASCII characters are emitted as
their JSON `\u` escape sequences rather than raw UTF-8 bytes - e.g. the euro sign is escaped,
not written literally) and `", "` / `": "` separators. Any other-language verifier must
reproduce this byte-for-byte — a naive `JSON.stringify` does **not** match (`ensure_ascii`,
separators and float representation all differ; a Python-emulating serializer is required).
The test suite pins the contract against a fixed `(pre-image, hash)` vector, so any verifier
that reproduces the pre-image validates the same chain.

## CLI

```text
autonomous-audit verify PATH            full-chain integrity check (exit 0 intact / 1 broken)
autonomous-audit report PATH [-o OUT]   render an HTML decision report (stdout when no -o)
autonomous-audit demo [DIR]             write + verify + report a sample chain
```

## License

Apache-2.0.
