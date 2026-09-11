# autonomous-audit - Reference

Companion to [SKILL.md](SKILL.md): API, record schema, CLI, and the canonical hash contract.

## Python API

### `AuditChainWriter(path, genesis_hash="0"*64, min_free_bytes=100*1024*1024, schema=None)`
Append records to a SHA-256 hash-chained, append-only JSONL log. On init it **resumes** the
chain from an existing file, so a process restart does not fork the chain.

- `append(record: dict) -> str` - stamp `prev_hash` + `hash`, write one JSON line, return the
  new hash. Raises `AuditDiskFullError` when free disk is below `min_free_bytes`.
- `last_hash: str` - the current chain head.

### `RecordSchema(fields: Dict[str, Type])`
A lightweight, stdlib-only schema validator. If passed to `AuditChainWriter` as `schema`, each record is validated before being appended. Missing required fields or type mismatches will fail loud and raise `ValueError` or `TypeError`.

### `verify_chain(path, genesis_hash="0"*64, expected_anchor=None) -> tuple[bool, str | None]`
Full-chain integrity walk. Recomputes every entry's hash and checks `prev_hash` linkage from
the genesis. Returns `(True, None)` if intact, otherwise `(False, "line N: ...")` — also for a
missing file, invalid JSON, or an empty log (a non-zero exit is not always "tamper").

Detects in-place edits, mid-chain deletions and reorders. If `expected_anchor` (format: `<record_count>:<head_hash>`) is provided, it also detects tail-truncation and rewrites from genesis by verifying the chain length and head hash match the anchor.

### `get_anchor(path, genesis_hash="0"*64) -> str`
Computes the current chain head anchor in the format `<record_count>:<head_hash>`. Note: this function does not verify the chain's integrity; it only reads the head. Run `verify_chain` first to ensure the log is intact before anchoring.

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
- if a uniform schema is detected across all records (e.g. they share the exact same keys), the report will dynamically render those keys as table columns;
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
autonomous-audit anchor PATH            compute and print the current chain anchor (<count>:<hash>)
autonomous-audit verify PATH            full-chain integrity check (exit 0 intact / 1 broken)
  --against-anchor ANCHOR               verify against a prior anchor to detect truncation
autonomous-audit report PATH [-o OUT]   render an HTML decision report (stdout when no -o)
autonomous-audit demo [DIR]             write + verify + report a sample chain
```

## License

Apache-2.0.
