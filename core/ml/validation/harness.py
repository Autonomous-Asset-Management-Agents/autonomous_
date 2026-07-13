"""
harness.py — canonical purged + embargoed walk-forward / CPCV validation.

Issue #1906 (MLR-6): today each model family validates differently —
RL evaluates on its own training distribution (in-sample EvalCallback),
LSTM splits time-based but without an embargo at the train/val boundary,
and there is no shared, leakage-safe splitter anywhere in the tree
(`core/validation.py` is a plain expanding-window split; the only
purged/embargoed CV is a script-local helper inside
`scripts/train_v4_lightgbm.py::purged_cv_splits`).

This module is the *single* validation library the trainers will adopt in
follow-up PRs. It implements the leakage controls from López de Prado,
*Advances in Financial Machine Learning*, Ch. 7:

  * **Purging** — drop training samples whose label window overlaps the
    test window (`purge_overlapping_labels`).
  * **Embargo** — additionally drop training samples in a buffer immediately
    after each test block, to break forward serial correlation
    (`apply_embargo`).
  * **Purged walk-forward** — expanding-window, chronological folds with
    purge + embargo applied at every boundary (`purged_walk_forward`).
  * **CPCV** — Combinatorial Purged Cross-Validation, C(n_groups,
    n_test_groups) folds, each purged + embargoed (`combinatorial_purged_cv`).

It also provides `shuffle_target_leakage_test`, a permutation sanity check
that is the basis for the #1906 CI gate: a leakage-free model must find no
signal on shuffled targets.

DORMANT: nothing imports this module yet. Wiring RL/LSTM/TFT onto it is done
in the follow-up PRs cut in the implementation plan for #1906.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Callable, Iterator, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Fold container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fold:
    """One train/test split produced by the harness.

    Attributes
    ----------
    train_idx, test_idx:
        Integer position arrays (0..n_samples-1), already purged + embargoed.
    fold_id:
        Sequential id of the fold within the generator.
    test_groups:
        For CPCV, the tuple of group indices used as the test set; ``None``
        for plain walk-forward folds.
    """

    train_idx: np.ndarray
    test_idx: np.ndarray
    fold_id: int
    test_groups: Optional[Tuple[int, ...]] = None


# ---------------------------------------------------------------------------
# Low-level primitives: purge + embargo
# ---------------------------------------------------------------------------


def _contiguous_blocks(sorted_idx: np.ndarray) -> List[Tuple[int, int]]:
    """Split a sorted index array into (lo, hi) inclusive contiguous runs."""
    blocks: List[Tuple[int, int]] = []
    if sorted_idx.size == 0:
        return blocks
    start = prev = int(sorted_idx[0])
    for value in sorted_idx[1:]:
        value = int(value)
        if value == prev + 1:
            prev = value
        else:
            blocks.append((start, prev))
            start = prev = value
    blocks.append((start, prev))
    return blocks


def purge_overlapping_labels(
    train_idx: np.ndarray, test_idx: np.ndarray, label_horizon: int
) -> np.ndarray:
    """Remove training samples whose label window overlaps the test window.

    A sample at position ``i`` carries a label that depends on bars
    ``[i, i + label_horizon]``. It leaks if that window intersects any test
    sample's window ``[j, j + label_horizon]``. Purging is bidirectional, so
    it is correct for both walk-forward (test after train) and CPCV
    (test blocks in the middle).

    Parameters
    ----------
    train_idx, test_idx:
        Integer position arrays.
    label_horizon:
        Number of forward bars each label depends on. ``0`` => point labels,
        nothing to purge.

    Returns
    -------
    np.ndarray
        The purged (sorted) training indices.
    """
    train = np.sort(np.asarray(train_idx).astype(int))
    test = np.sort(np.asarray(test_idx).astype(int))
    if label_horizon <= 0 or train.size == 0 or test.size == 0:
        return train

    keep = np.ones(train.size, dtype=bool)
    for lo, hi in _contiguous_blocks(test):
        # train sample s (window [s, s+h]) overlaps block [lo, hi+h] iff
        # lo - h <= s <= hi + h
        purge_lo = lo - label_horizon
        purge_hi = hi + label_horizon
        keep &= ~((train >= purge_lo) & (train <= purge_hi))
    return train[keep]


def apply_embargo(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    embargo: float,
    n_samples: Optional[int] = None,
) -> np.ndarray:
    """Drop training samples in the embargo buffer after each test block.

    The embargo removes the ``embargo`` samples immediately *after* each
    contiguous test block (forward serial-correlation guard). ``embargo`` may
    be an integer number of bars, or a float in ``(0, 1)`` interpreted as a
    fraction of ``n_samples``.
    """
    train = np.sort(np.asarray(train_idx).astype(int))
    test = np.sort(np.asarray(test_idx).astype(int))
    if train.size == 0 or test.size == 0:
        return train

    if isinstance(embargo, float) and 0.0 < embargo < 1.0:
        if n_samples is None:
            n_samples = int(max(int(train.max()), int(test.max())) + 1)
        embargo_bars = int(np.ceil(embargo * n_samples))
    else:
        embargo_bars = int(embargo)
    if embargo_bars <= 0:
        return train

    keep = np.ones(train.size, dtype=bool)
    for _, hi in _contiguous_blocks(test):
        keep &= ~((train > hi) & (train <= hi + embargo_bars))
    return train[keep]


def _clean_train(
    raw_train: np.ndarray,
    test_idx: np.ndarray,
    label_horizon: int,
    embargo: float,
    n_samples: int,
) -> np.ndarray:
    """Apply purge then embargo — the boundary cleaning used by every fold."""
    train = purge_overlapping_labels(raw_train, test_idx, label_horizon)
    train = apply_embargo(train, test_idx, embargo, n_samples)
    return train


# ---------------------------------------------------------------------------
# Purged walk-forward
# ---------------------------------------------------------------------------


def purged_walk_forward(
    n_samples: int,
    *,
    n_splits: int = 5,
    embargo: float = 0.0,
    label_horizon: int = 0,
    min_train_size: int = 1,
    train_window: Optional[int] = None,
) -> Iterator[Fold]:
    """Yield chronological, purged + embargoed walk-forward folds.

    The series is cut into ``n_splits + 1`` contiguous blocks; block ``i+1`` is
    the test set for fold ``i`` and everything before it is the (expanding)
    training window, cleaned by purge + embargo. Pass ``train_window`` to use a
    fixed rolling window instead of an expanding one.

    Parameters
    ----------
    n_samples:
        Total number of ordered samples.
    n_splits:
        Number of out-of-sample folds.
    embargo:
        Embargo size (int bars or fraction of ``n_samples``).
    label_horizon:
        Forward bars each label depends on (drives purging).
    min_train_size:
        Folds whose cleaned training set is smaller than this are skipped.
    train_window:
        If set, rolling window of this many samples; otherwise expanding.

    Yields
    ------
    Fold
    """
    if n_samples < n_splits + 1:
        raise ValueError(
            f"n_samples={n_samples} too small for n_splits={n_splits} "
            "(need at least n_splits + 1)."
        )

    fold_size = n_samples // (n_splits + 1)
    for i in range(n_splits):
        test_start = fold_size * (i + 1)
        test_end = n_samples if i == n_splits - 1 else test_start + fold_size
        test_idx = np.arange(test_start, test_end)

        train_start = 0 if train_window is None else max(0, test_start - train_window)
        raw_train = np.arange(train_start, test_start)

        train_idx = _clean_train(raw_train, test_idx, label_horizon, embargo, n_samples)
        if train_idx.size < min_train_size:
            continue
        yield Fold(
            train_idx=train_idx,
            test_idx=test_idx,
            fold_id=i,
            test_groups=None,
        )


# ---------------------------------------------------------------------------
# Combinatorial Purged Cross-Validation (CPCV)
# ---------------------------------------------------------------------------


def combinatorial_purged_cv(
    n_samples: int,
    *,
    n_groups: int = 6,
    n_test_groups: int = 2,
    embargo: float = 0.0,
    label_horizon: int = 0,
) -> Iterator[Fold]:
    """Yield Combinatorial Purged CV folds (López de Prado, Ch. 12).

    The series is partitioned into ``n_groups`` contiguous groups. Every
    combination of ``n_test_groups`` groups becomes a test set (C(n_groups,
    n_test_groups) folds); the remaining groups form the training set, cleaned
    with purge + embargo around each contiguous test block.

    Yields
    ------
    Fold
        ``fold.test_groups`` records which group indices were held out.
    """
    if n_test_groups < 1 or n_test_groups >= n_groups:
        raise ValueError(
            f"n_test_groups must be in [1, n_groups-1]; got "
            f"n_test_groups={n_test_groups}, n_groups={n_groups}."
        )
    if n_groups > n_samples:
        raise ValueError(f"n_groups={n_groups} cannot exceed n_samples={n_samples}.")

    bounds = np.linspace(0, n_samples, n_groups + 1).astype(int)
    groups = [np.arange(bounds[g], bounds[g + 1]) for g in range(n_groups)]

    fold_id = 0
    for combo in itertools.combinations(range(n_groups), n_test_groups):
        test_idx = np.sort(np.concatenate([groups[g] for g in combo]))
        train_groups = [g for g in range(n_groups) if g not in combo]
        if train_groups:
            raw_train = np.sort(np.concatenate([groups[g] for g in train_groups]))
        else:
            raw_train = np.array([], dtype=int)

        train_idx = _clean_train(raw_train, test_idx, label_horizon, embargo, n_samples)
        yield Fold(
            train_idx=train_idx,
            test_idx=test_idx,
            fold_id=fold_id,
            test_groups=tuple(combo),
        )
        fold_id += 1


# ---------------------------------------------------------------------------
# Shuffle-target leakage test (basis for the CI gate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeakageResult:
    """Outcome of :func:`shuffle_target_leakage_test`.

    Attributes
    ----------
    real_score:
        Metric on the true (unshuffled) target — for reference only.
    shuffled_scores:
        Metric for each label permutation.
    mean_shuffled_score:
        Mean of ``shuffled_scores``; the quantity the gate thresholds.
    threshold:
        Decision threshold used.
    leakage_detected:
        ``True`` when the model scores above ``threshold`` on shuffled targets,
        i.e. it recovers signal from randomised labels — a leak.
    """

    real_score: float
    shuffled_scores: List[float]
    mean_shuffled_score: float
    threshold: float
    leakage_detected: bool


def _abs_pearson(pred: np.ndarray, true: np.ndarray) -> float:
    """Absolute Pearson correlation; 0.0 for degenerate (constant) inputs."""
    pred = np.asarray(pred, dtype=float)
    true = np.asarray(true, dtype=float)
    if pred.size < 2 or np.std(pred) == 0.0 or np.std(true) == 0.0:
        return 0.0
    corr = np.corrcoef(pred, true)[0, 1]
    return float(abs(corr)) if np.isfinite(corr) else 0.0


def shuffle_target_leakage_test(
    model_factory: Callable[[], object],
    X: np.ndarray,
    y: np.ndarray,
    *,
    train_idx: Optional[np.ndarray] = None,
    test_idx: Optional[np.ndarray] = None,
    n_shuffles: int = 10,
    test_size: float = 0.3,
    metric: Optional[Callable[[np.ndarray, np.ndarray], float]] = None,
    threshold: float = 0.2,
    random_state: int = 0,
) -> LeakageResult:
    """Permutation sanity check: a leak-free model finds no signal on shuffled y.

    The model (built fresh from ``model_factory`` each fit, must expose sklearn
    ``fit``/``predict``) is trained on ``train_idx`` and scored on ``test_idx``.
    We then permute the labels ``n_shuffles`` times and repeat: a model that
    still scores above ``threshold`` on randomised labels is exploiting
    leakage (e.g. train/test contamination from a missing purge). If no split
    is given, a chronological ``1 - test_size`` / ``test_size`` split is used.

    Returns
    -------
    LeakageResult
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float).ravel()
    n = y.shape[0]

    if train_idx is None or test_idx is None:
        split = int(round(n * (1.0 - test_size)))
        train_idx = np.arange(0, split)
        test_idx = np.arange(split, n)
    train_idx = np.asarray(train_idx).astype(int)
    test_idx = np.asarray(test_idx).astype(int)

    score_fn = metric if metric is not None else _abs_pearson

    def _fit_score(y_target: np.ndarray) -> float:
        model = model_factory()
        model.fit(X[train_idx], y_target[train_idx])
        pred = np.asarray(model.predict(X[test_idx])).ravel()
        return score_fn(pred, y_target[test_idx])

    real_score = _fit_score(y)

    rng = np.random.default_rng(random_state)
    shuffled_scores: List[float] = []
    for _ in range(n_shuffles):
        y_perm = y.copy()
        rng.shuffle(y_perm)
        shuffled_scores.append(_fit_score(y_perm))

    mean_shuffled = float(np.mean(shuffled_scores)) if shuffled_scores else 0.0
    return LeakageResult(
        real_score=float(real_score),
        shuffled_scores=shuffled_scores,
        mean_shuffled_score=mean_shuffled,
        threshold=threshold,
        leakage_detected=bool(mean_shuffled > threshold),
    )
