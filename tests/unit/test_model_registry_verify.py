# core/ml/model_registry — RF-3 verify-before-load gate (provenance Issue 2)
# TDD Red → Green. implementation_plan 2026-06-09-model-provenance §7 (Issue 2).
#
# The gate SHA-256-verifies BOTH executable artifacts the engine will unpickle —
# checkpoint.pt (torch.load weights_only=False) AND the matched training_ds (pickle.load,
# W-4) — against the provenance manifest BEFORE constructing/loading. Fail-closed-to-None.
# Manifest absent → TFT_REQUIRE_MANIFEST (default True cloud / False local).
#
# No sys.modules torch mocks (§9). TFTInferenceEngine is replaced by a light fake so the
# test never touches torch / never unpickles anything; the gate is the unit under test.

import hashlib
import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import core.ml.model_registry as mr


@pytest.fixture(autouse=True)
def _tft_root_via_config(monkeypatch):
    # Section 2.10: model_registry resolves TFT_MODELS_ROOT via get_config() (not
    # os.getenv). Route it to a fresh RuntimeConfigState (BaseSettings re-reads the env
    # at instantiation) so this file's monkeypatch.setenv("TFT_MODELS_ROOT", ...) works.
    from config import RuntimeConfigState

    monkeypatch.setattr(mr, "get_config", lambda: RuntimeConfigState())


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class _FakeEngine:
    """Stands in for TFTInferenceEngine — no torch. Tracks whether load() ran.

    ``ds_name`` / ``raise_resolve`` are class-level knobs (reset by _setup) so a test
    can exercise a versioned training_ds name or a resolver that raises."""

    instances: list = []
    ds_name = "training_ds.pkl"
    raise_resolve = False

    def __init__(self, symbol, model_dir):
        self.symbol = symbol
        self.model_dir = Path(model_dir)
        self._load_error = None
        # D3: the gate pins the read-once VERIFIED BYTES (not just the ds path) so load()
        # unpickles exactly the verified buffers — closes the FUSE TOCTOU for BOTH files.
        self._pinned_ckpt_bytes = None
        self._pinned_ds_bytes = None
        self.load = MagicMock(return_value=True)
        _FakeEngine.instances.append(self)

    def _resolve_training_ds_path(self):
        if _FakeEngine.raise_resolve:
            raise RuntimeError("resolver boom")
        return self.model_dir / _FakeEngine.ds_name


def _make_tree(tmp: Path, *, ckpt=b"CKPT-bytes", ds=b"DS-bytes", symbol="AAPL"):
    d = tmp / symbol
    d.mkdir(parents=True)
    (d / "checkpoint.pt").write_bytes(ckpt)
    (d / "training_ds.pkl").write_bytes(ds)
    return d


def _manifest_for(tmp: Path, symbol="AAPL", *, ckpt_sha=None, ds_sha=None):
    d = tmp / symbol
    ck, ds = d / "checkpoint.pt", d / "training_ds.pkl"
    return {
        "schema_version": 1,
        "kind": "tft-per-symbol",
        "models": [
            {
                "symbol": symbol,
                "filename": f"{symbol}/checkpoint.pt",
                "sha256": ckpt_sha or _sha(ck),
                "size_bytes": ck.stat().st_size,
            },
            {
                "symbol": symbol,
                "filename": f"{symbol}/training_ds.pkl",
                "sha256": ds_sha or _sha(ds),
                "size_bytes": ds.stat().st_size,
            },
        ],
        "incomplete": [],
    }


def _setup(
    tmp,
    monkeypatch,
    *,
    with_manifest=True,
    ckpt_sha=None,
    ds_sha=None,
    manifest_symbol="AAPL",
    traversal=False,
    require=None,
):
    """Build the AAPL serving tree FIRST (so manifest SHAs hash real files), point env
    at it, then optionally write a manifest variant."""
    _make_tree(tmp)
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp))
    _FakeEngine.instances = []
    _FakeEngine.ds_name = "training_ds.pkl"
    _FakeEngine.raise_resolve = False
    if require is not None:
        monkeypatch.setenv("TFT_REQUIRE_MANIFEST", require)
    if not with_manifest:
        monkeypatch.setenv("TFT_MANIFEST_PATH", str(tmp / "nope.json"))
        return
    m = _manifest_for(tmp, ckpt_sha=ckpt_sha, ds_sha=ds_sha)
    if manifest_symbol != "AAPL":
        for e in m["models"]:
            e["symbol"] = manifest_symbol
            e["filename"] = e["filename"].replace("AAPL", manifest_symbol)
    if traversal:
        m["models"][0]["filename"] = "../../etc/passwd"
    mpath = tmp / "tft_models_manifest.json"
    mpath.write_text(json.dumps(m), encoding="utf-8")
    monkeypatch.setenv("TFT_MANIFEST_PATH", str(mpath))


async def _ensure(reg, sym="AAPL"):
    with patch.object(mr, "TFTInferenceEngine", _FakeEngine), patch.object(
        mr, "_gate_evaluate", return_value=types.SimpleNamespace(passed=True, reason="")
    ):
        return await reg._ensure_engine(sym)


# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_matching_sha_loads(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    reg = mr._TFTModelRegistry()
    engine = await _ensure(reg)
    assert engine is not None
    assert _FakeEngine.instances[-1].load.called  # passed verify → load ran


@pytest.mark.anyio
async def test_tampered_checkpoint_refused_without_loading(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, ckpt_sha="deadbeef" * 8)
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing
    assert not _FakeEngine.instances[-1].load.called  # torch.load NEVER reached


@pytest.mark.anyio
async def test_tampered_training_ds_refused(tmp_path, monkeypatch):
    # W-4: the matched training_ds is pickle.load-ed → must be SHA-verified too.
    _setup(tmp_path, monkeypatch, ds_sha="cafe" * 16)
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing
    assert not _FakeEngine.instances[-1].load.called


@pytest.mark.anyio
async def test_symbol_absent_from_manifest_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, manifest_symbol="MSFT")
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing


@pytest.mark.anyio
async def test_no_manifest_local_permissive_loads(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, with_manifest=False, require="false")
    reg = mr._TFTModelRegistry()
    engine = await _ensure(reg)
    assert engine is not None  # local-dev: loads UNVERIFIED with a warning
    assert _FakeEngine.instances[-1].load.called


@pytest.mark.anyio
async def test_no_manifest_strict_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, with_manifest=False, require="true")
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing


@pytest.mark.anyio
async def test_require_defaults_true_off_local(tmp_path, monkeypatch):
    # No explicit TFT_REQUIRE_MANIFEST -> strict even under DEPLOYMENT_MODE=LOCAL.
    _setup(tmp_path, monkeypatch, with_manifest=False)
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert await _ensure(mr._TFTModelRegistry()) is None  # default strict (cloud)
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    assert (
        await _ensure(mr._TFTModelRegistry()) is None
    )  # LOCAL -> also strict by default
    monkeypatch.setenv("TFT_REQUIRE_MANIFEST", "false")
    assert (
        await _ensure(mr._TFTModelRegistry()) is not None
    )  # explicit override -> permissive


@pytest.mark.anyio
async def test_path_traversal_entry_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, traversal=True)
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None


@pytest.mark.anyio
async def test_versioned_ds_verified_and_pinned(tmp_path, monkeypatch):
    # The resolved ds is a VERSIONED name (not the legacy default): it must be the file
    # that is SHA-verified AND pinned onto the engine (closes the metadata-swap TOCTOU).
    d = tmp_path / "AAPL"
    d.mkdir(parents=True)
    (d / "checkpoint.pt").write_bytes(b"CK")
    (d / "training_ds_v2.pkl").write_bytes(b"VDS")
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    manifest = {
        "models": [
            {
                "symbol": "AAPL",
                "filename": "AAPL/checkpoint.pt",
                "sha256": _sha(d / "checkpoint.pt"),
                "size_bytes": 2,
            },
            {
                "symbol": "AAPL",
                "filename": "AAPL/training_ds_v2.pkl",
                "sha256": _sha(d / "training_ds_v2.pkl"),
                "size_bytes": 3,
            },
        ],
    }
    mpath = tmp_path / "tft_models_manifest.json"
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("TFT_MANIFEST_PATH", str(mpath))
    _FakeEngine.instances = []
    _FakeEngine.ds_name = "training_ds_v2.pkl"
    _FakeEngine.raise_resolve = False
    reg = mr._TFTModelRegistry()
    engine = await _ensure(reg)
    assert engine is not None
    # D3: the VERSIONED ds + checkpoint are read once, verified, and pinned as BYTES
    # (load() unpickles these buffers — never re-opens the path).
    assert engine._pinned_ckpt_bytes == b"CK"
    assert engine._pinned_ds_bytes == b"VDS"


@pytest.mark.anyio
async def test_resolver_raising_fails_closed(tmp_path, monkeypatch):
    # A raising _resolve_training_ds_path must NOT escape the gate → refuse + cache miss.
    _setup(tmp_path, monkeypatch)
    _FakeEngine.raise_resolve = True
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing
    assert not _FakeEngine.instances[-1].load.called


@pytest.mark.anyio
async def test_malformed_manifest_entry_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    m = _manifest_for(tmp_path)
    del m["models"][0]["sha256"]  # malformed: entry missing its SHA
    (tmp_path / "tft_models_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert not _FakeEngine.instances[-1].load.called


# --- D3: read-once buffer-verify closes the FUSE TOCTOU ----------------------
@pytest.mark.anyio
async def test_d3_pins_readonce_buffers_for_both_files(tmp_path, monkeypatch):
    # The gate reads checkpoint.pt + training_ds.pkl ONCE, verifies the BYTES, and pins
    # them on the engine — so load() unpickles the verified buffers, not a re-opened path.
    _setup(tmp_path, monkeypatch)
    reg = mr._TFTModelRegistry()
    engine = await _ensure(reg)
    assert engine is not None
    assert engine._pinned_ckpt_bytes == b"CKPT-bytes"
    assert engine._pinned_ds_bytes == b"DS-bytes"


@pytest.mark.anyio
async def test_d3_toctou_swap_after_verify_is_ineffective(tmp_path, monkeypatch):
    # TOCTOU: an attacker swaps checkpoint.pt in the bucket AFTER the gate hashed it.
    # The gate already captured the VERIFIED bytes → the pin is unaffected by the swap.
    _setup(tmp_path, monkeypatch)
    reg = mr._TFTModelRegistry()
    engine = await _ensure(reg)
    assert engine is not None
    (tmp_path / "AAPL" / "checkpoint.pt").write_bytes(
        b"EVIL-SWAPPED"
    )  # post-verify swap
    (tmp_path / "AAPL" / "training_ds.pkl").write_bytes(b"EVIL-DS")
    # pinned buffers still hold the originally-verified content
    assert engine._pinned_ckpt_bytes == b"CKPT-bytes"
    assert engine._pinned_ds_bytes == b"DS-bytes"


@pytest.mark.anyio
async def test_d3_read_error_fails_closed(tmp_path, monkeypatch):
    # A FUSE read failure on the artifact must fail CLOSED (engine None), never reach load.
    _setup(tmp_path, monkeypatch)
    real_read = Path.read_bytes

    def _boom(self, *a, **kw):
        if self.name == "checkpoint.pt":
            raise OSError("FUSE read drop")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_bytes", _boom)
    reg = mr._TFTModelRegistry()
    assert await _ensure(reg) is None
    assert "AAPL" in reg._known_missing
    assert not _FakeEngine.instances[-1].load.called
