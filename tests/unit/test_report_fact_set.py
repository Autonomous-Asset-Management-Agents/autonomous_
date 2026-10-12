# tests/unit/test_report_fact_set.py
# RPT-1 (#2000, Epic #1998) — deterministic FactSet builder.
#
# Test surfaces:
#   1. GOLDEN-FILE GATE 1 (no LLM): the FactSet reproduces the audited,
#      deterministic NVDA claims number-exact against committed real-data
#      fixtures (nvda_report_final.md / nvda_audit_table.md).
#   2. Point-in-time (PIT) filtering: future bars / LSTM rows / news are excluded.
#   3. Provenance: every derived field carries a non-empty ``src``.
#   4. Determinism: same input -> byte-identical FactSet.
#   5. Degenerate inputs: missing news / fundamentals / short history -> an
#      honest gap (None / []), never a fabricated value.
#
# Fully deterministic: no network / LLM / GPU / pandas.

import json
import os
from datetime import date, datetime

from core.report.fact_set import (
    Fact,
    FactSet,
    InMemoryDataContext,
    NewsItem,
    build_fact_set,
)

# ---------------------------------------------------------------------------
# Fixtures (minimal, committed, real NVDA data — see fixtures/report/manifest.json)
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)


def _load_json(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _load_bars():
    return [(date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")]


def _load_lstm_series():
    return [
        (date.fromisoformat(d), float(s))
        for d, s in _load_json("nvda_lstm_series.json")
    ]


def _load_xsec():
    return {sym: float(sc) for sym, sc in _load_json("nvda_lstm_xsec.json").items()}


def _load_news():
    with open(os.path.join(_FX, "nvda_news.json"), encoding="utf-8") as f:
        return [
            NewsItem(
                title=n["title"],
                published_utc=n["published_utc"],
                publisher=n["publisher"],
                url=n["url"],
            )
            for n in json.load(f)
        ]


def _load_card():
    with open(_CARD, encoding="utf-8") as f:
        return json.load(f)


def _ctx(**overrides):
    kw = {
        "bars": _load_bars(),
        "lstm_series": _load_lstm_series(),
        "lstm_cross_section": _load_xsec(),
        "model_card": _load_card(),
        "fundamentals": None,
        "news": _load_news(),
        "alt_signals": {},
    }
    kw.update(overrides)
    return InMemoryDataContext(**kw)


def _build(**overrides):
    return build_fact_set("NVDA", AS_OF, _ctx(**overrides))


# ---------------------------------------------------------------------------
# 1. GOLDEN-FILE GATE 1 — deterministic NVDA claims, number-exact
# ---------------------------------------------------------------------------
def test_golden_tech_and_vol():
    fs = _build()
    assert round(fs.close.value, 2) == 198.45
    assert [round(x, 2) for x in fs.pullback_series.value] == [
        216.61,
        213.17,
        209.25,
        199.57,
        198.45,
    ]
    assert round(fs.pullback_pct.value, 1) == -8.4
    assert round(fs.ma20.value, 2) == 197.22
    assert round(fs.ma50.value, 2) == 187.15
    assert round(fs.ma100.value, 2) == 185.93
    assert round(fs.ma200.value, 2) == 183.84
    assert round(fs.rvol_20d.value * 100, 1) == 34.9
    assert round(fs.rvol_63d.value * 100, 1) == 39.1


def test_golden_high_low_windows():
    fs = _build()
    assert round(fs.high_52w.value.price, 2) == 216.61
    assert fs.high_52w.value.on == date(2026, 4, 27)
    assert round(fs.low_52w.value.price, 2) == 111.58
    assert fs.low_52w.value.on == date(2025, 5, 1)
    assert round(fs.low_1m.value.price, 2) == 177.64
    assert fs.low_1m.value.on == date(2026, 4, 6)
    assert round(fs.high_1m.value.price, 2) == 216.61


def test_golden_xsec_lstm():
    fs = _build()
    assert round(fs.lstm_score.value, 4) == 4.0999
    assert round(fs.lstm_own_percentile.value, 1) == 93.5
    assert round(fs.lstm_xsec_percentile.value, 1) == 95.7
    assert fs.lstm_xsec_rank.value == 21
    assert fs.lstm_xsec_n.value == 486
    assert [round(x, 2) for x in fs.lstm_score_trend.value] == [
        1.55,
        2.52,
        3.29,
        3.58,
        4.10,
    ]
    assert round(fs.lstm_hist_min.value, 2) == -5.09
    assert round(fs.lstm_hist_max.value, 2) == 6.91
    assert round(fs.lstm_hist_mean.value, 2) == 0.39
    assert round(fs.lstm_hist_std.value, 2) == 2.27


def test_golden_model_card():
    fs = _build()
    mc = fs.model_card.value
    assert mc["cs_ic_mean"] == 0.067
    assert mc["cs_ic_tstat"] == 20.4
    assert mc["sharpe_topq_gross"] == 1.53
    assert mc["sharpe_topq_net_10bps"] == 0.758
    assert mc["sharpe_equalweight_net_10bps"] == 0.944
    assert mc["turnover_daily"] == 0.50
    # Honest leakage caveat must ride along with the card, verbatim.
    assert mc["leakage_caveat"]
    assert "random 80/20" in mc["leakage_caveat"]


def test_golden_news_items_pit_and_provenance():
    fs = _build()
    items = fs.news_items.value
    assert len(items) == 4
    # every item is PIT-valid and fully provenanced
    for ni in items:
        assert datetime.fromisoformat(ni.published_utc).date() <= AS_OF
        assert ni.title and ni.published_utc and ni.publisher and ni.url
    # the hard, terminated catalyst (earnings CC) is present and correctly sourced
    cc = [n for n in items if "Conference Call" in n.title]
    assert len(cc) == 1
    assert cc[0].publisher == "GlobeNewswire Inc."
    assert cc[0].published_utc.startswith("2026-04-29")


# ---------------------------------------------------------------------------
# 2. Point-in-time filtering (no lookahead)
# ---------------------------------------------------------------------------
def test_pit_excludes_future_bars_and_lstm():
    bars = _load_bars() + [(date(2026, 5, 2), 999.0)]
    series = _load_lstm_series() + [(date(2026, 5, 2), 99.0)]
    fs = _build(bars=bars, lstm_series=series)
    # future close/score must not leak into as-of facts
    assert round(fs.close.value, 2) == 198.45
    assert round(fs.lstm_score.value, 4) == 4.0999
    # 99.0 would be a new all-time max if it leaked — it must not
    assert round(fs.lstm_hist_max.value, 2) == 6.91


def test_pit_excludes_future_news():
    future = NewsItem(
        title="SYNTHETIC FUTURE HEADLINE",
        published_utc="2026-06-01T12:00:00+00:00",
        publisher="Test",
        url="https://example.com/future",
    )
    fs = _build(news=_load_news() + [future])
    titles = [n.title for n in fs.news_items.value]
    assert "SYNTHETIC FUTURE HEADLINE" not in titles
    assert len(fs.news_items.value) == 4


# ---------------------------------------------------------------------------
# 3. Provenance — every derived Fact carries a non-empty src
# ---------------------------------------------------------------------------
def test_every_fact_has_src():
    fs = _build()
    facts = [v for v in vars(fs).values() if isinstance(v, Fact)]
    assert len(facts) >= 20
    for fct in facts:
        assert isinstance(fct.src, str) and fct.src.strip()


# ---------------------------------------------------------------------------
# 4. Determinism — same input -> identical FactSet
# ---------------------------------------------------------------------------
def test_deterministic_same_input_same_factset():
    assert _build() == _build()
    assert isinstance(_build(), FactSet)


# ---------------------------------------------------------------------------
# 5. Degenerate inputs — honest gaps, never fabrication
# ---------------------------------------------------------------------------
def test_missing_fundamentals_is_honest_gap():
    fs = _build(fundamentals=None)
    assert fs.fundamentals.value is None
    assert "RPT-2" in fs.fundamentals.src or "not wired" in fs.fundamentals.src.lower()


# ---------------------------------------------------------------------------
# 5b. P/E completion seam — the feed can only form pe when handed an as-of
#     close; the production reader is built once (before any per-symbol bar),
#     so it never has one and pe stays None. build_fact_set is the one place
#     where the as-of close and the derived filing meet, so pe is completed
#     here: pe = as-of close / trailing diluted EPS, ONLY when both are > 0.
# ---------------------------------------------------------------------------
def test_pe_completed_from_close_and_eps_when_feed_lacked_a_close():
    # Report-schema fundamentals exactly as production emits them (pe=None because
    # the feed had no close). fixture bars end at the 2026-05-01 close of 198.45.
    fund = {
        "pe": None,
        "eps_diluted": 4.90,
        "revenue_yoy_pct": 65.5,
        "debt_to_equity": 0.31,
    }
    fs = _build(fundamentals=fund)
    assert round(fs.fundamentals.value["pe"], 1) == 40.5  # 198.45 / 4.90
    # provenance names the derivation (close + trailing EPS), not just the filing
    assert "as-of close" in fs.fundamentals.src
    # the input dict is not mutated (build copies it)
    assert fund["pe"] is None


def test_pe_is_not_fabricated_without_a_positive_eps():
    # None / 0 / negative EPS -> pe stays an honest gap (no pe=0, no inf, no
    # division by zero), so the valuation detector correctly stays silent.
    for bad_eps in (None, 0.0, -1.25):
        fund = {
            "pe": None,
            "eps_diluted": bad_eps,
            "revenue_yoy_pct": 65.5,
            "debt_to_equity": 0.31,
        }
        fs = _build(fundamentals=fund)
        assert fs.fundamentals.value.get("pe") is None, f"eps={bad_eps}"


def test_existing_pe_from_the_feed_is_never_overwritten():
    # When the feed already computed pe (a close_lookup was wired), the seam must
    # leave it untouched — no silent recomputation from a different close.
    fund = {
        "pe": 40.5,
        "eps_diluted": 4.90,
        "revenue_yoy_pct": 65.5,
        "debt_to_equity": 0.31,
    }
    fs = _build(fundamentals=fund)
    assert fs.fundamentals.value["pe"] == 40.5
    # untouched -> the plain filing provenance stays (no close clause appended)
    assert "as-of close" not in fs.fundamentals.src


def test_pe_stays_none_when_bars_are_empty():
    # No close available at all -> pe cannot be formed, honest gap.
    fund = {
        "pe": None,
        "eps_diluted": 4.90,
        "revenue_yoy_pct": 65.5,
        "debt_to_equity": 0.31,
    }
    fs = _build(bars=[], fundamentals=fund)
    assert fs.fundamentals.value.get("pe") is None


def test_missing_news_yields_empty_not_fabricated():
    fs = _build(news=[])
    assert fs.news_items.value == []
    assert fs.news_items.src  # still provenanced


def test_empty_bars_yields_none_tech_not_crash():
    fs = _build(bars=[])
    assert fs.close.value is None
    assert fs.ma20.value is None
    assert fs.ma200.value is None
    assert fs.close.src  # honest src even on the empty path


def test_short_history_gives_ma20_but_no_ma200():
    fs = _build(bars=_load_bars()[-30:])
    assert fs.ma20.value is not None
    assert fs.ma200.value is None  # < 200 closes -> honest gap


def test_missing_cross_section_gives_none_xsec():
    fs = _build(lstm_cross_section={})
    assert fs.lstm_xsec_percentile.value is None
    assert fs.lstm_xsec_rank.value is None
    assert fs.lstm_xsec_n.value is None
    # own-history facts still computed from the series
    assert round(fs.lstm_score.value, 4) == 4.0999


# ---------------------------------------------------------------------------
# 6. build_pros_cons reuse (the deterministic card component, #1264)
# ---------------------------------------------------------------------------
def test_pros_cons_reuse_from_alt_signals():
    fs = _build(alt_signals={"insider_trades": [{}, {}, {}]})
    assert any("insider" in p.lower() for p in fs.pros.value)
    # default (no alt-signals) -> empty, honest
    assert _build().pros.value == []
