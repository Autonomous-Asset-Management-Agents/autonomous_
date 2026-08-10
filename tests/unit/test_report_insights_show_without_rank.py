# tests/unit/test_report_insights_show_without_rank.py
# FACTUAL insights must show WITHOUT a rank; only RANK-DERIVED bullish framing stays gated.
#
# THE DEFECT (measured on NVDA in the real app path):
#   render._section_insights suppressed EVERY member of _BULLISH_INSIGHTS whenever the
#   cross-sectional percentile was missing or below the top quintile. That set held BOTH
#   divergence_pattern (genuinely rank-derived: "the model's rank is RISING while price
#   falls") AND valuation_vs_growth_pattern (a FACT about the company: trailing PEG < 1 on
#   a low-debt balance sheet, true regardless of any rank).
#
#   The shipped default writes an EMPTY cross-section (the panel's only writer is
#   LSTMDynamic; ACTIVE_STRATEGY defaults to RLAgent) -> lstm_xsec_percentile is None for
#   every symbol. For NVDA (PEG ~0.62, real filing) valuation_vs_growth fires, but with
#   pct=None the gate filtered it out, `insights` went empty, and _section_insights
#   returned None -> the ENTIRE "## 9. Insights" section vanished. All the insight work is
#   invisible in exactly the state a paying customer actually sees.
#
# THE DECISION (Georg): a low PEG is a fact about the firm, not a rank verdict. Factual
# insights render regardless of rank; only genuinely rank-derived bullish claims
# (divergence_pattern) stay rank-gated (anti-overselling: no "the model likes this name"
# framing on a symbol the model does not rank highly). PEG never asserts that — it says
# "the multiple is not stretched relative to growth", explicitly caveated "not to be read
# as 'cheap' or 'buy'".
#
# EVIDENCE ANCHORS (verified in this repo, origin/main ded5615):
#   - render.py:765  _BULLISH_INSIGHTS = {"divergence_pattern", "valuation_vs_growth_pattern"}
#   - render.py:774  `if pct is None or pct < _TOP_QUINTILE_PCT:` filters that whole set
#   - insight_patterns.py:332 valuation reads ONLY fundamentals (pe / revenue_yoy / d-to-e)
#   - insight_patterns.py:240 divergence reads lstm_score_trend (the rank signal)
#   - lstm_panel_store.py:192 cross_section_standing -> (None,None,None) on an empty panel,
#     while lstm_score_trend comes from the INDEPENDENT per-symbol series -> divergence CAN
#     fire with pct=None, so its gate is load-bearing, NOT redundant.
#
# Fully deterministic: no network / LLM / GPU / pandas.

from __future__ import annotations

from datetime import date

from core.report.fact_set import Fact, FactSet
from core.report.render import render


def _f(v, src="test"):
    return Fact(v, src)


# PEG < 1 on a low-debt balance sheet -> valuation_vs_growth_pattern fires. Mirrors the
# committed real NVDA filing (P/E 40.5 on +65.5% growth -> trailing PEG 0.62; d/e 0.31).
_PEG_FUND = {
    "revenue_yoy_pct": 65.5,
    "net_margin_pct": 55.6,
    "debt_to_equity": 0.31,
    "pe": 40.5,
}

# Fundamentals INTACT for divergence (revenue growing) but pe absent -> valuation stays
# silent, so a test can isolate the divergence gate without valuation also firing.
_INTACT_NO_PEG_FUND = {
    "revenue_yoy_pct": 65.5,
    "net_margin_pct": 55.6,
    "debt_to_equity": 0.31,
    "pe": None,
}

# A monotonically rising model score -> divergence's score_rising precondition is met.
_RISING_TREND = [1.55, 2.10, 3.05, 4.10]


def _factset(
    *,
    xsec_pct=None,
    xsec_rank=None,
    xsec_n=None,
    close=None,
    ma20=None,
    ma50=None,
    ma100=None,
    ma200=None,
    pullback_pct=None,
    score_trend=None,
    fundamentals=None,
):
    """A FactSet with only the fields a given detector needs populated; everything
    else is an honest gap. Same construction style as test_report_abstains_without_rank.
    """
    return FactSet(
        symbol="NVDA",
        as_of=date(2026, 5, 1),
        company=_f(None, "company feed not wired"),
        close=_f(close, "close"),
        pullback_series=_f([], "bars"),
        pullback_pct=_f(pullback_pct, "bars"),
        ma20=_f(ma20, "bars"),
        ma50=_f(ma50, "bars"),
        ma100=_f(ma100, "bars"),
        ma200=_f(ma200, "bars"),
        high_52w=_f(None, "bars"),
        low_52w=_f(None, "bars"),
        high_1m=_f(None, "bars"),
        low_1m=_f(None, "bars"),
        rvol_20d=_f(None, "bars"),
        rvol_63d=_f(None, "bars"),
        lstm_score=_f(None, "lstm_panel"),
        lstm_own_percentile=_f(None, "lstm_panel"),
        lstm_xsec_percentile=_f(xsec_pct, "lstm_panel cross-section @ 2026-05-01"),
        lstm_xsec_rank=_f(xsec_rank, "lstm_panel cross-section @ 2026-05-01"),
        lstm_xsec_n=_f(xsec_n, "lstm_panel cross-section @ 2026-05-01"),
        lstm_score_trend=_f(
            score_trend if score_trend is not None else [], "lstm_panel"
        ),
        lstm_hist_min=_f(None, "lstm_panel"),
        lstm_hist_max=_f(None, "lstm_panel"),
        lstm_hist_mean=_f(None, "lstm_panel"),
        lstm_hist_std=_f(None, "lstm_panel"),
        model_card=_f({}, "model_card"),
        fundamentals=_f(fundamentals, "polygon vX financials"),
        news_items=_f([], "news"),
        pros=_f([], "cards"),
        cons=_f([], "cards"),
    )


# =========================================================================== #
# 1. THE defect: a FACTUAL insight (PEG) must render even with NO rank.
# =========================================================================== #
class TestFactualInsightShowsWithoutRank:
    def test_peg_insight_renders_when_percentile_is_none(self):
        """valuation_vs_growth is the ONLY firing insight here, and there is no rank.
        On origin/main the gate filtered it, `insights` went empty, and the whole
        section returned None. After the fix the section renders with the PEG insight.
        """
        md = render(_factset(xsec_pct=None, fundamentals=_PEG_FUND))

        assert "## 9. Insights" in md, (
            "the Insights section vanished entirely with no rank — the exact NVDA defect: "
            "a factual PEG insight was suppressed as if it were a rank verdict"
        )
        section = md.split("## 9. Insights")[1]
        assert "valuation_vs_growth_pattern" in section
        assert "PEG" in section
        assert "gestreckt" in section  # the valuation implication ("nicht gestreckt")

    def test_peg_insight_renders_at_a_mediocre_rank_too(self):
        """Not just None — a mid/low percentile must also keep the FACTUAL insight."""
        md = render(
            _factset(xsec_pct=42.0, xsec_rank=250, xsec_n=486, fundamentals=_PEG_FUND)
        )
        assert "## 9. Insights" in md
        assert "valuation_vs_growth_pattern" in md.split("## 9. Insights")[1]


# =========================================================================== #
# 2. Anti-overselling is PRESERVED: a rank-derived bullish claim stays gated.
# =========================================================================== #
class TestRankDerivedInsightStaysGated:
    def _divergence_fixture(self, xsec_pct, xsec_rank):
        # divergence raw condition is met (rising score + price down + intact
        # fundamentals). A below-all-MAs stack fires the FACTUAL trend_regime insight,
        # so the section RENDERS regardless — proving divergence is dropped by the GATE,
        # not merely absent because the section is empty. pe=None keeps valuation silent.
        return _factset(
            xsec_pct=xsec_pct,
            xsec_rank=xsec_rank,
            xsec_n=486,
            close=90.0,
            ma20=100.0,
            ma50=105.0,
            ma100=110.0,
            ma200=120.0,
            pullback_pct=-8.4,
            score_trend=_RISING_TREND,
            fundamentals=_INTACT_NO_PEG_FUND,
        )

    def test_divergence_is_suppressed_at_a_low_rank(self):
        md = render(self._divergence_fixture(xsec_pct=15.0, xsec_rank=430))
        # the section renders (trend_regime is factual) ...
        assert "## 9. Insights" in md
        section = md.split("## 9. Insights")[1]
        assert "trend_regime_pattern" in section
        # ... but the rank-derived bullish claim is gated out.
        assert "divergence_pattern" not in section
        assert "positionierungsgetrieben" not in md

    def test_divergence_is_suppressed_when_rank_is_missing(self):
        md = render(self._divergence_fixture(xsec_pct=None, xsec_rank=None))
        section = md.split("## 9. Insights")[1]
        assert "divergence_pattern" not in section
        assert "positionierungsgetrieben" not in md

    def test_divergence_DOES_appear_at_top_quintile_same_data(self):
        """Paired proof: identical data, only the rank differs -> the GATE is what
        suppressed it below, not a missing precondition."""
        md = render(self._divergence_fixture(xsec_pct=95.7, xsec_rank=3))
        section = md.split("## 9. Insights")[1]
        assert "divergence_pattern" in section
        assert "positionierungsgetrieben" in md


# =========================================================================== #
# 3. Top-quintile regression: everything shows as before (no regression).
# =========================================================================== #
class TestTopQuintileUnaffected:
    def test_factual_and_rank_derived_insights_both_show_at_top_rank(self):
        md = render(
            _factset(
                xsec_pct=95.7,
                xsec_rank=3,
                xsec_n=486,
                close=90.0,
                ma20=100.0,
                ma50=105.0,
                ma100=110.0,
                ma200=120.0,
                pullback_pct=-8.4,
                score_trend=_RISING_TREND,
                fundamentals=_PEG_FUND,
            )
        )
        section = md.split("## 9. Insights")[1]
        assert "valuation_vs_growth_pattern" in section  # factual
        assert "divergence_pattern" in section  # rank-derived, allowed at top quintile
        assert "positionierungsgetrieben" in md
        assert "PEG" in section
