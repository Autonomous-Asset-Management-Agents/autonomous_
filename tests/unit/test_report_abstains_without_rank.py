# tests/unit/test_report_abstains_without_rank.py
# The report must ABSTAIN when it has no rank — not fabricate a recommendation.
#
# THE DEFECT (found by executing the real path, not by reading):
# With no cross-sectional rank, build_report_bundle produces a ~5k report with no price,
# no rank, no fundamentals, no news — and it passes the mechanical audit with ZERO flags.
# It says:
#   **HALTEN — neutral / abwarten.**                     <- a recommendation, invented
#   **Warum:** ... liegt noch keine Rangfolge ... vor.    <- admits it has nothing
#   **Wie sicher:** Mittel. Diese Rangfolge ist der ...   <- confidence in a rank that
#                                                            does not exist, 2 lines later
# and §2, the section labelled "Operative Zahl (trägt die Empfehlung)", prints literally
#   **Platz None von None** ... [src: lstm_panel cross-section @ ...]
# — raw Python None wearing a provenance tag, which makes the hole look sourced.
#
# WHY THE AUDITOR CANNOT SAVE US: audit() only proves that numbers PRESENT are sourced.
# "None" is not a number. test_report_auditor.py even pins audit('') -> integrity_ok=True.
# So a 500-report run CANNOT be gated on audit-OK: 500 empty reports ship green, and each
# reads to a retail user exactly like a real HALTEN.
#
# The rank-missing path is the DEFAULT state of the shipped build (the panel's only writer
# is LSTMDynamic; ACTIVE_STRATEGY defaults to RLAgent) — so this is not an edge case, it is
# what a user would see.
#
# The precedent this follows is our own: LSTMSignalAgent._vote_on_rank abstains (weight 0)
# when the standing is None rather than voting a confident-looking 0.5. The report must
# hold the same line: no basis -> no recommendation. Not a neutral one. None.

from __future__ import annotations

from datetime import date

import pytest

from core.report.fact_set import Fact, FactSet
from core.report.render import render


def _fact(v, src="test"):
    return Fact(v, src)


def _factset(*, xsec_pct=None, xsec_rank=None, xsec_n=None, close=None):
    """A FactSet with the rank present or absent. Everything else stays an honest gap,
    which is exactly the shipped default's shape."""
    return FactSet(
        symbol="MSFT",
        as_of=date(2026, 7, 15),
        # ADR-RPT-COMPANY-01: no company source attached — the shipped default's shape,
        # like every other gap in this fixture.
        company=_fact(None, "company feed not wired"),
        close=_fact(close, "close"),
        pullback_series=_fact([], "bars"),
        pullback_pct=_fact(None, "bars"),
        ma20=_fact(None, "bars"),
        ma50=_fact(None, "bars"),
        ma100=_fact(None, "bars"),
        ma200=_fact(None, "bars"),
        high_52w=_fact(None, "bars"),
        low_52w=_fact(None, "bars"),
        high_1m=_fact(None, "bars"),
        low_1m=_fact(None, "bars"),
        rvol_20d=_fact(None, "bars"),
        rvol_63d=_fact(None, "bars"),
        lstm_score=_fact(None, "lstm_panel"),
        lstm_own_percentile=_fact(None, "lstm_panel"),
        lstm_xsec_percentile=_fact(xsec_pct, "lstm_panel cross-section @ 2026-07-15"),
        lstm_xsec_rank=_fact(xsec_rank, "lstm_panel cross-section @ 2026-07-15"),
        lstm_xsec_n=_fact(xsec_n, "lstm_panel cross-section @ 2026-07-15"),
        lstm_score_trend=_fact([], "lstm_panel"),
        lstm_hist_min=_fact(None, "lstm_panel"),
        lstm_hist_max=_fact(None, "lstm_panel"),
        lstm_hist_mean=_fact(None, "lstm_panel"),
        lstm_hist_std=_fact(None, "lstm_panel"),
        model_card=_fact({}, "model_card"),
        fundamentals=_fact(None, "not wired"),
        news_items=_fact([], "news"),
        pros=_fact([], "cards"),
        cons=_fact([], "cards"),
    )


def _render(fs):
    return render(fs, narration=None)


# =========================================================================== #
# 1. THE defect: no rank -> no recommendation. Not a neutral one. NONE.
# =========================================================================== #
class TestNoRankMeansNoRecommendation:
    def test_no_verb_is_fabricated(self):
        """A report with no rank must not print ANY recommendation verb. Today it
        prints 'HALTEN — neutral / abwarten', indistinguishable from a real HALTEN."""
        md = _render(_factset())

        for verb in ("KAUFEN", "HALTEN", "VERKAUFEN"):
            assert verb not in md, (
                f"the report fabricated {verb!r} with no rank to base it on — a reader "
                f"cannot tell this from a real recommendation"
            )

    def test_no_confidence_is_claimed(self):
        """'Wie sicher: Mittel. Diese Rangfolge ist der einzige systematisch getestete
        Baustein' — asserted two lines after admitting there is no Rangfolge."""
        md = _render(_factset())
        # NB: assert on the LABEL, not on the bare word "Mittel" — that also means
        # "mean" in the statistics line ("Mittel n/a"). The substring trap again.
        assert "Wie sicher" not in md
        assert "einzige systematisch getestete Baustein" not in md

    def test_no_horizon_and_no_price_target_are_claimed(self):
        md = _render(_factset())
        assert "Zeithorizont" not in md

    def test_the_abstention_is_stated_explicitly(self):
        """Silence is not honesty either — the user must be TOLD there is no call."""
        md = _render(_factset())
        assert "keine Empfehlung" in md

    def test_raw_none_never_reaches_the_page(self):
        """§2 prints 'Platz None von None' today — raw Python None, with a [src:] tag
        that makes the hole look sourced."""
        md = _render(_factset())
        assert "None" not in md, "raw Python None leaked into a user-facing report"


# =========================================================================== #
# 2. The guard must not swallow the real case it exists to protect.
# =========================================================================== #
class TestRankPresentIsUnaffected:
    def test_a_ranked_stock_still_gets_its_recommendation(self):
        md = _render(_factset(xsec_pct=95.7, xsec_rank=3, xsec_n=486, close=198.45))

        assert "KAUFEN" in md
        assert "Wie sicher" in md
        assert "Platz 3 von 486" in md

    def test_a_bottom_ranked_stock_still_gets_its_recommendation(self):
        md = _render(_factset(xsec_pct=2.1, xsec_rank=476, xsec_n=486, close=198.45))

        assert "VERKAUFEN" in md
        assert "Platz 476 von 486" in md


# =========================================================================== #
# 3. The auditor must catch a future fabrication — the mechanical backstop.
#    Without this rule the next 'helpful' default reintroduces itself silently;
#    a 3594-green suite already hid three defects of exactly this shape.
# =========================================================================== #
class TestAuditorCatchesFabricatedRecommendations:
    def test_a_verb_without_a_rank_is_a_flag(self):
        """The rule: a recommendation verb may only appear when lstm_xsec_rank exists.
        This is the assertion the auditor structurally could not make before — it only
        ever checked that numbers PRESENT were sourced, and 'HALTEN' is not a number."""
        from core.report.auditor import audit

        fs = _factset()
        forged = "# MSFT\n\n**HALTEN — neutral / abwarten.**\n\nkein Beleg hier.\n"

        result = audit(forged, fs)

        assert not result.integrity_ok, (
            "a recommendation with no rank behind it must FLAG — otherwise a 500-report "
            "run cannot be gated on the audit at all"
        )

    def test_a_verb_with_a_rank_is_clean(self):
        from core.report.auditor import audit

        fs = _factset(xsec_pct=95.7, xsec_rank=3, xsec_n=486, close=198.45)
        ok = "# MSFT\n\n**KAUFEN — Übergewichten.**\n\nPlatz 3 von 486. [src: lstm_panel]\n"

        assert audit(ok, fs).integrity_ok
