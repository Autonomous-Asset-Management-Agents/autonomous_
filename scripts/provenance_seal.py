#!/usr/bin/env python3
# scripts/provenance_seal.py
# Epic #2912 · Inc 1 (#2913): deterministic Merkle manifest of a clean checkout + Ed25519 sign.
#
# Seals a source tree into a byte-stable Merkle root so an independent timestamp (OpenTimestamps,
# eIDAS QTS — later increments) anchors the WHOLE tree at a date. The manifest carries ONLY
# per-file SHA-256 hashes + repo-relative paths — never file bodies — so a private/headless tree
# can be sealed and its root timestamped WITHOUT disclosing the code (selective disclosure via
# Merkle proofs). Signing reuses the single existing WORM key
# (ai_trading_bot/core/round_table/worm_signing.py) — no new key material.
#
# ── Canonical spec (SPEC v1 — pin before anchoring; changing it changes every root) ──
#   file_hex   = sha256(file_bytes)
#   leaf_hex   = sha256(b"prov-leaf-v1\0" + relpath_utf8 + b"\0" + file_hex_utf8)   (binds path)
#   node_hex   = sha256(b"prov-node-v1\0" + bytes.fromhex(left) + bytes.fromhex(right))
#   tree       = RFC-6962-style; a lone node at a level is PROMOTED unchanged (no Bitcoin
#                duplicate-leaf ambiguity). Single leaf ⇒ root == that leaf.
#   order      = files sorted ascending by repo-relative POSIX path (bytes).
#   excluded   = VCS / build / cache / vendored-dep dirs + compiled artefacts (see below).
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Callable, List, Optional, Tuple

_LEAF_PREFIX = b"prov-leaf-v1\x00"
_NODE_PREFIX = b"prov-node-v1\x00"
_EMPTY_ROOT = hashlib.sha256(b"prov-empty-v1").hexdigest()

# Not part of the developed tree: version control, dependency vendoring, build output, caches.
_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".eggs",
}
_EXCLUDE_DIR_PREFIXES = ("venv", ".venv")
_EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pyd")

Proof = List[Tuple[str, str]]  # [(side 'L'|'R', sibling_hex), ...]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_excluded(rel: PurePosixPath) -> bool:
    for part in rel.parts[:-1]:  # directory components only
        if part in _EXCLUDE_DIRS or part.startswith(_EXCLUDE_DIR_PREFIXES):
            return True
    return rel.name.endswith(_EXCLUDE_SUFFIXES)


def _file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: os.PathLike | str) -> List[dict]:
    """Walk ``root`` and return a path-sorted list of ``{"path", "sha256"}`` — no file contents.

    Symlinks are not followed (only regular files are hashed). Excluded dirs/artefacts are skipped.
    """
    root_p = Path(root)
    entries: List[dict] = []
    for p in root_p.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        rel = PurePosixPath(p.relative_to(root_p).as_posix())
        if _is_excluded(rel):
            continue
        entries.append({"path": str(rel), "sha256": _file_digest(p)})
    entries.sort(key=lambda e: e["path"])
    return entries


def _leaf_digest(path: str, file_hex: str) -> str:
    return _sha256_hex(
        _LEAF_PREFIX + path.encode("utf-8") + b"\x00" + file_hex.encode("ascii")
    )


def manifest_leaves(manifest: List[dict]) -> List[str]:
    return [_leaf_digest(e["path"], e["sha256"]) for e in manifest]


def _node(left_hex: str, right_hex: str) -> str:
    return _sha256_hex(
        _NODE_PREFIX + bytes.fromhex(left_hex) + bytes.fromhex(right_hex)
    )


def merkle_root(leaves: List[str]) -> str:
    if not leaves:
        return _EMPTY_ROOT
    level = list(leaves)
    while len(level) > 1:
        nxt: List[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])  # lone node promoted unchanged
        level = nxt
    return level[0]


def merkle_proof(leaves: List[str], index: int) -> Proof:
    """Inclusion proof for ``leaves[index]`` — the siblings needed to recompute the root."""
    if not (0 <= index < len(leaves)):
        raise IndexError(index)
    proof: Proof = []
    level = list(leaves)
    idx = index
    while len(level) > 1:
        nxt: List[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                if i == idx:
                    proof.append(("R", level[i + 1]))
                elif i + 1 == idx:
                    proof.append(("L", level[i]))
                nxt.append(_node(level[i], level[i + 1]))
            else:
                nxt.append(level[i])  # promoted; no sibling for a lone node
        idx //= 2
        level = nxt
    return proof


def verify_proof(leaf_hex: str, proof: Proof, root_hex: str) -> bool:
    cur = leaf_hex
    for side, sibling in proof:
        cur = _node(sibling, cur) if side == "L" else _node(cur, sibling)
    return cur == root_hex


def flat_anchor(manifest: List[dict]) -> str:
    """Option-B cross-check: sha256 of the canonical flat ``"<sha256>  <path>\\n"`` manifest.

    Bundled over the PUBLIC manifest so a third party can recompute it with ``sort | sha256sum``
    — low-effort verification where disclosure is not a concern (see plan Dual Design).
    """
    text = "".join(f'{e["sha256"]}  {e["path"]}\n' for e in manifest)
    return _sha256_hex(text.encode("utf-8"))


def sign_root(root_hex: str) -> Optional[str]:
    """Detached Ed25519 signature (base64) over the Merkle root, via the existing WORM key.

    Returns None (fail-safe) when no ``AAA_WORM_SIGNING_KEY`` is provisioned — the seal is written
    UNSIGNED rather than blocking, mirroring the WORM record contract (CLAUDE.md §5.6).
    """
    from core.round_table.worm_signing import sign_worm_hash

    return sign_worm_hash(root_hex)


def seal_tree(root: os.PathLike | str) -> dict:
    """Convenience: build the manifest + roots for a tree (no I/O side effects)."""
    manifest = build_manifest(root)
    leaves = manifest_leaves(manifest)
    root_hex = merkle_root(leaves)
    return {
        "spec": "prov-v1",
        "file_count": len(manifest),
        "merkle_root": root_hex,
        "flat_anchor": flat_anchor(manifest),
        "manifest": manifest,
    }


def verify_seal(seal: dict, pub_b64: Optional[str] = None) -> dict:
    """Re-derive the roots from the seal's OWN manifest and (optionally) check the signature.

    Deliberately does NOT touch the filesystem: a sealed/undisclosed tree still verifies from its
    manifest alone. ``signature_ok`` is ``None`` when no pubkey/signature is supplied (not a
    failure). ``ok`` is False only on an actual mismatch.
    """
    manifest = seal.get("manifest", [])
    recomputed_root = merkle_root(manifest_leaves(manifest))
    root_ok = recomputed_root == seal.get("merkle_root")
    flat_ok = flat_anchor(manifest) == seal.get("flat_anchor")
    sig = seal.get("signature")
    if pub_b64 and sig:
        from core.round_table.worm_signing import verify_worm_hash

        signature_ok: Optional[bool] = verify_worm_hash(
            seal.get("merkle_root", ""), sig, pub_b64
        )
    else:
        signature_ok = None
    return {
        "root_ok": root_ok,
        "flat_ok": flat_ok,
        "signature_ok": signature_ok,
        "ok": bool(root_ok and flat_ok and signature_ok is not False),
    }


def verify_against_tree(seal: dict, root: os.PathLike | str) -> dict:
    """Re-hash the ACTUAL files at ``root`` and confirm they reproduce the seal's manifest + root.

    The reproducibility check (opposite of ``verify_seal``, which trusts the manifest): catches any
    later tampering with the tree OR with the stored seal. Used by the provenance CI to re-verify a
    ``provenance-seal-*`` tag against its committed ``seal.repo.json``.
    """
    rebuilt = build_manifest(root)
    manifest_matches = rebuilt == seal.get("manifest")
    root_ok = merkle_root(manifest_leaves(rebuilt)) == seal.get("merkle_root")
    return {
        "manifest_matches": manifest_matches,
        "root_ok": root_ok,
        "ok": bool(manifest_matches and root_ok),
    }


# ── OpenTimestamps anchoring (free, decentralized; the eIDAS QTS layer lands in Inc 4) ──
# Shells out to the `ots` client through an INJECTABLE runner so unit tests need neither the
# binary nor the network. The real stamp/verify/upgrade is an operational step (see VERIFY.md).
_Runner = Callable[[List[str]], "subprocess.CompletedProcess"]


def _default_runner(cmd: List[str]) -> "subprocess.CompletedProcess":
    return subprocess.run(cmd, capture_output=True, text=True)


def ots_available(*, runner: _Runner = _default_runner) -> bool:
    try:
        res = runner(["ots", "--version"])
    except OSError:
        return False
    return getattr(res, "returncode", 1) == 0


def ots_stamp(
    digest_hex: str, out_dir: os.PathLike | str, *, runner: _Runner = _default_runner
) -> Path:
    """Write the Merkle root to ``merkle_root.txt`` and OpenTimestamps-stamp it → the ``.ots`` proof.

    The proof is initially *pending*; run ``ots upgrade`` after the Bitcoin attestation matures.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    root_file = out / "merkle_root.txt"
    root_file.write_text(digest_hex + "\n", encoding="utf-8")
    res = runner(["ots", "stamp", str(root_file)])
    if getattr(res, "returncode", 1) != 0:
        raise RuntimeError(f"ots stamp failed: {getattr(res, 'stderr', '')}")
    return root_file.with_name(root_file.name + ".ots")


def ots_verify(
    proof_file: os.PathLike | str, *, runner: _Runner = _default_runner
) -> bool:
    res = runner(["ots", "verify", str(proof_file)])
    return getattr(res, "returncode", 1) == 0


def _write_seal(seal: dict, out: Optional[str]) -> None:
    text = json.dumps(seal, indent=2, sort_keys=True)
    if out:
        Path(out).write_text(text + "\n", encoding="utf-8")
        print(f"merkle_root={seal['merkle_root']} files={seal['file_count']} -> {out}")
    else:
        print(text)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Provenance seal — Merkle manifest, sign, timestamp."
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_seal = sub.add_parser(
        "seal", help="build a (optionally signed) Merkle seal of a tree"
    )
    p_seal.add_argument("root", help="tree to seal (clean checkout)")
    p_seal.add_argument("--out", help="write the seal JSON here (default: stdout)")
    p_seal.add_argument(
        "--sign", action="store_true", help="attach an Ed25519 signature"
    )

    p_verify = sub.add_parser(
        "verify", help="verify a seal's roots (+ signature) from its manifest"
    )
    p_verify.add_argument("seal_json", help="path to a seal JSON")
    p_verify.add_argument(
        "--pubkey", help="base64 Ed25519 public key to check the signature"
    )

    p_anchor = sub.add_parser(
        "anchor", help="seal + sign + OpenTimestamps-stamp the root (operational)"
    )
    p_anchor.add_argument("root", help="tree to seal")
    p_anchor.add_argument(
        "--out-dir", default=".", help="directory for seal.json + merkle_root.txt(.ots)"
    )

    p_check = sub.add_parser(
        "check-tree",
        help="reproduce a seal against an actual tree (CI reproducibility gate)",
    )
    p_check.add_argument(
        "seal_json", help="path to a seal JSON (e.g. anchors/<date>/seal.repo.json)"
    )
    p_check.add_argument(
        "root", help="tree to re-hash and compare (a clean checkout of the commit)"
    )

    args = ap.parse_args(argv)

    if args.cmd == "seal":
        seal = seal_tree(args.root)
        if args.sign:
            seal["signature"] = sign_root(seal["merkle_root"])
        _write_seal(seal, args.out)
        return 0

    if args.cmd == "verify":
        seal = json.loads(Path(args.seal_json).read_text(encoding="utf-8"))
        rep = verify_seal(seal, args.pubkey)
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 1

    if args.cmd == "check-tree":
        seal = json.loads(Path(args.seal_json).read_text(encoding="utf-8"))
        rep = verify_against_tree(seal, args.root)
        print(json.dumps(rep, indent=2))
        return 0 if rep["ok"] else 1

    if args.cmd == "anchor":
        out_dir = Path(args.out_dir)
        seal = seal_tree(args.root)
        seal["signature"] = sign_root(seal["merkle_root"])
        _write_seal(seal, str(out_dir / "seal.json"))
        if not ots_available():
            print(
                "WARN: `ots` client not installed — root written, stamp skipped "
                "(`pip install opentimestamps-client`, then re-run anchor)."
            )
            return 0
        proof = ots_stamp(seal["merkle_root"], out_dir)
        print(
            f"OpenTimestamps proof (pending): {proof} — run `ots upgrade` once Bitcoin confirms."
        )
        return 0

    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
