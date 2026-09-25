# tests/unit/test_report_insight_patterns_hardening.py
# RPT-7 (Epic #1998) — hardening of the deterministic insight detectors AFTER the
# #2202 de-fabrication. #2202 removed the hard-coded NVDA prose; this suite pins
# the three residual defects the audit is structurally blind to:
#
#   1. KEYWORD OVER-BREADTH (cross-contamination): "ipo" made a SpaceX-IPO headline
#      look like a competitive structural threat on EVERY symbol; "rally"/"snap"
#      made a bullish market wrap look like a selloff catalyst. Removed.
#   2. DIVERGENCE — the "sentiment-driven selloff" claim was made UNCONDITIONALLY,
#      from a detector that fired identically on a FactSet with zero news. The
#      sentiment half of the claim is now gated on real sentiment headlines and
#      QUOTES them; the positioning read-across (supported by the three numbers
#      alone) stays.
#   3. HORIZON — structural[0] and the catalyst could be the SAME headline
#      ("Rival's earnings selloff"), collapsing the whole horizon-separation thesis
#      into one quote on both sides. Deduped. And the realized-vol label was
#      inverted: rvol63 > rvol20 means SHORT-term vol is LOWER, not "(erhoeht)".
#
# Every fixture is a directly-constructed FactSet so each condition is isolated;
# nothing here touches network / LLM / GPU / pandas.

from datetime import date

from core.report.fact_set import Fact, FactSet, NewsItem
from core.report.insight_patterns import detect_insights

_SRC = "src:test"


def _news(title, url):
    return NewsItem(
        title=title,
        published_utc="2026-04-22T12:00:00+00:00",
        publisher="Reuters",
        url=url,
    )


def _mk_fs(**over):
    """A FactSet whose defaults fire divergence + valuation; news drives horizon.

    Defaults: rising score-trend (1.0 -> 3.0), pullback -5%, revenue +65.5% YoY,
    PEG 40.5/65.5 = 0.62 on debt/equity 0.31, model horizon 5d, and NO news.
    Override any field with its raw (unwrapped) value.
    """
    ident_symbol = over.pop("symbol", "TST")
    ident_as_of = over.pop("as_of", date(2026, 5, 1))
    fields = dict(
        company={},
        close=100.0,
        pullback_series=[100.0, 99.0, 95.0],
        pullback_pct=-5.0,
        ma20=None,
        ma50=None,
        ma100=None,
        ma200=None,
        high_52w=None,
        low_52w=None,
        high_1m=None,
        low_1m=None,
        rvol_20d=0.30,
        rvol_63d=0.30,
        lstm_score=3.0,
        lstm_own_percentile=None,
        lstm_xsec_percentile=90.0,
        lstm_xsec_rank=5,
        lstm_xsec_n=500,
        lstm_score_trend=[1.0, 3.0],
        lstm_hist_min=None,
        lstm_hist_max=None,
        lstm_hist_mean=None,
        lstm_hist_std=None,
        model_card={"horizon_days": 5},
        fundamentals={
            "revenue_yoy_pct": 65.5,
            "net_margin_pct": 55.6,
            "pe": 40.5,
            "debt_to_equity": 0.31,
        },
        news_items=[],
        pros=[],
        cons=[],
    )
    fields.update(over)
    wrapped = {k: Fact(v, _SRC) for k, v in fields.items()}
    return FactSet(symbol=ident_symbol, as_of=ident_as_of, **wrapped)


def _by_name(fs):
    return {i.pattern_name: i for i in detect_insights(fs)}


# ---------------------------------------------------------------------------
# 1. Keyword over-breadth — cross-contamination removed.
# ---------------------------------------------------------------------------
def test_ipo_headline_no_longer_matches_a_structural_threat():
    # A SpaceX IPO article is not a competitive threat to this symbol. With "ipo"
    # gone from the structural classifier there is no long-horizon structural leg,
    # so horizon_separation cannot fire on it — even though a real event exists.
    news = [
        _news("SpaceX Eyes Record IPO Valuation Next Year", "https://ex.com/ipo"),
        _news("Company Reports First-Quarter Financial Results", "https://ex.com/res"),
    ]
    names = [i.pattern_name for i in detect_insights(_mk_fs(news_items=news))]
    assert "horizon_separation_pattern" not in names


def test_rally_and_snap_are_not_selloff_sentiment():
    # A bullish "rally / snap a losing streak" wrap is not a selloff driver. With
    # those words gone from the sentiment classifier, divergence makes NO sentiment
    # claim and attaches no selloff_type grounding for it.
    news = [
        _news("Chip Stocks Rally as Names Snap a Losing Streak", "https://ex.com/r")
    ]
    div = _by_name(_mk_fs(news_items=news))["divergence_pattern"]
    assert "selloff_type" not in div.grounding
    assert "stimmungsgetrieben" not in div.implication.lower()


# ---------------------------------------------------------------------------
# 2. Divergence — the sentiment claim is conditional and quoted, not unconditional.
# ---------------------------------------------------------------------------
def test_divergence_without_sentiment_makes_no_sentiment_claim():
    # THE residual fabrication in detector 1: the implication called the pullback
    # "sentiment-driven" even with zero news. The positioning read-across (from the
    # three numbers) stays; the sentiment half is dropped when unbacked.
    div = _by_name(_mk_fs(news_items=[]))["divergence_pattern"]
    impl = div.implication.lower()
    assert "positionierungsgetrieben" in impl  # supported by rank/price/fundamentals
    assert "stimmungsgetrieben" not in impl  # unbacked -> absent
    assert "selloff_type" not in div.grounding


def test_divergence_with_sentiment_quotes_the_real_headline():
    title = "AI Chip Stocks Are Swooning on Selloff Fears"
    div = _by_name(_mk_fs(news_items=[_news(title, "https://ex.com/s")]))[
        "divergence_pattern"
    ]
    assert "stimmungsgetrieben" in div.implication.lower()
    assert title in div.implication  # verbatim, not a canned summary
    assert "selloff_type" in div.grounding
    assert title in div.grounding["selloff_type"].value


# ---------------------------------------------------------------------------
# 3a. Horizon — dedup: one headline cannot be both sides of the separation.
# ---------------------------------------------------------------------------
def test_horizon_does_not_fire_when_one_headline_is_both_sides():
    # "Rival's earnings selloff" matches structural (rival) AND event (earnings) AND
    # sentiment (selloff). With no SECOND, distinct catalyst there is no separation
    # to state, so the detector stays silent instead of quoting one headline twice.
    news = [_news("Rival's Earnings Trigger a Sector-Wide Selloff", "https://ex.com/x")]
    names = [i.pattern_name for i in detect_insights(_mk_fs(news_items=news))]
    assert "horizon_separation_pattern" not in names


def test_horizon_quotes_two_distinct_headlines():
    struct = _news("Rivals Gain Market Share in Custom Silicon", "https://ex.com/a")
    cat = _news(
        "Company Sets Conference Call for Financial Results", "https://ex.com/b"
    )
    hor = _by_name(_mk_fs(news_items=[struct, cat]))["horizon_separation_pattern"]
    sl = hor.grounding["structural_long"].value
    sc = hor.grounding["short_catalyst"].value
    assert struct.title in sl and cat.title in sc
    assert sl != sc
    assert struct.title in hor.implication and cat.title in hor.implication


# ---------------------------------------------------------------------------
# 3b. Horizon — the realized-vol label is no longer inverted.
# ---------------------------------------------------------------------------
def _horizon_with_vol(rvol20, rvol63):
    struct = _news("Rivals Gain Market Share in Custom Silicon", "https://ex.com/a")
    cat = _news(
        "Company Sets Conference Call for Financial Results", "https://ex.com/b"
    )
    fs = _mk_fs(news_items=[struct, cat], rvol_20d=rvol20, rvol_63d=rvol63)
    return _by_name(fs)["horizon_separation_pattern"]


def test_vol_label_reads_short_term_lower_when_rvol20_below_rvol63():
    hor = _horizon_with_vol(rvol20=0.30, rvol63=0.40)
    assert "(Kurzfrist-Vol niedriger)" in hor.grounding["volatility"].value


def test_vol_label_reads_short_term_elevated_when_rvol20_above_rvol63():
    hor = _horizon_with_vol(rvol20=0.45, rvol63=0.30)
    assert "(Kurzfrist-Vol erhoeht)" in hor.grounding["volatility"].value


# ---------------------------------------------------------------------------
# 4. No hard-coded company names leak into a generic symbol's insights.
# ---------------------------------------------------------------------------
def test_no_hardcoded_company_names_for_a_generic_symbol():
    struct = _news("Rivals Gain Market Share in Custom Silicon", "https://ex.com/a")
    cat = _news(
        "Company Sets Conference Call for Financial Results", "https://ex.com/b"
    )
    fs = _mk_fs(news_items=[struct, cat])
    blob = " ".join(
        ins.implication
        + " "
        + ins.caveat
        + " "
        + " ".join(g.value for g in ins.grounding.values())
        for ins in detect_insights(fs)
    )
    for ghost in ("Cerebras", "Broadcom", "Burry", "OpenAI", "Custom-Chip"):
        assert (
            ghost not in blob
        ), f"{ghost!r} fabricated into a generic symbol's insight"
