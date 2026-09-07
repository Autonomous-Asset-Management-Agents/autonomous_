# tests/unit/test_model_card_honesty.py
# ADR-CARD-01 — the model card contradicts itself, and it is surfaced VERBATIM in every
# FactSet (its own _doc says so: "the audited, single-sourced model-reliability facts
# surfaced verbatim in every FactSet").
#
# MEASURED, before the fix:
#   _doc        : "Static, code-fixed universe WALK-FORWARD constants ... One WALK-FORWARD
#                  evaluation produced them; see provenance."
#   split       : "random_80_20"
#   provenance  : "corrected_benchmark_results.json / corrected_benchmark_NOTES.md
#                  (single WALK-FORWARD eval, session artifact)"
#   leakage_caveat: "v1 uses a random 80/20 train/test split ..."   <- honest, and it
#                  contradicts _doc and provenance in the same file.
#
# A random 80/20 split is NOT a walk-forward evaluation. The card asserts "walk-forward"
# twice while its own `split` field says otherwise — and the honest caveat sits right
# beside the claim it refutes.
#
# AND THE PROVENANCE POINTS AT NOTHING: neither corrected_benchmark_results.json nor
# corrected_benchmark_NOTES.md exists in the working tree OR in ANY commit in the
# repository's history (verified with git log --all --diff-filter=A). The numbers cannot
# be regenerated or checked from this repo.
#
# This is not a modelling weakness. It is a false provenance claim on the artifact we sell
# as auditable, and it reaches the reader unmediated. The mechanical auditor cannot catch
# it: it verifies that numbers trace to a FactSet value, and these ARE FactSet values —
# it never asks whether the card's description of its own method is true.
#
# THE FIX IS THE DESCRIPTION, NOT THE NUMBERS. Nobody can re-run the benchmark here. What
# we can do is stop describing a random split as a walk-forward, and stop citing files
# that never existed.

from __future__ import annotations

import json
from pathlib import Path

import pytest

CARD = Path(__file__).resolve().parents[2] / "core" / "report" / "model_card.json"


@pytest.fixture(scope="module")
def card():
    return json.loads(CARD.read_text(encoding="utf-8"))


class TestTheCardDoesNotContradictItself:
    def test_a_random_split_is_not_called_a_walk_forward(self, card):
        """The single defect this file exists for. `split` is the ground truth; the prose
        must not assert a method the split field denies."""
        if card.get("split", "").startswith("random"):
            for field in ("_doc", "provenance"):
                assert "walk-forward" not in card.get(field, "").lower(), (
                    f"{field!r} calls this a walk-forward evaluation while split="
                    f"{card['split']!r}. A random split is not a walk-forward, and this "
                    f"text is surfaced verbatim in every report."
                )

    def test_the_split_is_still_disclosed(self, card):
        """The honest field must not be quietly dropped to resolve the contradiction."""
        assert card.get("split"), "the split field disappeared — that is the wrong fix"

    def test_the_leakage_caveat_survives(self, card):
        """It was already honest. Removing it to make the card look tidier would be a
        regression, not a cleanup."""
        caveat = card.get("leakage_caveat", "").lower()
        assert "random" in caveat and "80/20" in caveat
        assert "optimistically biased" in caveat


class TestTheProvenanceIsCheckable:
    """A provenance that points at files which never existed is worse than none: it
    invites the reader to believe the numbers were checked."""

    def test_cited_artifacts_exist_or_are_not_cited_as_artifacts(self, card):
        import re

        prov = card.get("provenance", "")
        repo = CARD.resolve().parents[2]
        cited = re.findall(r"[\w./-]+\.(?:json|md|csv|py|ipynb)", prov)
        missing = [c for c in cited if not list(repo.rglob(Path(c).name))]
        assert not missing, (
            f"provenance cites {missing}, which exist nowhere in the tree. Verified "
            f"separately: they exist in no commit either. Cite what exists, or state "
            f"plainly that the source artifact was not retained."
        )

    def test_the_provenance_says_something(self, card):
        assert card.get("provenance", "").strip(), "provenance must not be blank"


class TestTheNumbersAreUntouched:
    """⚠ This PR fixes the DESCRIPTION, not the measurements. Nobody re-ran the benchmark.
    Silently 'improving' a number here would be the exact dishonesty being removed."""

    @pytest.mark.parametrize(
        "field,expected",
        [
            ("model", "v1_cross_sectional_lstm"),
            ("horizon_days", 5),
            ("cs_ic_mean", 0.067),
            ("cs_ic_tstat", 20.4),
            ("sharpe_topq_gross", 1.53),
            ("sharpe_topq_net_10bps", 0.758),
            ("sharpe_equalweight_gross", 0.97),
            ("sharpe_equalweight_net_10bps", 0.944),
            ("sharpe_buyhold", 1.09),
            ("turnover_daily", 0.5),
            ("cost_bps", 10),
            ("rebalance", "daily"),
            ("universe_symbols", 486),
        ],
    )
    def test_measurement_unchanged(self, card, field, expected):
        assert card[field] == expected, (
            f"{field} moved. The measurements are NOT this change's business — if a "
            f"number legitimately changed, it needs its own PR with a re-run behind it."
        )
