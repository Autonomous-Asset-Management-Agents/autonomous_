# tests/unit/test_report_wiring_v2.py
# RPT-6 (Epic #1998) — wire the report pipeline (fact_set -> render -> narrate ->
# audit) behind REPORT_GENERATOR_V2_ENABLED (now ACTIVATED, default ON) into the
# specialist path, plus a real EngineFactSetReader and a test entrypoint.
#
# Test surfaces:
#   1. Flag is default ON in BOTH editions (config.py + config.oss.py) — BORA parity.
#   2. EngineFactSetReader wires the REAL engine sources (bars via the canonical
#      HistoricalDataProvider, the committed model card, CachedFundamentalsReader)
#      and degrades gracefully (honest gaps) for LSTM/news and on any source error.
#   3. build_report_bundle / generate_v2_report run the full pipeline and return an
#      AUDITED markdown report; degenerate data stays graceful (no fabrication).
#   4. DORMANCY: flag OFF -> the specialist's decision path is byte-identical —
#      the v2 attach is skipped, report_markdown stays None, the DTO is unchanged,
#      and _attach never perturbs score/recommendation/reasons/escalate.
#
# The LLM is non-deterministic, so every pipeline test injects a fake provider
# (a bare callable) — no network, no clock, no real model. Fully deterministic.

import asyncio
import copy
import importlib.util
import json
import os
import types
from datetime import date, datetime, timezone
from unittest.mock import patch

import pandas as pd

import config
from core.report.auditor import AuditResult
from core.report.engine_reader import EngineFactSetReader
from core.report.fact_set import FactSet, InMemoryDataContext, NewsItem, build_fact_set
from core.report.financials_feed import CachedFundamentalsReader
from core.report.pipeline import ReportBundle, build_report_bundle, generate_v2_report
from core.specialist.report import SpecialistReport
from core.stock_specialist import StockSpecialistAgent

# ---------------------------------------------------------------------------
# Fixtures — the SAME committed real NVDA slices RPT-1/RPT-3/RPT-5 use.
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)


def _load_json(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _load_card():
    with open(_CARD, encoding="utf-8") as f:
        return json.load(f)


def _ctx(**overrides):
    kw = {
        "bars": [
            (date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")
        ],
        "lstm_series": [
            (date.fromisoformat(d), float(s))
            for d, s in _load_json("nvda_lstm_series.json")
        ],
        "lstm_cross_section": {
            sym: float(sc) for sym, sc in _load_json("nvda_lstm_xsec.json").items()
        },
        "model_card": _load_card(),
        "fundamentals": None,
        "news": [
            NewsItem(
                title=n["title"],
                published_utc=n["published_utc"],
                publisher=n["publisher"],
                url=n["url"],
            )
            for n in _load_json("nvda_news.json")
        ],
        "alt_signals": {},
    }
    kw.update(overrides)
    return InMemoryDataContext(**kw)


def _abstain_llm(_prompt):
    """A bare-callable provider that abstains -> deterministic placeholders."""
    return ""


class _FakeProvider:
    """Stand-in for HistoricalDataProvider.get_data (records the PIT bound)."""

    def __init__(self, result):
        self._result = result
        self.calls = []

    def get_data(self, symbol, end_date, days, **kwargs):
        self.calls.append((symbol, end_date, days))
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _RaisingProvider:
    def get_data(self, *a, **k):
        raise RuntimeError("boom")


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_rpt6", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _gathered(**overrides):
    base = {
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": [],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }
    base.update(overrides)
    return base


def _build_report_cfg():
    """Config surface _build_report reads — all report/blend flags OFF."""
    return types.SimpleNamespace(
        SPECIALIST_PROMPT_V2=False,
        SPECIALIST_CARDS_ENABLED=False,
        ML_SENTIMENT_BLEND_ENABLED=False,
        DATA_INTEGRITY_GUARD_ENABLED=False,
    )


# ===========================================================================
# 1. Flag default ON in BOTH editions (BORA parity).
# Round-table-v2 activation (owner decision): REPORT_GENERATOR_V2_ENABLED now
# defaults to "True" in config.py (l.1028-1029) AND config.oss.py (l.1014-1015).
# This test documents the new activated default; the OFF path stays env-reachable.
# ===========================================================================
def test_flag_default_on_enterprise():
    assert config.get_config().REPORT_GENERATOR_V2_ENABLED is True
    assert "REPORT_GENERATOR_V2_ENABLED" in config.RuntimeConfigState.model_fields


def test_flag_default_on_oss():
    oss = _load_config_oss()
    assert oss.REPORT_GENERATOR_V2_ENABLED is True
    assert oss.get_config().REPORT_GENERATOR_V2_ENABLED is True


# --- A5 (RPT-8b): full-universe report registry flag — dormant, default OFF ----
def test_report_registry_full_universe_flag_default_off_enterprise():
    assert config.get_config().REPORT_REGISTRY_FULL_UNIVERSE_ENABLED is False
    assert (
        "REPORT_REGISTRY_FULL_UNIVERSE_ENABLED"
        in config.RuntimeConfigState.model_fields
    )


def test_report_registry_full_universe_flag_default_off_oss():
    oss = _load_config_oss()
    assert oss.REPORT_REGISTRY_FULL_UNIVERSE_ENABLED is False
    assert oss.get_config().REPORT_REGISTRY_FULL_UNIVERSE_ENABLED is False


def test_refresh_report_only_is_llm_free_and_gather_free():
    """A5: refresh_report_only attaches the v2 report with narrate=False and an
    EMPTY gather ({}), i.e. no LLM and no research()/Gemini fan-out."""
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    seen = {}

    def _spy(report, gathered, *, reader=None, llm=None, narrate=True):
        seen["narrate"] = narrate
        seen["gathered"] = gathered
        report.report_markdown = "# NVDA ..."

    with patch.object(agent, "_attach_v2_report", side_effect=_spy):
        rep = agent.refresh_report_only()
    assert seen["narrate"] is False  # deterministic-first, no LLM
    assert seen["gathered"] == {}  # no research() alt-signal gather
    assert rep.symbol == "NVDA"
    assert rep.report_markdown == "# NVDA ..."


def test_registry_report_only_uses_deterministic_refresh_not_research():
    """A5: with report_only=True the registry refresh calls the agent's
    deterministic report path, NOT research() (no Gemini/network fan-out)."""
    from core.specialist_registry import StockSpecialistRegistry

    reg = StockSpecialistRegistry(["NVDA"], "", report_only=True)

    class _FakeAgent:
        def __init__(self):
            self.research_called = False
            self.report_only_called = False

        async def research(self):
            self.research_called = True
            return SpecialistReport(symbol="NVDA")

        def refresh_report_only(self):
            self.report_only_called = True
            return SpecialistReport(symbol="NVDA", report_markdown="# NVDA (det)")

    fake = _FakeAgent()
    reg._agents["NVDA"] = fake
    asyncio.run(reg._refresh_symbol("NVDA"))
    assert fake.report_only_called is True
    assert fake.research_called is False
    assert reg.get_report("NVDA").report_markdown == "# NVDA (det)"


def test_registry_default_is_full_research_not_report_only():
    """Dormant-default parity: without report_only, the registry still uses
    research() (byte-identical to today)."""
    from core.specialist_registry import StockSpecialistRegistry

    reg = StockSpecialistRegistry(["NVDA"], "")
    assert reg._report_only is False

    class _FakeAgent:
        def __init__(self):
            self.research_called = False

        async def research(self):
            self.research_called = True
            return SpecialistReport(symbol="NVDA")

        def refresh_report_only(self):  # pragma: no cover - must NOT be called
            raise AssertionError("research() path must be used when report_only=False")

    fake = _FakeAgent()
    reg._agents["NVDA"] = fake
    asyncio.run(reg._refresh_symbol("NVDA"))
    assert fake.research_called is True


# ===========================================================================
# 2. EngineFactSetReader — real sources + graceful honest gaps + PIT.
# ===========================================================================
def test_reader_bars_from_dataframe():
    idx = pd.to_datetime(["2026-04-28", "2026-04-29", "2026-04-30"])
    df = pd.DataFrame(
        {"open": [1, 2, 3], "close": [10.0, 11.0, 12.5], "volume": [1, 1, 1]},
        index=idx,
    )
    prov = _FakeProvider(df)
    reader = EngineFactSetReader(AS_OF, data_provider=prov)
    bars = reader.get_bars("nvda")
    assert bars == [
        (date(2026, 4, 28), 10.0),
        (date(2026, 4, 29), 11.0),
        (date(2026, 4, 30), 12.5),
    ]
    # PIT bound handed to the provider is the as-of day (never the future).
    assert prov.calls and prov.calls[0][1].date() == AS_OF
    assert prov.calls[0][0] == "NVDA"


def test_reader_bars_from_sequence():
    seq = [(date(2026, 4, 28), 10.0), (date(2026, 4, 29), 11.0)]
    reader = EngineFactSetReader(AS_OF, data_provider=_FakeProvider(seq))
    assert reader.get_bars("NVDA") == seq


def test_reader_bars_provider_error_is_graceful():
    reader = EngineFactSetReader(AS_OF, data_provider=_RaisingProvider())
    assert reader.get_bars("NVDA") == []  # honest gap, never a crash


def test_reader_bars_empty_frame_is_quiet_gap():
    # A cold cache returns an empty frame (has an index, no 'close' column) ->
    # honest gap, no crash (and no misleading warning).
    reader = EngineFactSetReader(AS_OF, data_provider=_FakeProvider(pd.DataFrame()))
    assert reader.get_bars("NVDA") == []


def test_reader_bars_are_pit_filtered_by_build_fact_set():
    # The reader returns raw bars (incl. one AFTER as-of); build_fact_set drops it.
    seq = [
        (date(2026, 4, 30), 100.0),
        (date(2026, 5, 1), 101.0),
        (date(2026, 5, 5), 999.0),  # future -> must be excluded
    ]
    reader = EngineFactSetReader(AS_OF, data_provider=_FakeProvider(seq))
    fs = build_fact_set("NVDA", AS_OF, reader)
    assert fs.close.value == 101.0  # the 2026-05-05 bar never leaks in


def test_reader_model_card_is_the_committed_constants():
    reader = EngineFactSetReader(AS_OF, data_provider=_FakeProvider([]))
    card = reader.get_model_card()
    assert card["cs_ic_mean"] == 0.067
    assert card["model"] == "v1_cross_sectional_lstm"


def test_reader_fundamentals_honest_gap_when_cache_cold(tmp_path):
    reader = EngineFactSetReader(
        AS_OF,
        data_provider=_FakeProvider([]),
        fundamentals_reader=CachedFundamentalsReader(cache_dir=str(tmp_path)),
    )
    assert reader.get_fundamentals("NVDA", AS_OF) is None


def test_reader_lstm_and_news_default_to_honest_gaps():
    """⚠ This test used to assert `get_news("NVDA") == []` and call that an honest gap.
    It is not one — and the confusion was load-bearing.

    An empty list is a FINDING: "a feed was asked and nothing qualified". None is the
    GAP: "no feed is attached". Collapsing them let build_fact_set stamp every report
    with "Keine relevanten Meldungen im Fenster [src: news feed[NVDA], published_utc
    <= ...]" — a claim about the world, evidenced by a feed nobody called, passing the
    audit with 0 flags.

    get_fundamentals had the contract right all along (Optional). News now matches it,
    and the assertion here matches what an honest gap actually is."""
    reader = EngineFactSetReader(AS_OF, data_provider=_FakeProvider([]))
    assert reader.get_lstm_series("NVDA") == []
    assert reader.get_lstm_cross_section(AS_OF) == {}
    assert (
        reader.get_news("NVDA") is None
    ), "no feed attached must read as None (a gap), never [] (an empty finding)"


def test_reader_injected_seams_pass_through():
    news = [
        NewsItem(
            title="X", published_utc="2026-04-30T10:00:00+00:00", publisher="P", url="u"
        )
    ]

    class _Lstm:
        def series(self, symbol):
            return [(date(2026, 5, 1), 0.5)]

        def cross_section(self, as_of):
            return {"NVDA": 0.5, "AAPL": 0.1}

    reader = EngineFactSetReader(
        AS_OF,
        data_provider=_FakeProvider([]),
        lstm_reader=_Lstm(),
        news=news,
        alt_signals={"insider_trades": [{}, {}, {}]},
    )
    assert reader.get_lstm_series("NVDA") == [(date(2026, 5, 1), 0.5)]
    assert reader.get_lstm_cross_section(AS_OF) == {"NVDA": 0.5, "AAPL": 0.1}
    assert reader.get_news("NVDA") == news
    assert reader.get_alt_signals("NVDA") == {"insider_trades": [{}, {}, {}]}


# ===========================================================================
# 3. Pipeline — audited markdown; graceful on degenerate data.
# ===========================================================================
def test_build_report_bundle_returns_audited_markdown():
    bundle = build_report_bundle("NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm)
    assert isinstance(bundle, ReportBundle)
    assert isinstance(bundle.fact_set, FactSet)
    assert isinstance(bundle.audit, AuditResult)
    assert bundle.markdown.startswith("# NVDA")
    assert "Empfehlung" in bundle.markdown
    # The abstained/deterministic report must audit clean (no fabricated claim).
    assert bundle.audit.integrity_ok is True


def test_generate_v2_report_returns_the_markdown_string():
    md = generate_v2_report("NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm)
    assert isinstance(md, str)
    assert md.startswith("# NVDA")
    # Thin wrapper == bundle.markdown.
    assert (
        md
        == build_report_bundle("NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm).markdown
    )


def test_pipeline_graceful_on_missing_feeds():
    # A genuine partial-data stock: ONLY the committed model card is present
    # (the audit vocabulary, e.g. "lstm"); bars, lstm panel, news and fundamentals
    # are all cold/honest gaps. The report is still produced with honest "n/a"
    # gaps everywhere and audits CLEAN — no fabrication, no crash, and crucially
    # no bare window-integer (e.g. "MA200") striking without a price to source it.
    # This is the norm for most of the ~500-symbol universe on a cold start.
    reader = InMemoryDataContext(model_card=_load_card())
    bundle = build_report_bundle("NVDA", AS_OF, reader=reader, llm=_abstain_llm)
    assert bundle.markdown.startswith("# NVDA")
    assert "n/a" in bundle.markdown  # the missing feeds render as honest gaps
    assert bundle.audit.integrity_ok is True


def test_price_trend_audits_clean_for_any_price_not_just_near_200():
    # Regression (RPT-3c): §3 must not emit bare window-integers ("MA200",
    # "200-Tage-Schnitt"). Those only audited when the price happened to sit
    # within tolerance of 200 (NVDA ~198) — a latent strike for every stock
    # priced elsewhere. A warm report for a ~137-priced name must audit clean.
    d0 = date(2026, 1, 1)
    bars = [(date.fromordinal(d0.toordinal() + i), 137.0 + (i % 5)) for i in range(260)]
    ctx = InMemoryDataContext(
        model_card=_load_card(),
        bars=bars,
        lstm_series=[
            (date.fromordinal(d0.toordinal() + i), 1.0 + i * 0.01) for i in range(260)
        ],
        lstm_cross_section={"WXY": 3.0, "A": 1.0, "B": 2.0},
    )
    fs = build_fact_set("WXY", date(2026, 9, 17), ctx)
    bundle = build_report_bundle("WXY", date(2026, 9, 17), reader=ctx, llm=_abstain_llm)
    assert bundle.audit.integrity_ok is True, bundle.audit.summary()
    assert "MA200" not in bundle.markdown  # no bare window-integer in the body


def test_pipeline_never_crashes_even_with_totally_empty_context():
    # Even a fully-empty context (no card) must produce a report and never raise;
    # audit cleanliness is NOT guaranteed there (an empty card cannot source the
    # word "LSTM"), but the pipeline degrades gracefully rather than crashing.
    bundle = build_report_bundle(
        "NVDA", AS_OF, reader=InMemoryDataContext(), llm=_abstain_llm
    )
    assert bundle.markdown.startswith("# NVDA")
    assert isinstance(bundle.audit, AuditResult)


def test_pipeline_deterministic():
    a = build_report_bundle("NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm).markdown
    b = build_report_bundle("NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm).markdown
    assert a == b


# ===========================================================================
# 3b. Deterministic-first (narrate=False) — a complete, audit-clean report
# with ZERO LLM touch, so all ~500 reports cost no LLM until one is opened.
# ===========================================================================
def test_narrate_false_never_touches_the_llm():
    """narrate=False must skip RPT-5 entirely — even an LLM that RAISES on call
    is never invoked, yet the report still builds and audits clean."""

    def _exploding_llm(_prompt):
        raise AssertionError("LLM must NOT be called when narrate=False")

    bundle = build_report_bundle(
        "NVDA", AS_OF, reader=_ctx(), llm=_exploding_llm, narrate=False
    )
    assert isinstance(bundle, ReportBundle)
    assert bundle.markdown.startswith("# NVDA")
    assert bundle.audit.integrity_ok is True
    # The prose slots degrade to their deterministic placeholders.
    assert "SLOT:thesis" in bundle.markdown


def test_narrate_false_equals_abstaining_llm():
    """A skipped narration and an abstaining provider yield the identical
    deterministic report (empty slots == no slots -> same fallbacks)."""
    skipped = build_report_bundle("NVDA", AS_OF, reader=_ctx(), narrate=False).markdown
    abstained = build_report_bundle(
        "NVDA", AS_OF, reader=_ctx(), llm=_abstain_llm
    ).markdown
    assert skipped == abstained


def test_generate_v2_report_narrate_false_is_llm_free():
    def _exploding_llm(_prompt):
        raise AssertionError("LLM must NOT be called when narrate=False")

    md = generate_v2_report(
        "NVDA", AS_OF, reader=_ctx(), llm=_exploding_llm, narrate=False
    )
    assert md.startswith("# NVDA")


# ===========================================================================
# 4. Dormancy — flag OFF is byte-identical; the attach never changes decisions.
# ===========================================================================
def test_new_report_fields_default_none():
    r = SpecialistReport(symbol="AAPL")
    assert r.report_markdown is None
    assert r.report_audit_ok is None
    assert r.report_audit_summary is None
    assert r.report_recommendation is None


def test_new_report_fields_not_serialized_dto_byte_identical():
    # The serializer lives in the FastAPI engine app; importing it pulls the ML
    # stack (torch/sb3), present in CI but optional locally. Skip cleanly when the
    # stack is absent so the check still runs (and gates) in CI.
    try:
        from core.engine.api_routes import _serialize_specialist_report
    except Exception as exc:  # pragma: no cover - local-only environment guard
        import pytest

        pytest.skip(f"engine app deps unavailable in this environment: {exc}")

    plain = SpecialistReport(symbol="AAPL")
    withrep = SpecialistReport(
        symbol="AAPL",
        report_markdown="# AAPL ...",
        report_audit_ok=True,
        report_audit_summary="integrity=OK claims=3 flags=0",
        report_recommendation="hold",
    )
    # This asserts the OFF-behaviour / BORA byte-identity contract: with the report
    # generator flag OFF, the additive report_* keys never leak into the DTO. That
    # flag now DEFAULTS to True (config.py l.1028-1029 / config.oss.py l.1014-1015),
    # so the OFF path must be forced explicitly here — it stays valid + env-reachable.
    off_cfg = config.get_config().model_copy(
        update={"REPORT_GENERATOR_V2_ENABLED": False}
    )
    with patch.object(config, "get_config", return_value=off_cfg):
        dto_plain = _serialize_specialist_report("AAPL", plain)
        dto_withrep = _serialize_specialist_report("AAPL", withrep)
    dto_plain.pop("updated_at", None)
    dto_withrep.pop("updated_at", None)
    # The new fields never leak into the DTO -> byte-identical serialization.
    assert "report_markdown" not in dto_plain
    assert "report_audit_ok" not in dto_plain
    assert "report_audit_summary" not in dto_plain
    assert "report_recommendation" not in dto_plain
    assert dto_plain == dto_withrep


def test_gate_off_is_a_noop():
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    report = SpecialistReport(symbol="NVDA")
    cfg = types.SimpleNamespace(REPORT_GENERATOR_V2_ENABLED=False)
    with patch("core.stock_specialist.get_config", return_value=cfg):
        with patch.object(agent, "_attach_v2_report") as spy:
            agent._maybe_attach_v2_report(report, _gathered())
    spy.assert_not_called()
    assert report.report_markdown is None


def test_gate_on_calls_attach():
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    report = SpecialistReport(symbol="NVDA")
    cfg = types.SimpleNamespace(REPORT_GENERATOR_V2_ENABLED=True)
    with patch("core.stock_specialist.get_config", return_value=cfg):
        with patch.object(agent, "_attach_v2_report") as spy:
            agent._maybe_attach_v2_report(report, _gathered())
    spy.assert_called_once()


def test_attach_sets_fields_without_touching_the_decision():
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    g = _gathered(short_interest_pct=30.0, reddit_mentions_24h=9)
    with patch("core.stock_specialist.get_config", return_value=_build_report_cfg()):
        report = agent._build_report(copy.deepcopy(g), {"text": ""})
    # Capture the decision surface BEFORE the report-only attach.
    before = (
        report.sentiment_score,
        report.recommendation,
        report.confidence,
        list(report.reasons),
        report.escalate,
        report.escalate_reason,
    )
    agent._attach_v2_report(report, g, reader=_ctx(), llm=_abstain_llm)
    after = (
        report.sentiment_score,
        report.recommendation,
        report.confidence,
        list(report.reasons),
        report.escalate,
        report.escalate_reason,
    )
    assert before == after  # report-only: the decision is untouched
    assert report.report_markdown.startswith("# NVDA")
    assert report.report_audit_ok is True
    assert report.report_audit_summary.startswith("integrity=OK")


def test_attach_is_guarded_on_pipeline_failure():
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    report = SpecialistReport(symbol="NVDA")

    class _BoomReader:
        def get_bars(self, symbol):
            raise RuntimeError("kaboom")

    # A reader that explodes must not crash research — honest gap, fields stay None.
    agent._attach_v2_report(report, _gathered(), reader=_BoomReader(), llm=_abstain_llm)
    assert report.report_markdown is None


# ===========================================================================
# 4b. Deterministic-first attach (RPT-8b) — narrate=False is LLM-free, so the
# specialist can populate the whole universe cheaply and narrate lazily on open.
# ===========================================================================
def test_attach_narrate_false_is_llm_free_but_still_attaches():
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    report = SpecialistReport(symbol="NVDA")

    def _exploding_llm(_prompt):
        raise AssertionError("attach(narrate=False) must not call the LLM")

    agent._attach_v2_report(
        report, _gathered(), reader=_ctx(), llm=_exploding_llm, narrate=False
    )
    assert report.report_markdown.startswith("# NVDA")
    assert report.report_audit_ok is True
    assert report.report_audit_summary.startswith("integrity=OK")


def test_narrate_report_upgrades_a_deterministic_report_in_place():
    """Lazy on-open path: a report first attached deterministically can later be
    re-narrated (one symbol, one LLM pass) and its markdown upgraded in place,
    without touching the decision fields."""
    agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
    report = SpecialistReport(symbol="NVDA")
    # 1) deterministic-first: prose slots are placeholders.
    agent._attach_v2_report(report, _gathered(), reader=_ctx(), narrate=False)
    assert "SLOT:thesis" in report.report_markdown
    deterministic_md = report.report_markdown

    # 2) lazy narration fills the prose slots via a provider (here a stub that
    # returns non-empty prose) — same symbol, in place.
    def _prose_llm(_prompt):
        return "Belegte These aus dem FactSet."

    agent.narrate_report(report, reader=_ctx(), llm=_prose_llm)
    assert report.report_markdown.startswith("# NVDA")
    # The narrated report differs from the placeholder one (prose filled in) and
    # still audits clean.
    assert report.report_markdown != deterministic_md
    assert report.report_audit_ok is True
