"""autonomous-audit - tamper-evident SHA-256 hash-chain audit log for AI trading agents.

Standalone, **stdlib-only**. Extracted from AAAgents' Senate Protocol (the decision-audit log
it uses to document its AI decision-making) into a dependency-free module so it can ship as an
agent-skill / PyPI package.

Each record is appended to an append-only JSONL under a SHA-256 hash chain: every entry
carries ``prev_hash`` (the hash of the previous entry), so an in-place edit, mid-chain
deletion or reorder breaks the chain and is caught by :func:`verify_chain`. (Truncating the
newest records or rewriting from genesis is NOT detected without an external anchor.)

The canonical pre-image hashed for an entry is ``json.dumps(entry, sort_keys=True)`` over the
entry WITHOUT its own ``hash`` field (``prev_hash`` IS included). Because the pre-image is
language-agnostic, a verifier in another language that reproduces it byte-for-byte validates
the same chain (a Python-emulating serializer is required - naive ``JSON.stringify`` differs).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Optional, Tuple, Union

__all__ = [
    "GENESIS_HASH",
    "AuditChainWriter",
    "AuditDiskFullError",
    "AuditIntegrityError",
    "canonical_preimage",
    "entry_hash",
    "verify_chain",
]

GENESIS_HASH = "0" * 64
_DEFAULT_MIN_FREE = 100 * 1024 * 1024  # 100 MB, matches the original Senate disk guard


class AuditDiskFullError(IOError):
    """Raised when free disk space is below the guard threshold. An audit log must
    fail LOUD rather than silently drop tamper-evidence records."""


class AuditIntegrityError(Exception):
    """Raised when the audit log is structurally corrupt (e.g. a non-blank line that is not
    valid JSON) while resuming. The chain fails LOUD rather than silently skipping the
    corruption - a truncated/garbled tail is exactly the tamper case we must surface."""


def canonical_preimage(entry: dict) -> str:
    """The exact byte-string that is hashed for ``entry``: ``json.dumps(sort_keys=True)``
    over the entry WITHOUT its own ``hash`` field. ``prev_hash`` is part of the pre-image.
    """
    body = {k: v for k, v in entry.items() if k != "hash"}
    return json.dumps(body, sort_keys=True)


def entry_hash(entry: dict) -> str:
    """SHA-256 of an entry's canonical pre-image (hex)."""
    return hashlib.sha256(canonical_preimage(entry).encode("utf-8")).hexdigest()


class AuditChainWriter:
    """Append records to a SHA-256 hash-chained, append-only JSONL audit log.

    Resumes the chain from an existing file, so a process restart does NOT fork the
    chain (an improvement over the original in-memory-only ``_last_hash``). I/O is
    synchronous stdlib (blocking): safe to call from any context, but inside an event
    loop run it in a thread (e.g. ``loop.run_in_executor``) so audit I/O never blocks
    the order path.
    """

    def __init__(
        self,
        path: Union[str, Path],
        genesis_hash: str = GENESIS_HASH,
        min_free_bytes: int = _DEFAULT_MIN_FREE,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._min_free = min_free_bytes
        self._last_hash = self._resume_last_hash(genesis_hash)

    def _resume_last_hash(self, genesis: str) -> str:
        if not self.path.exists():
            return genesis
        last = genesis
        with open(self.path, "r", encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line).get("hash", last)
                except json.JSONDecodeError as exc:
                    raise AuditIntegrityError(
                        f"corrupt audit log at {self.path} line {i}: not valid JSON ({exc})"
                    ) from exc
        return last

    @property
    def last_hash(self) -> str:
        return self._last_hash

    def append(self, record: dict) -> str:
        """Chain + persist one record; return its SHA-256 hash.

        Raises :class:`AuditDiskFullError` if free space is below the guard (an audit
        log must never silently drop a record). ``record`` must be JSON-serialisable;
        keep values float-free (use strings) if the same chain is verified in JS.
        """
        free = shutil.disk_usage(self.path.parent).free
        if free < self._min_free:
            raise AuditDiskFullError(
                f"free disk {free} < guard {self._min_free}; refusing to write an audit record"
            )
        entry = dict(record)
        entry.pop("hash", None)
        entry["prev_hash"] = self._last_hash
        digest = entry_hash(entry)  # over {record..., prev_hash}, no hash field yet
        entry["hash"] = digest
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        self._last_hash = digest
        return digest


def verify_chain(
    path: Union[str, Path], genesis_hash: str = GENESIS_HASH
) -> Tuple[bool, Optional[str]]:
    """Full-chain integrity walk. Recomputes every entry's hash from its canonical
    pre-image and checks ``prev_hash`` linkage from the genesis. Returns
    ``(ok, error_message)`` - ``error_message`` is ``None`` iff the chain is intact.
    """
    prev = genesis_hash
    n = 0
    p = Path(path)
    if not p.exists():
        return False, f"file not found: {p}"
    with open(p, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                return False, f"line {i}: invalid JSON ({exc})"
            if entry.get("prev_hash") != prev:
                return False, (
                    f"line {i}: prev_hash break "
                    f"(entry says {str(entry.get('prev_hash'))[:12]}, chain expected {prev[:12]})"
                )
            recomputed = entry_hash(entry)
            if entry.get("hash") != recomputed:
                return False, f"line {i}: hash mismatch - entry was tampered"
            prev = entry["hash"]
    if n == 0:
        return False, "empty log (no records to verify)"
    return True, None
