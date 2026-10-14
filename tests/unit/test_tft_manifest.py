# scripts/build_tft_manifest + build_tft_serving_bundle — Model-Provenance Issue 1 (fusion)
# TDD Red → Green. implementation_plan 2026-06-09-model-provenance (Issue 1).
#
# The serving-bundle packer stages ONLY the serving files per symbol (checkpoint.pt +
# metadata.json + the metadata-matched training_ds, ADR-ML-DS-01) and the manifest
# carries a SHA-256 for BOTH executable artifacts — checkpoint.pt AND the matched
# training_ds (W-4: pickle.load is the same RCE risk as torch.load(weights_only=False)).
# Seed checkpoints, seed training_ds and _v2_train_logs/ are excluded.

import json
from unittest.mock import patch


def _mk_symbol_no_ds(root, sym):
    """A symbol with a checkpoint but NO resolvable training_ds (incomplete/not servable)."""
    d = root / sym
    d.mkdir(parents=True, exist_ok=True)
    (d / "checkpoint.pt").write_bytes(b"CKPT-" + sym.encode())
    return d


def _mk_symbol(root, sym, *, with_seeds=True):
    """Build a realistic per-symbol model dir (matched ds = seed0 via promoted_from)."""
    d = root / sym
    d.mkdir(parents=True, exist_ok=True)
    (d / "checkpoint.pt").write_bytes(b"CKPT-" + sym.encode())
    (d / "metadata.json").write_text(
        json.dumps({"promoted_from": "checkpoint_v2_seed0_10y_full491.pt"}),
        encoding="utf-8",
    )
    matched = "training_ds_v2_seed0_10y_full491.pkl"  # ADR-ML-DS-01 match of seed0
    (d / matched).write_bytes(b"DS-MATCHED-" + sym.encode())
    if with_seeds:
        # Training artifacts that the packer MUST exclude (serving needs none of these).
        (d / "checkpoint_v2_seed0_10y_full491.pt").write_bytes(b"SEED0")
        (d / "checkpoint_v2_seed1_10y_full491.pt").write_bytes(b"SEED1")
        (d / "training_ds_v2_seed1_10y_full491.pkl").write_bytes(b"DS-SEED1")
        (d / "_v2_train_logs").mkdir(exist_ok=True)
        (d / "_v2_train_logs" / "log.txt").write_text("noise", encoding="utf-8")
    return d, matched


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------
def test_manifest_has_checkpoint_and_training_ds_sha(tmp_path):
    from scripts.build_tft_manifest import build_tft_manifest

    _mk_symbol(tmp_path, "AAPL")
    _mk_symbol(tmp_path, "MSFT")
    manifest = build_tft_manifest(tmp_path)

    files = {e["filename"] for e in manifest["models"]}
    # W-4: BOTH executable artifacts SHA-256'd, per symbol
    assert "AAPL/checkpoint.pt" in files
    assert "AAPL/training_ds_v2_seed0_10y_full491.pkl" in files
    assert "MSFT/checkpoint.pt" in files
    # the seed (non-matched) training_ds is NOT manifested
    assert "AAPL/training_ds_v2_seed1_10y_full491.pkl" not in files
    for entry in manifest["models"]:
        assert len(entry["sha256"]) == 64
        assert entry["size_bytes"] > 0
        assert entry["symbol"] in {"AAPL", "MSFT"}


def test_manifest_skips_symbol_without_checkpoint(tmp_path):
    from scripts.build_tft_manifest import build_tft_manifest

    _mk_symbol(tmp_path, "AAPL")
    (tmp_path / "EMPTY").mkdir()
    manifest = build_tft_manifest(tmp_path)
    assert all(not e["filename"].startswith("EMPTY/") for e in manifest["models"])


def test_verify_round_trip_and_tamper_detection(tmp_path):
    from scripts.build_tft_manifest import build_tft_manifest, verify_tft_manifest

    _mk_symbol(tmp_path, "AAPL")
    manifest = build_tft_manifest(tmp_path)
    assert verify_tft_manifest(manifest, tmp_path) == 0  # clean round-trip

    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(b"TAMPERED")
    assert verify_tft_manifest(manifest, tmp_path) == 1  # SHA mismatch caught


# ---------------------------------------------------------------------------
# Serving-bundle packer
# ---------------------------------------------------------------------------
def test_packer_stages_only_serving_files(tmp_path):
    from scripts.build_tft_serving_bundle import stage_serving_tree

    src, dest = tmp_path / "src", tmp_path / "dest"
    _, matched = _mk_symbol(src, "AAPL")
    staged = stage_serving_tree(src, dest)

    assert staged == ["AAPL"]
    out = dest / "AAPL"
    present = {p.name for p in out.iterdir()}
    assert present == {"checkpoint.pt", "metadata.json", matched}
    # seed/training artifacts excluded
    assert not (out / "checkpoint_v2_seed0_10y_full491.pt").exists()
    assert not (out / "training_ds_v2_seed1_10y_full491.pkl").exists()
    assert not (out / "_v2_train_logs").exists()


def test_packer_skips_symbol_without_checkpoint(tmp_path):
    from scripts.build_tft_serving_bundle import stage_serving_tree

    src, dest = tmp_path / "src", tmp_path / "dest"
    _mk_symbol(src, "AAPL")
    (src / "EMPTY").mkdir(parents=True)
    staged = stage_serving_tree(src, dest)
    assert staged == ["AAPL"]
    assert not (dest / "EMPTY").exists()


def test_packed_tree_verifies_against_its_manifest(tmp_path):
    # End-to-end: stage serving-only → build manifest on the staged tree → verify clean
    # (proves the staged <SYM>/ layout matches the manifest filenames — peer-review point).
    from scripts.build_tft_manifest import build_tft_manifest, verify_tft_manifest
    from scripts.build_tft_serving_bundle import stage_serving_tree

    src, dest = tmp_path / "src", tmp_path / "dest"
    _mk_symbol(src, "AAPL")
    _mk_symbol(src, "MSFT")
    stage_serving_tree(src, dest)
    manifest = build_tft_manifest(dest)
    assert verify_tft_manifest(manifest, dest) == 0


# ---------------------------------------------------------------------------
# Review fixes: missing-file, W-4 incomplete enforcement, path-traversal, resolve-fail
# ---------------------------------------------------------------------------
def test_verify_catches_missing_file(tmp_path):
    from scripts.build_tft_manifest import build_tft_manifest, verify_tft_manifest

    _mk_symbol(tmp_path, "AAPL")
    manifest = build_tft_manifest(tmp_path)
    (tmp_path / "AAPL" / "checkpoint.pt").unlink()  # drop a manifested file
    assert verify_tft_manifest(manifest, tmp_path) == 1


def test_manifest_excludes_and_reports_symbol_without_training_ds(tmp_path):
    # W-4: a checkpoint without a verifiable training_ds must NOT be manifested as
    # complete — it is recorded as incomplete and emits no (partial) entry.
    from scripts.build_tft_manifest import build_tft_manifest

    _mk_symbol(tmp_path, "AAPL")  # complete
    _mk_symbol_no_ds(tmp_path, "NODS")  # checkpoint only
    manifest = build_tft_manifest(tmp_path)

    assert "NODS" in manifest["incomplete"]
    assert all(not e["filename"].startswith("NODS/") for e in manifest["models"])
    assert any(e["filename"] == "AAPL/checkpoint.pt" for e in manifest["models"])


def test_packer_skips_symbol_without_training_ds(tmp_path):
    from scripts.build_tft_serving_bundle import stage_serving_tree

    src, dest = tmp_path / "src", tmp_path / "dest"
    _mk_symbol(src, "AAPL")
    _mk_symbol_no_ds(src, "NODS")
    staged = stage_serving_tree(src, dest)

    assert staged == ["AAPL"]
    assert not (dest / "NODS").exists()


def test_verify_rejects_path_traversal(tmp_path):
    from scripts.build_tft_manifest import verify_tft_manifest

    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")  # outside the tree
    tree = tmp_path / "tree"
    tree.mkdir()
    malicious = {"models": [{"filename": "../secret.txt", "sha256": "deadbeef"}]}
    assert verify_tft_manifest(malicious, tree) == 1  # escape rejected, not hashed


def test_matched_training_ds_none_on_resolver_error(tmp_path):
    # The shared resolver returns None (loud, not silent) if the runtime resolver raises
    # (e.g. operator env without pandas) → callers treat it as incomplete (W-4).
    from scripts import _tft_provenance

    _mk_symbol_no_ds(tmp_path, "AAPL")
    with patch(
        "core.ml.tft_inference.TFTInferenceEngine",
        side_effect=RuntimeError("no pandas"),
    ):
        assert _tft_provenance.matched_training_ds(tmp_path / "AAPL") is None
