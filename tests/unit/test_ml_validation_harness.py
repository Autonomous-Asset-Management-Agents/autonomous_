"""
test_ml_validation_harness.py — unit tests for the canonical purged +
embargoed walk-forward / CPCV harness (core/ml/validation/harness.py).

Scope of Issue #1906 (MLR-6): one purged+embargoed validation library that
RL / LSTM / TFT trainers will adopt in follow-up PRs. This brick is the
library + tests only — no trainer is wired to it yet (dormant / additive).

Covered here:
  * purge_overlapping_labels  — label-overlap purging (López de Prado Ch.7)
  * apply_embargo             — trailing embargo after each test block
  * purged_walk_forward       — expanding-window purged walk-forward folds
  * combinatorial_purged_cv   — CPCV folds, C(n_groups, n_test_groups)
  * shuffle_target_leakage_test — permutation sanity check for the CI gate
"""

import numpy as np
import pytest

from core.ml.validation import (
    Fold,
    LeakageResult,
    apply_embargo,
    combinatorial_purged_cv,
    purge_overlapping_labels,
    purged_walk_forward,
    shuffle_target_leakage_test,
)

# ---------------------------------------------------------------------------
# purge_overlapping_labels
# ---------------------------------------------------------------------------


class TestPurge:
    def test_removes_train_labels_overlapping_test_horizon(self):
        train = np.arange(0, 10)  # 0..9
        test = np.arange(10, 15)  # 10..14
        purged = purge_overlapping_labels(train, test, label_horizon=2)
        # samples 8,9 have label windows [8,10] / [9,11] that reach into test
        assert set(purged.tolist()) == set(range(0, 8))
        # never keep a test index in the train set
        assert not (set(purged.tolist()) & set(test.tolist()))

    def test_horizon_zero_keeps_all_train(self):
        train = np.arange(0, 10)
        test = np.arange(10, 15)
        purged = purge_overlapping_labels(train, test, label_horizon=0)
        assert set(purged.tolist()) == set(train.tolist())

    def test_bidirectional_purge_for_middle_test_block(self):
        # CPCV-style: test block sits in the middle, train on both sides
        train = np.concatenate([np.arange(0, 10), np.arange(15, 20)])
        test = np.arange(10, 15)  # block [10..14]
        purged = purge_overlapping_labels(train, test, label_horizon=2)
        # left leak: 8,9 ; right leak: 15,16 (their windows reach back to 14)
        assert set(purged.tolist()) == {0, 1, 2, 3, 4, 5, 6, 7, 17, 18, 19}

    def test_empty_inputs_are_safe(self):
        assert purge_overlapping_labels(np.array([], int), np.arange(3), 5).size == 0
        out = purge_overlapping_labels(np.arange(3), np.array([], int), 5)
        assert set(out.tolist()) == {0, 1, 2}


# ---------------------------------------------------------------------------
# apply_embargo
# ---------------------------------------------------------------------------


class TestEmbargo:
    def test_removes_samples_immediately_after_test(self):
        train = np.concatenate([np.arange(0, 10), np.arange(15, 20)])
        test = np.arange(10, 15)  # block ends at 14
        out = apply_embargo(train, test, embargo=2)
        # embargo zone (14, 16] -> drops 15,16 ; left side untouched
        assert set(out.tolist()) == {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 17, 18, 19}

    def test_zero_embargo_is_noop(self):
        train = np.arange(0, 20)
        test = np.arange(10, 15)
        out = apply_embargo(train, test, embargo=0)
        assert set(out.tolist()) == set(train.tolist())

    def test_fractional_embargo_scales_with_n_samples(self):
        train = np.arange(0, 100)
        test = np.arange(40, 50)  # ends at 49
        out = apply_embargo(train, test, embargo=0.1, n_samples=100)
        # 0.1 * 100 = 10 -> drop (49, 59] = 50..59
        assert set(range(50, 60)).isdisjoint(set(out.tolist()))
        assert 60 in out.tolist() and 49 in out.tolist()


# ---------------------------------------------------------------------------
# purged_walk_forward
# ---------------------------------------------------------------------------


class TestWalkForward:
    def test_yields_requested_number_of_folds(self):
        folds = list(purged_walk_forward(100, n_splits=5))
        assert len(folds) == 5
        assert all(isinstance(f, Fold) for f in folds)

    def test_folds_are_chronological_and_disjoint(self):
        folds = list(purged_walk_forward(100, n_splits=5))
        prev_test_end = -1
        for f in folds:
            # train strictly precedes test (expanding window, no leakage)
            assert f.train_idx.max() < f.test_idx.min()
            # tests advance in time and never overlap
            assert f.test_idx.min() > prev_test_end
            prev_test_end = f.test_idx.max()
            assert set(f.train_idx.tolist()).isdisjoint(set(f.test_idx.tolist()))

    def test_purge_and_embargo_open_a_gap_before_test(self):
        # with a label horizon the last train samples before test must be purged
        folds = list(purged_walk_forward(120, n_splits=3, label_horizon=3, embargo=2))
        for f in folds:
            gap = f.test_idx.min() - f.train_idx.max()
            assert gap > 1  # a real purge/embargo gap, not an adjacent boundary

    def test_raises_when_too_few_samples(self):
        with pytest.raises(ValueError):
            list(purged_walk_forward(3, n_splits=5))


# ---------------------------------------------------------------------------
# combinatorial_purged_cv
# ---------------------------------------------------------------------------


class TestCPCV:
    def test_number_of_folds_is_n_choose_k(self):
        folds = list(combinatorial_purged_cv(120, n_groups=6, n_test_groups=2))
        assert len(folds) == 15  # C(6,2)

    def test_train_and_test_are_disjoint_and_labelled(self):
        folds = list(combinatorial_purged_cv(120, n_groups=6, n_test_groups=2))
        for f in folds:
            assert set(f.train_idx.tolist()).isdisjoint(set(f.test_idx.tolist()))
            assert f.test_groups is not None and len(f.test_groups) == 2

    def test_purge_removes_boundary_leakage_in_cpcv(self):
        no_purge = list(
            combinatorial_purged_cv(120, n_groups=6, n_test_groups=2, label_horizon=0)
        )
        purged = list(
            combinatorial_purged_cv(120, n_groups=6, n_test_groups=2, label_horizon=4)
        )
        # purging can only shrink (or keep) the train set, never grow it
        assert all(
            p.train_idx.size <= n.train_idx.size for p, n in zip(purged, no_purge)
        )
        # at least one fold actually loses boundary samples to purging
        assert any(
            p.train_idx.size < n.train_idx.size for p, n in zip(purged, no_purge)
        )

    def test_invalid_group_config_raises(self):
        with pytest.raises(ValueError):
            list(combinatorial_purged_cv(120, n_groups=3, n_test_groups=3))


# ---------------------------------------------------------------------------
# shuffle_target_leakage_test — the CI-gate helper
# ---------------------------------------------------------------------------


class _MeanModel:
    """Clean baseline: predicts the train mean, independent of the target."""

    def fit(self, X, y):
        self._mean = float(np.mean(y))
        return self

    def predict(self, X):
        return np.full(len(X), self._mean)


class _MemorizeModel:
    """Synthetic leaky model that 'sees' the target.

    It memorises id->target for every training row (id = column 0 of X) and
    reproduces it at predict time. Under a contaminated split (test rows also
    appear in train) it reproduces the — even shuffled — test labels perfectly,
    which is exactly the boundary leakage #1906 purging is meant to prevent.
    Under a clean disjoint split it misses and falls back to the train mean.
    """

    def fit(self, X, y):
        X = np.asarray(X)
        y = np.asarray(y, dtype=float)
        self._table = {int(row[0]): float(t) for row, t in zip(X, y)}
        self._default = float(np.mean(y))
        return self

    def predict(self, X):
        X = np.asarray(X)
        return np.array([self._table.get(int(r[0]), self._default) for r in X])


def _make_xy(n=100):
    rng = np.random.default_rng(7)
    ids = np.arange(n).reshape(-1, 1)  # column 0 = unique row id
    feats = rng.normal(size=(n, 3))
    X = np.hstack([ids, feats]).astype(float)
    y = rng.normal(size=n)
    return X, y


class TestShuffleLeakage:
    def test_detects_target_peeking_under_contaminated_split(self):
        X, y = _make_xy(100)
        # contamination: test rows 50..89 also appear in the training window
        train_idx = np.arange(0, 90)
        test_idx = np.arange(50, 100)
        res = shuffle_target_leakage_test(
            _MemorizeModel, X, y, train_idx=train_idx, test_idx=test_idx, n_shuffles=5
        )
        assert isinstance(res, LeakageResult)
        assert res.leakage_detected is True
        assert res.mean_shuffled_score > res.threshold

    def test_clean_model_passes(self):
        X, y = _make_xy(100)
        res = shuffle_target_leakage_test(_MeanModel, X, y, n_shuffles=5)
        assert res.leakage_detected is False
        assert res.mean_shuffled_score <= res.threshold

    def test_same_leaky_model_passes_under_clean_split(self):
        # proves the test measures the SPLIT (leakage), not just the model:
        # a disjoint (properly held-out) split makes the memoriser harmless.
        X, y = _make_xy(100)
        train_idx = np.arange(0, 60)
        test_idx = np.arange(60, 100)
        res = shuffle_target_leakage_test(
            _MemorizeModel, X, y, train_idx=train_idx, test_idx=test_idx, n_shuffles=5
        )
        assert res.leakage_detected is False

    def test_result_fields_present(self):
        X, y = _make_xy(60)
        res = shuffle_target_leakage_test(_MeanModel, X, y, n_shuffles=4)
        assert len(res.shuffled_scores) == 4
        assert res.threshold > 0
        assert np.isfinite(res.real_score)
        assert np.isfinite(res.mean_shuffled_score)
