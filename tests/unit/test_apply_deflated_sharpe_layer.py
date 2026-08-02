"""MLR-9 (#1909): Layer-5 offline producer — stamps deflated_sharpe into metadata.

scripts/apply_deflated_sharpe_layer.py walks a per-symbol tree (<SYM>/metadata.json),
reads the net-of-cost walk-forward return series (``layer4.net_returns`` in
metadata.json, else the ``net_returns.json`` sidecar), computes the Bailey/Lopez
de Prado deflation via core.ml.deflated_sharpe and stamps (non-destructively):

  top-level: deflated_sharpe (annualized SR - E[max SR] margin), psr, n_trials
  layer5:    {deflated_sharpe, dsr_prob, psr, pbo, n_trials, sr_variance, ...}

n_trials is NEVER hard-coded (plan §12.2): explicit --n-trials wins, else
(unique manifest symbols | usable tree symbols) x seeds_per_symbol (best-of-N
seed selection). Symbols without a series are SKIPPED untouched (fail-safe —
the gate treats the absent field as legacy passthrough).

Operator tool — offline only, never imported by the live trading path.
"""

import json

import pytest

from scripts.apply_deflated_sharpe_layer import apply_layer, resolve_n_trials

# Deterministic fixtures: strong edge (per-period SR ~5) vs. zero-edge noise.
_STRONG = [0.012, 0.008] * 100
_WEAK = [0.001, -0.001] * 100


def _sym(tmp_path, name, meta):
    d = tmp_path / name
    d.mkdir()
    (d / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def test_stamps_fields_and_preserves_existing(tmp_path):
    _sym(
        tmp_path,
        "AAA",
        {"val_loss": 0.01, "net_sharpe": 0.8, "layer4": {"net_returns": _STRONG}},
    )
    summary = apply_layer(tmp_path, n_trials=9, sr_variance=0.01)
    assert summary["stamped"] == ["AAA"]
    assert summary["n_trials"] == 9

    meta = json.loads((tmp_path / "AAA" / "metadata.json").read_text(encoding="utf-8"))
    # pre-existing fields preserved (non-destructive merge)
    assert meta["val_loss"] == 0.01
    assert meta["net_sharpe"] == 0.8
    assert meta["layer4"]["net_returns"] == _STRONG
    # new fields: top level + layer5 block
    assert isinstance(meta["deflated_sharpe"], float)
    assert 0.0 <= meta["psr"] <= 1.0
    assert meta["n_trials"] == 9
    l5 = meta["layer5"]
    assert l5["n_trials"] == 9
    assert l5["deflated_sharpe"] == meta["deflated_sharpe"]
    assert 0.0 <= l5["dsr_prob"] <= 1.0
    assert l5["pbo"] is None  # CSCV split matrix not in artefacts yet (plan §12.7)
    assert l5["n_obs"] == len(_STRONG)


def test_sign_strong_positive_weak_negative(tmp_path):
    _sym(tmp_path, "GOOD", {"layer4": {"net_returns": _STRONG}})
    _sym(tmp_path, "BAD", {"layer4": {"net_returns": _WEAK}})
    apply_layer(tmp_path, n_trials=100, sr_variance=0.01)
    good = json.loads((tmp_path / "GOOD" / "metadata.json").read_text(encoding="utf-8"))
    bad = json.loads((tmp_path / "BAD" / "metadata.json").read_text(encoding="utf-8"))
    assert good["deflated_sharpe"] > 0  # SR ~5 clears E[max SR] easily
    assert bad["deflated_sharpe"] < 0  # zero-edge noise does not


def test_symbol_without_series_skipped_untouched(tmp_path):
    d = _sym(tmp_path, "NOSERIES", {"val_loss": 0.01, "walkforward_ic": 0.1})
    _sym(tmp_path, "AAA", {"layer4": {"net_returns": _STRONG}})
    before = (d / "metadata.json").read_text(encoding="utf-8")
    summary = apply_layer(tmp_path, n_trials=3, sr_variance=0.01)
    assert "NOSERIES" in summary["skipped"]
    assert summary["stamped"] == ["AAA"]
    # fail-safe: file byte-identical → gate passthrough (legacy metadata)
    assert (d / "metadata.json").read_text(encoding="utf-8") == before


def test_sidecar_net_returns_fallback(tmp_path):
    d = _sym(tmp_path, "SIDE", {"val_loss": 0.01})
    (d / "net_returns.json").write_text(json.dumps(_STRONG), encoding="utf-8")
    summary = apply_layer(tmp_path, n_trials=3, sr_variance=0.01)
    assert summary["stamped"] == ["SIDE"]
    meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    assert isinstance(meta["deflated_sharpe"], float)


def test_n_trials_from_manifest_times_seeds(tmp_path):
    manifest = tmp_path / "tft_models_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "models": [
                    {"symbol": "AAA", "filename": "AAA/checkpoint.pt"},
                    {"symbol": "AAA", "filename": "AAA/training_ds_1.pkl"},
                    {"symbol": "BBB", "filename": "BBB/checkpoint.pt"},
                    {"symbol": "BBB", "filename": "BBB/training_ds_1.pkl"},
                ]
            }
        ),
        encoding="utf-8",
    )
    # 2 unique symbols x 3 seeds = 6 (best-of-3-seed selection, plan §12.2)
    assert resolve_n_trials(None, manifest, n_symbols_fallback=99) == 6
    # explicit always wins
    assert resolve_n_trials(42, manifest, n_symbols_fallback=99) == 42


def test_n_trials_fallback_to_tree_count(tmp_path):
    assert resolve_n_trials(None, None, n_symbols_fallback=5) == 15


def test_dry_run_writes_nothing(tmp_path):
    d = _sym(tmp_path, "AAA", {"layer4": {"net_returns": _STRONG}})
    before = (d / "metadata.json").read_text(encoding="utf-8")
    summary = apply_layer(tmp_path, n_trials=3, sr_variance=0.01, dry_run=True)
    assert summary["stamped"] == ["AAA"]  # reported, not written
    assert (d / "metadata.json").read_text(encoding="utf-8") == before


def test_cross_sectional_sr_variance_derived_when_not_given(tmp_path):
    # Two usable symbols → V = population variance of their per-period SRs.
    _sym(tmp_path, "GOOD", {"layer4": {"net_returns": _STRONG}})
    _sym(tmp_path, "BAD", {"layer4": {"net_returns": _WEAK}})
    summary = apply_layer(tmp_path, n_trials=6)
    assert summary["sr_variance"] > 0


def test_single_symbol_without_variance_override_fails_loudly(tmp_path):
    _sym(tmp_path, "ONLY", {"layer4": {"net_returns": _STRONG}})
    with pytest.raises(ValueError, match="sr_variance"):
        apply_layer(tmp_path, n_trials=3)
