# tests/unit/test_report_insight_coverage.py
# RPT-7 (Epic #1998) — COVERAGE detectors. After the #2202/#2214 de-fabrication the
# per-symbol news/fundamentals detectors are HONEST but THIN: a quiet symbol with no
# divergence, no PEG case and no news mix gets almost nothing. These four detectors
# derive additional, TRUE insights from FactSet fields that almost every symbol has
# (the MA stack, the two realized-vol windows, the score trajectory, the own-history
# percentile) so nearly every symbol has >= 1 grounded thing to say — WITHOUT ever
# fabricating one.
#
# Iron rule (why RPT-7 exists): a detector fires ONLY when its pattern is truly in
# THIS symbol's data; a flat / ambiguous input yields NO insight. So every detector
# below has BOTH surfaces: (a) a FactSet that MEETS the pattern -> fires with the real
# numbers; (b) a FactSet that does NOT -> stays silent. Directly-constructed FactSets
# isolate each condition; nothing here touches network / LLM / GPU / pandas.

from datetime import date

from core.report.fact_set import Fact, FactSet
from core.report.insight_patterns import detect_insights

_SRC = "src:test"


def _mk_fs(**over):
    """A neutral FactSet (fires NOTHING by default). Override raw field values.

    Defaults: no MAs, flat vol, a flat 3-point score trend, mid own-percentile,
    no fundamentals, no news -> none of the four coverage detectors fire. Each
    test flips exactly the fields its pattern needs.
    """
    ident_symbol = over.pop("symbol", "TST")
    ident_as_of = over.pop("as_of", date(2026, 5, 1))
    fields = dict(
        company={},
        close=100.0,
        pullback_series=[100.0, 100.0, 100.0],
        pullback_pct=0.0,
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
        lstm_score=1.0,
        lstm_own_percentile=50.0,
        lstm_xsec_percentile=50.0,
        lstm_xsec_rank=250,
        lstm_xsec_n=500,
        lstm_score_trend=[1.0, 1.0, 1.0],
        lstm_hist_min=None,
        lstm_hist_max=None,
        lstm_hist_mean=None,
        lstm_hist_std=None,
        model_card={"horizon_days": 5},
        fundamentals=None,
        news_items=[],
        pros=[],
        cons=[],
    )
    fields.update(over)
    wrapped = {k: Fact(v, _SRC) for k, v in fields.items()}
    return FactSet(symbol=ident_symbol, as_of=ident_as_of, **wrapped)


def _by_name(fs):
    return {i.pattern_name: i for i in detect_insights(fs)}


def _neutral_defaults_fire_nothing():
    # Guard: the shared default FactSet must be inert, else a "does not fire" test
    # could pass for the wrong reason.
    return detect_insights(_mk_fs()) == []


def test_neutral_factset_yields_no_coverage_insight():
    assert _neutral_defaults_fire_nothing()


# ---------------------------------------------------------------------------
# DETECTOR 4 — trend_regime_pattern.
# ---------------------------------------------------------------------------
def test_trend_regime_fires_uptrend_when_price_above_all_four_mas():
    fs = _mk_fs(close=185.0, ma20=178.0, ma50=172.0, ma100=168.0, ma200=160.0)
    ins = _by_name(fs)["trend_regime_pattern"]
    assert "Aufwaertstrend" in ins.implication
    # grounding quotes the real close + MA numbers, sourced from the FactSet
    g = ins.grounding["close_vs_ma"]
    assert "185.00" in g.value and "178.00" in g.value and "160.00" in g.value
    assert g.src == _SRC


def test_trend_regime_fires_downtrend_when_price_below_all_four_mas():
    fs = _mk_fs(close=98.0, ma20=101.0, ma50=104.0, ma100=108.0, ma200=112.0)
    ins = _by_name(fs)["trend_regime_pattern"]
    assert "Abwaertstrend" in ins.implication


def test_trend_regime_fires_transition_on_a_clean_ladder():
    # Above the short average, still below the mid/long ones -> monotone recovery.
    fs = _mk_fs(close=415.0, ma20=408.0, ma50=418.0, ma100=422.0, ma200=430.0)
    ins = _by_name(fs)["trend_regime_pattern"]
    assert "Uebergang" in ins.implication
    assert "zurueckerobert" in ins.implication


def test_trend_regime_does_not_fire_on_a_choppy_non_monotonic_split():
    # above ma20, below ma50, above ma100, below ma200 -> no clean regime.
    fs = _mk_fs(close=100.0, ma20=99.0, ma50=101.0, ma100=99.5, ma200=101.0)
    assert "trend_regime_pattern" not in _by_name(fs)


def test_trend_regime_does_not_fire_when_price_sits_exactly_on_an_average():
    fs = _mk_fs(close=100.0, ma20=100.0, ma50=98.0, ma100=97.0, ma200=95.0)
    assert "trend_regime_pattern" not in _by_name(fs)


def test_trend_regime_does_not_fire_with_a_missing_moving_average():
    # ma200 not computable (thin history) -> the code cannot judge the regime.
    fs = _mk_fs(close=185.0, ma20=178.0, ma50=172.0, ma100=168.0, ma200=None)
    assert "trend_regime_pattern" not in _by_name(fs)


# ---------------------------------------------------------------------------
# DETECTOR 5 — vol_regime_pattern.
# ---------------------------------------------------------------------------
def test_vol_regime_fires_expanding_when_short_window_runs_hotter():
    fs = _mk_fs(rvol_20d=0.40, rvol_63d=0.28)  # ratio 1.43 -> expanding
    ins = _by_name(fs)["vol_regime_pattern"]
    assert "nimmt gerade zu" in ins.implication
    g = ins.grounding["realized_vol"]
    assert "40.0" in g.value and "28.0" in g.value


def test_vol_regime_fires_calming_when_short_window_is_cooler():
    fs = _mk_fs(rvol_20d=0.20, rvol_63d=0.30)  # ratio 0.67 -> calming
    ins = _by_name(fs)["vol_regime_pattern"]
    assert "beruhigt" in ins.implication


def test_vol_regime_does_not_fire_inside_the_neutral_band():
    fs = _mk_fs(rvol_20d=0.31, rvol_63d=0.30)  # ratio 1.03 -> no regime
    assert "vol_regime_pattern" not in _by_name(fs)


def test_vol_regime_does_not_fire_when_a_vol_window_is_missing():
    fs = _mk_fs(rvol_20d=None, rvol_63d=0.30)
    assert "vol_regime_pattern" not in _by_name(fs)


# ---------------------------------------------------------------------------
# DETECTOR 6 — rank_momentum_pattern.
# ---------------------------------------------------------------------------
def test_rank_momentum_fires_rising_on_a_strictly_increasing_trend():
    fs = _mk_fs(lstm_score_trend=[0.4, 0.6, 0.9, 1.1, 1.4], lstm_xsec_percentile=78.0)
    ins = _by_name(fs)["rank_momentum_pattern"]
    assert "steigt" in ins.implication
    assert "0.40" in ins.implication and "1.40" in ins.implication  # endpoints
    assert "78.0" in ins.implication  # cross-sectional standing
    assert ins.grounding["cross_section"].src == _SRC


def test_rank_momentum_fires_falling_on_a_strictly_decreasing_trend():
    fs = _mk_fs(lstm_score_trend=[0.9, 0.8, 0.6, 0.5, 0.3])
    ins = _by_name(fs)["rank_momentum_pattern"]
    assert "faellt" in ins.implication


def test_rank_momentum_does_not_fire_on_a_flat_trend():
    fs = _mk_fs(lstm_score_trend=[1.0, 1.0, 1.0])
    assert "rank_momentum_pattern" not in _by_name(fs)


def test_rank_momentum_does_not_fire_on_a_wiggly_trend():
    fs = _mk_fs(lstm_score_trend=[1.0, 3.0, 2.0, 4.0])  # up, down, up -> not clean
    assert "rank_momentum_pattern" not in _by_name(fs)


def test_rank_momentum_does_not_fire_on_a_two_point_trend():
    fs = _mk_fs(lstm_score_trend=[1.0, 2.0])  # too few points for a trajectory
    assert "rank_momentum_pattern" not in _by_name(fs)


def test_rank_momentum_fires_without_a_cross_section_percentile():
    fs = _mk_fs(lstm_score_trend=[0.1, 0.4, 0.9], lstm_xsec_percentile=None)
    ins = _by_name(fs)["rank_momentum_pattern"]
    assert "steigt" in ins.implication
    assert "cross_section" not in ins.grounding  # no percentile -> no such grounding
    assert "Perzentil" not in ins.implication


# ---------------------------------------------------------------------------
# DETECTOR 7 — own_history_extreme_pattern.
# ---------------------------------------------------------------------------
def test_own_extreme_fires_high_at_the_top_of_its_own_range():
    fs = _mk_fs(
        lstm_own_percentile=95.0,
        lstm_hist_std=0.6,
        lstm_hist_min=-0.9,
        lstm_hist_max=1.7,
    )
    ins = _by_name(fs)["own_history_extreme_pattern"]
    assert "oberen" in ins.implication and "95.0" in ins.implication
    assert "historische Spanne" in ins.grounding["hist_range"].value


def test_own_extreme_fires_low_at_the_bottom_of_its_own_range():
    fs = _mk_fs(lstm_own_percentile=4.0, lstm_hist_std=0.5)
    ins = _by_name(fs)["own_history_extreme_pattern"]
    assert "unteren" in ins.implication and "4.0" in ins.implication


def test_own_extreme_does_not_fire_in_the_middle_of_the_range():
    fs = _mk_fs(lstm_own_percentile=50.0, lstm_hist_std=0.6)
    assert "own_history_extreme_pattern" not in _by_name(fs)


def test_own_extreme_does_not_fire_on_a_flat_history_even_at_percentile_zero():
    # A flat history collapses own_percentile to 0.0 by construction — an ARTEFACT,
    # not a low extreme. std == 0 (no dispersion) must keep the detector silent.
    fs = _mk_fs(lstm_own_percentile=0.0, lstm_hist_std=0.0)
    assert "own_history_extreme_pattern" not in _by_name(fs)


def test_own_extreme_does_not_fire_without_a_percentile():
    fs = _mk_fs(lstm_own_percentile=None, lstm_hist_std=0.6)
    assert "own_history_extreme_pattern" not in _by_name(fs)


# ---------------------------------------------------------------------------
# Cross-cutting: determinism, provenance, and no fabricated entities.
# ---------------------------------------------------------------------------
def test_coverage_detectors_are_deterministic():
    fs = _mk_fs(
        close=185.0,
        ma20=178.0,
        ma50=172.0,
        ma100=168.0,
        ma200=160.0,
        rvol_20d=0.40,
        rvol_63d=0.28,
        lstm_score_trend=[0.4, 0.6, 0.9, 1.1, 1.4],
        lstm_own_percentile=95.0,
        lstm_hist_std=0.6,
    )
    assert detect_insights(fs) == detect_insights(fs)


def test_every_coverage_grounding_is_sourced_from_the_factset():
    fs = _mk_fs(
        close=185.0,
        ma20=178.0,
        ma50=172.0,
        ma100=168.0,
        ma200=160.0,
        rvol_20d=0.40,
        rvol_63d=0.28,
        lstm_score_trend=[0.4, 0.6, 0.9, 1.1, 1.4],
        lstm_own_percentile=95.0,
        lstm_hist_std=0.6,
    )
    coverage = {
        "trend_regime_pattern",
        "vol_regime_pattern",
        "rank_momentum_pattern",
        "own_history_extreme_pattern",
    }
    fired = [i for i in detect_insights(fs) if i.pattern_name in coverage]
    assert len(fired) == 4  # all four fire on this rich symbol
    for ins in fired:
        assert ins.grounding
        for key, g in ins.grounding.items():
            assert g.value, f"{ins.pattern_name}.{key} empty"
            assert g.src == _SRC, f"{ins.pattern_name}.{key} not FactSet-sourced"


def test_coverage_detectors_invent_no_company_names_or_dates():
    fs = _mk_fs(
        close=185.0,
        ma20=178.0,
        ma50=172.0,
        ma100=168.0,
        ma200=160.0,
        rvol_20d=0.40,
        rvol_63d=0.28,
        lstm_score_trend=[0.4, 0.6, 0.9, 1.1, 1.4],
        lstm_own_percentile=95.0,
        lstm_hist_std=0.6,
    )
    blob = " ".join(
        ins.implication
        + " "
        + ins.caveat
        + " "
        + " ".join(g.value for g in ins.grounding.values())
        for ins in detect_insights(fs)
    )
    for ghost in ("Cerebras", "Broadcom", "Burry", "OpenAI", "Ergebnis-Termin"):
        assert ghost not in blob, f"{ghost!r} fabricated into a coverage insight"
