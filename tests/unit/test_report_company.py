# tests/unit/test_report_company.py
# ADR-RPT-COMPANY-01 — the report must SAY WHAT THE COMPANY DOES.
#
# The defect: the note printed the ticker "LYB" and jumped straight to a rank and a
# table of numbers. A reader never learned that LyondellBasell makes petrochemicals.
#
# The fixtures are the REAL, live /v3/reference/tickers payloads (fetched 2026-07-17,
# HTTP 200, free tier) — NOT hand-built dicts in the shape this module wishes for.
# That distinction is the point: three features shipped dead this week because the
# consumer was written against an invented schema and the fixture agreed with the
# invention. XOM is committed alongside LYB precisely because its live payload carries
# `sic_description: None` — a real missing field, not an imagined one.

import json
import os
from datetime import date

import pytest

from core.report.auditor import audit
from core.report.company_feed import (
    CachedCompanyReader,
    cache_path,
    map_polygon_company,
)
from core.report.fact_set import InMemoryDataContext, build_fact_set
from core.report.render import render

_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
AS_OF = date(2026, 5, 1)
FETCHED = date(2026, 7, 17)


def _load(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _lyb_raw():
    return _load("lyb_company.json")["results"]


def _company(**raw_overrides):
    """The mapped LYB company facts, overriding fields of the RAW payload FIRST.

    ⚠ Overriding the MAPPED dict afterwards (`c.update(...)`) would bypass the producer —
    derived fields like `description_figures` / `description_withheld` would keep LYB's
    values while `description` said something else. That is the "fixture is not the
    producer" trap this suite exists to prevent, and it silently defeated three of these
    tests until the mapper was made to run on the override.
    """
    raw = dict(_lyb_raw())
    raw.update(raw_overrides)
    return map_polygon_company(raw, fetched_at=FETCHED)


def _ctx(company=None, **kw):
    return InMemoryDataContext(company=company, **kw)


def _fs(company=None, **kw):
    return build_fact_set("LYB", AS_OF, _ctx(company=company, **kw))


# =========================================================================== #
# 1. The REAL payload maps correctly — pinned field by field.
# =========================================================================== #
class TestRealPayloadMaps:
    def test_the_live_lyb_payload_maps_every_field(self):
        c = map_polygon_company(_lyb_raw(), fetched_at=FETCHED)

        assert c["name"] == "LyondellBasell Industries N.V. Class A"
        assert c["description"].startswith("LyondellBasell is a petrochemical producer")
        assert c["sic_description"] == "INDUSTRIAL ORGANIC CHEMICALS"
        assert c["employees"] == 18970
        assert c["market_cap"] == pytest.approx(18721516834.0)
        assert c["homepage_url"] == "https://www.lyondellbasell.com"
        assert c["list_date"] == date(2010, 4, 28)
        assert c["fetched_at"] == FETCHED

    def test_list_date_is_a_date_object_not_a_string(self):
        """Load-bearing for the AUDITOR, not for taste: auditor._Pool._absorb adds a
        `date` to the PIT date pool but only word-scans a `str`. A string list_date
        renders "2010-04-28" and then STRIKES as an unsourced date."""
        c = map_polygon_company(_lyb_raw(), fetched_at=FETCHED)

        assert isinstance(c["list_date"], date)
        assert not isinstance(c["list_date"], str)

    def test_market_cap_is_pre_scaled_to_the_units_the_report_prints(self):
        """Also auditor-load-bearing, and the same trap to_report_fundamentals hit: the
        pool holds Fact VALUES verbatim, so a rendered magnitude must exist in the
        FactSet AT ITS RENDERED SCALE. Raw 18721516834.0 in the pool can never source a
        rendered "18.72 Mrd"."""
        c = map_polygon_company(_lyb_raw(), fetched_at=FETCHED)

        assert c["market_cap_display"] == pytest.approx(18.721516834)
        assert c["market_cap_display_unit"] == "Mrd"

    def test_only_the_rendered_scale_enters_the_factset(self):
        """⚠ Emitting BOTH scales put an unrendered market_cap_bio (0.0187) in the audit
        pool, and |0.0187| <= 1.5 made the pool's fraction rule admit 1.87 as well — two
        numbers that appear nowhere in the source and can never legitimately be rendered,
        each able to shield a fabrication in the gate that IS the moat."""
        c = map_polygon_company(_lyb_raw(), fetched_at=FETCHED)
        fs = _fs(company=c)

        assert "market_cap_mrd" not in c and "market_cap_bio" not in c

        # Prove it at the gate, not just in the dict: the unrendered scale and its
        # fraction-rule shadow must NOT be sourceable.
        for fabricated in ("Marktkapitalisierung 1.87 Mrd USD.", "0.02 Bio USD."):
            assert not audit(fabricated, fs).integrity_ok, (
                f"{fabricated!r} was accepted — an unrendered scale is shielding a "
                f"fabrication in the audit pool"
            )

    def test_the_live_xom_payload_omits_sic_description_entirely(self):
        """Not a hypothetical, and not what we assumed either: XOM's LIVE payload does
        not send `sic_description: null` — it OMITS the key. A mapper written with
        `raw["sic_description"]` would KeyError on a real S&P 500 name; only `.get()`
        survives contact. (This test asserted `is None` until the real fixture said
        otherwise — which is the entire argument for committing live payloads.)"""
        raw = _load("xom_company.json")["results"]
        assert "sic_description" not in raw
        assert "sic_code" not in raw

        c = map_polygon_company(raw, fetched_at=FETCHED)

        assert c["sic_description"] is None  # absent key -> honest gap, not a crash
        assert c["name"] == "ExxonMobil Holdings Corporation"  # the rest survives


# =========================================================================== #
# 2. None vs {} — the defect that shipped TWICE this week.
# =========================================================================== #
class TestNoneVersusEmptyAreDifferentClaims:
    def test_unusable_payload_fails_closed_to_none(self):
        for junk in (None, "not a dict", 42, []):
            assert map_polygon_company(junk, fetched_at=FETCHED) is None

    def test_a_payload_with_no_usable_facts_is_empty_dict_not_none(self):
        """The source ANSWERED and carried nothing. That is a different claim from
        'nobody asked', and it must survive as {}."""
        out = map_polygon_company({"ticker": "ZZZ"}, fetched_at=FETCHED)

        assert out == {}
        assert out is not None

    def test_factset_keeps_none_and_empty_apart(self):
        assert _fs(company=None).company.value is None
        assert _fs(company={}).company.value == {}

    def test_none_and_empty_render_differently(self):
        never_asked = render(_fs(company=None))
        asked_nothing = render(_fs(company={}))

        assert "keine Unternehmensquelle angebunden" in never_asked
        assert "kein Befund" in never_asked

        assert "keine Angaben" in asked_nothing
        assert "keine Unternehmensquelle angebunden" not in asked_nothing

        assert never_asked != asked_nothing

    def test_no_feed_never_cites_a_source_it_did_not_call(self):
        """The news defect verbatim: 'no relevant news' + a [src:] badge for a feed
        nobody called. The company fact must not repeat it."""
        fs = _fs(company=None)
        assert "not wired" in fs.company.src
        assert "reference/tickers" not in fs.company.src


# =========================================================================== #
# 3. The description is POLYGON'S prose — never ours, never an LLM's.
# =========================================================================== #
class TestDescriptionIsAnAttributedQuote:
    def test_a_blank_description_is_an_honest_gap_not_an_empty_string(self):
        for blank in ("", "   ", None):
            c = map_polygon_company(
                dict(_lyb_raw(), description=blank), fetched_at=FETCHED
            )
            assert c["description"] is None, f"{blank!r} became a fake description"

    def test_a_missing_description_says_so_and_quotes_nothing(self):
        md = render(_fs(company=_company(description=None)))

        assert "keine Beschreibung" in md
        # B5: bis zur naechsten Ueberschrift schneiden, nicht bis "## 1." — sonst
        # zieht der Slice nach einer Umnummerierung fremde Abschnitte mit hinein.
        assert "> " not in md.split("## 0.")[1].split("\n## ")[0]  # no empty blockquote

    def test_the_description_renders_verbatim_as_an_attributed_quote(self):
        md = render(_fs(company=_company()))

        # verbatim — not summarised, not translated, not laundered into our voice
        quoted = "> LyondellBasell is a petrochemical producer with operations"
        assert quoted in md
        # and ATTRIBUTED as someone else's words
        assert "Originaltext" in md
        assert "polygon" in md.lower()

    def test_the_reader_learns_what_the_company_does(self):
        """The whole point (Georg): 'genau aussagen: das ist die firma, das macht die'."""
        md = render(_fs(company=_company()))
        section = md.split("## 0. Das Unternehmen")[1].split("## ")[0]

        assert "LyondellBasell Industries N.V. Class A" in section
        assert "petrochemical producer" in section
        assert "INDUSTRIAL ORGANIC CHEMICALS" in section


# =========================================================================== #
# 4. THE WIRING — a verified mapper with no caller is a dead feature.
#    That exact shape shipped three times this week.
# =========================================================================== #
class TestWiring:
    def test_build_fact_set_carries_the_company_fact_through(self):
        fs = _fs(company=_company())

        assert fs.company.value["name"] == "LyondellBasell Industries N.V. Class A"
        assert fs.company.src == "polygon reference/tickers[LYB], fetched 2026-07-17"

    def test_render_actually_emits_the_company_section(self):
        md = render(_fs(company=_company()))

        assert "## 0. Das Unternehmen" in md

    def test_section_zero_precedes_the_recommendation_and_the_rank(self):
        """§0 is the FIRST thing the reader sees — before the call, before the rank."""
        md = render(_fs(company=_company()))

        # B5: the full recommendation moved to the END (Georg's dictated order); the
        # CALL itself stays high up as a one-liner. §0 must still precede it.
        assert md.index("## 0. Das Unternehmen") < md.index(
            "## Einschätzung in einem Satz"
        )
        assert md.index("## 0. Das Unternehmen") < md.index("## Empfehlung")
        assert md.index("## 0. Das Unternehmen") < md.index("## 1. Quant-Signal")
        assert md.startswith("# LYB")

    def test_the_engine_reader_defaults_to_the_cached_company_reader(self):
        """The seam must be WIRED by default, not left for a caller that never comes.
        Mirrors how fundamentals defaults to CachedFundamentalsReader."""
        from core.report.company_feed import CachedCompanyReader
        from core.report.engine_reader import EngineFactSetReader

        reader = EngineFactSetReader(AS_OF)
        assert isinstance(reader._company_reader, CachedCompanyReader)

    def test_the_engine_reader_passes_company_facts_through(self):
        from core.report.engine_reader import EngineFactSetReader

        class _Stub:
            def get_company(self, symbol):
                return {"name": "Stub Inc"}

        wired = EngineFactSetReader(AS_OF, company_reader=_Stub())
        assert wired.get_company("LYB") == {"name": "Stub Inc"}

    def test_the_engine_reader_keeps_none_and_empty_apart(self):
        """A `or {}` here would erase the distinction §0 branches on."""
        from core.report.engine_reader import EngineFactSetReader

        class _Stub:
            def __init__(self, out):
                self.out = out

            def get_company(self, symbol):
                return self.out

        assert (
            EngineFactSetReader(AS_OF, company_reader=_Stub(None)).get_company("X")
            is None
        )
        assert (
            EngineFactSetReader(AS_OF, company_reader=_Stub({})).get_company("X") == {}
        )

    def test_engine_reader_failure_degrades_to_none_never_crashes(self):
        from core.report.engine_reader import EngineFactSetReader

        class _Boom:
            def get_company(self, symbol):
                raise RuntimeError("cache on fire")

        assert (
            EngineFactSetReader(AS_OF, company_reader=_Boom()).get_company("LYB")
            is None
        )


# =========================================================================== #
# 5. The cache round-trips (quarter-stable, like the financials cache).
# =========================================================================== #
class TestCacheRoundTrip:
    def test_cache_round_trips_the_real_payload(self, tmp_path):
        cache_dir = str(tmp_path / "company_cache")
        path = cache_path(cache_dir, "lyb")  # lower-case in, upper-case file out
        assert path.endswith("LYB.json")

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(_load("lyb_company.json"), fh)

        out = CachedCompanyReader(cache_dir=cache_dir).get_company("LYB")

        assert out["name"] == "LyondellBasell Industries N.V. Class A"
        assert out["employees"] == 18970
        assert out["list_date"] == date(2010, 4, 28)
        assert out["fetched_at"] == FETCHED  # read off the cache, NOT off a clock

    def test_a_cold_cache_is_none_not_empty(self, tmp_path):
        assert CachedCompanyReader(cache_dir=str(tmp_path)).get_company("NOPE") is None

    def test_an_unreadable_cache_fails_closed_and_warns(self, tmp_path, caplog):
        cache_dir = str(tmp_path)
        with open(cache_path(cache_dir, "BAD"), "w", encoding="utf-8") as fh:
            fh.write("{not json")

        with caplog.at_level("WARNING"):
            assert CachedCompanyReader(cache_dir=cache_dir).get_company("BAD") is None

        # ADR / CLAUDE.md §5.6: a fallback is logged at WARNING, never DEBUG.
        assert any(r.levelname == "WARNING" for r in caplog.records)


# =========================================================================== #
# 6. Market cap — "~n/a Bio USD" can now be real, and stays honestly dated.
# =========================================================================== #
class TestMarketCap:
    def test_section_zero_prints_a_real_market_cap_at_a_sane_scale(self):
        md = render(_fs(company=_company()))

        assert "18.72 Mrd USD" in md  # NOT "~0.02 Bio USD"

    def test_a_mega_cap_scales_up_to_bio(self):
        raw = _load("xom_company.json")["results"]
        nvda_like = map_polygon_company(
            dict(raw, market_cap=5.1469625e12), fetched_at=FETCHED
        )
        md = render(_fs(company=nvda_like))

        assert "5.15 Bio USD" in md

    def test_the_market_cap_is_dated_by_its_FETCH_not_by_the_as_of(self):
        """It is NOT a point-in-time value: the cache is fetched now, the report may be
        dated months earlier. Saying the date is what keeps that honest."""
        md = render(_fs(company=_company()))

        assert "Stand 2026-07-17" in md

    def test_a_missing_market_cap_is_a_gap_never_a_zero(self):
        md = render(_fs(company=_company(market_cap=None)))
        section = md.split("## 0. Das Unternehmen")[1].split("## ")[0]

        assert "0.00" not in section

    def test_section_six_falls_back_to_the_real_market_cap_when_derived_is_missing(
        self,
    ):
        """§6 rendered 'Marktkapitalisierung ~n/a Bio USD' whenever there was no close
        to derive it from. The reference feed has the real number."""
        fund = {"fiscal_label": "FY2025 10-K", "market_cap_bn": None}
        md = render(_fs(company=_company(), fundamentals=fund))
        section = md.split("## 4. Fundamentaldaten")[1].split("## ")[0]

        assert "~n/a Bio USD" not in section
        assert "18.72 Mrd USD" in section
        assert "Stand 2026-07-17" in section  # never passed off as an as-of valuation

    def test_section_six_still_prefers_the_point_in_time_derived_value(self):
        """The derived value is close@as_of x shares — genuinely PIT. The fetched-now
        reference value must NOT displace it."""
        fund = {"fiscal_label": "FY2025 10-K", "market_cap_bn": 4.87}
        md = render(_fs(company=_company(), fundamentals=fund))
        section = md.split("## 4. Fundamentaldaten")[1].split("## ")[0]

        assert "4.87 Bio USD" in section


# =========================================================================== #
# 7. The audit gate must stay green — generate_report.py exits 0 iff integrity_ok,
#    so a §0 that strikes would blank the report for that symbol.
# =========================================================================== #
class TestSectionZeroAudits:
    def test_a_full_company_section_audits_clean(self):
        fs = _fs(company=_company())
        result = audit(render(fs), fs)

        assert result.integrity_ok, [(f.token, f.kind, f.note) for f in result.flags]

    def test_both_honest_gap_states_audit_clean(self):
        for company in (None, {}):
            fs = _fs(company=company)
            result = audit(render(fs), fs)
            assert result.integrity_ok, [(f.token, f.note) for f in result.flags]

    def test_the_real_xom_payload_audits_clean(self):
        """XOM's description is dense with figures ('8.4 billion cubic feet', '69%',
        '3.3 million barrels') — every one of them must trace back to the source."""
        c = map_polygon_company(
            _load("xom_company.json")["results"], fetched_at=FETCHED
        )
        fs = build_fact_set("XOM", AS_OF, _ctx(company=c))
        result = audit(render(fs), fs)

        assert result.integrity_ok, [(f.token, f.note) for f in result.flags]

    def test_a_thousands_separated_figure_in_the_prose_audits_clean(self):
        """⚠ THE defect that blanked 15 of 30 REAL reports — and neither LYB nor XOM has
        the shape: the auditor's POOL scanner reads "10,700" as 10 and 700, while its
        REPORT scanner reads it as 10700. So a figure quoted verbatim from the very
        source the section attributes traced to NOTHING and STRUCK.

        LYB's description has zero numbers and XOM's are all separator-free ('3.3
        million'), so the original fixtures were real but the SAMPLE was exactly the half
        that passes. Prose below is verbatim-shaped from Walmart's live payload.
        """
        prose = (
            "Walmart operates over 10,700 stores globally, including 4,600 namesake "
            "locations, and serves 255 million customers weekly."
        )
        c = _company(description=prose)
        fs = _fs(company=c)

        assert c["description_figures"] == [255.0, 4600.0, 10700.0]
        result = audit(render(fs), fs)
        assert result.integrity_ok, [(f.token, f.note) for f in result.flags]

    def test_a_small_dollar_figure_withholds_the_prose_not_the_report(self):
        """auditor._hit rejects EVERY candidate in [0,9] for a money claim, and it tests
        the CANDIDATE, not the claim — so no pool entry can rescue "$1". Real names: PG
        ("sales of more than $1 billion each"), DG ("priced at $1 or less").

        Withholding the prose costs those readers a paragraph; NOT withholding it costs
        them the whole report (generate_report.py exits non-zero on a strike).
        news_feed.py made the same call: failing at the boundary beats failing in the
        report.
        """
        prose = "P&G has 20 brands that generate annual sales of $1 billion each."
        c = _company(description=prose)

        assert c["description"] is None
        assert c["description_withheld"] is True
        assert c["name"] is not None  # the rest of §0 still renders

        fs = _fs(company=c)
        md = render(fs)
        section = md.split("## 0. Das Unternehmen")[1].split("## ")[0]

        # It must NOT claim the source has no description — the source HAS one.
        # NB: assert on the FALSE CLAIM, not on the bare words "keine Beschreibung",
        # which legitimately occur in other honest sentences. The substring trap again.
        assert "enthält zu diesem Titel **keine Beschreibung**" not in section
        assert "**nicht zitiert**" in section
        assert "enthält eine Beschreibung" in section  # the truth is stated
        assert audit(md, fs).integrity_ok, [
            (f.token, f.note) for f in audit(md, fs).flags
        ]

    def test_withholding_is_logged_at_warning_never_debug(self, caplog):
        prose = "A brand that generates $5 billion in annual sales."
        with caplog.at_level("WARNING"):
            map_polygon_company(dict(_lyb_raw(), description=prose), fetched_at=FETCHED)
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_ordinary_money_figures_are_not_withheld(self):
        """The guard must not swallow the normal case: only an integer $0-$9 mantissa is
        unprovable. '$25 billion' / '$1.2 billion' are sourceable and must survive."""
        for prose in (
            "The company recorded a $25 billion charge in 2025.",
            "Revenue reached $1.2 billion last year.",
            "It operates 1,250 stores totalling 30,000 square feet.",
        ):
            c = _company(description=prose)
            assert c["description"] == prose, f"wrongly withheld: {prose!r}"
            assert c["description_withheld"] is False
            fs = _fs(company=c)
            assert audit(render(fs), fs).integrity_ok, prose

    def test_no_raw_none_leaks_into_the_page(self):
        for company in (None, {}, _company(description=None, sic_description=None)):
            md = render(_fs(company=company))
            assert "None" not in md


# =========================================================================== #
# 8. Determinism — the pure-core contract.
# =========================================================================== #
def test_same_facts_render_byte_identically_across_independent_builds():
    """⚠ `render(fs) == render(fs)` on ONE object was near-tautological — same object,
    same process, so it could never catch a dict-ordering or clock leak. Build the
    FactSet twice, independently, with the company dict's keys inserted in REVERSED
    order: same facts, same bytes, or the renderer is reading insertion order."""
    forward = map_polygon_company(_lyb_raw(), fetched_at=FETCHED)
    reversed_dict = {k: forward[k] for k in reversed(list(forward))}
    assert list(reversed_dict) != list(forward)  # the probe is real

    a = render(build_fact_set("LYB", AS_OF, _ctx(company=forward)))
    b = render(build_fact_set("LYB", AS_OF, _ctx(company=reversed_dict)))

    assert a == b
