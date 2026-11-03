# tests/unit/test_report_news_provenance_honesty.py
# "Keine relevanten Meldungen" may not carry a source badge for a feed that never ran.
#
# THE DEFECT (fact_set.py:375, verified):
#     news_src = f"news feed[{symbol}], published_utc <= {as_of_d.isoformat()}"
# — set UNCONDITIONALLY, with no None-branch. Eleven lines above, fundamentals does it
# correctly: no feed -> "fundamentals feed not wired (RPT-2 delivers it later)".
#
# So a report with no news feed attached today states "Keine relevanten Meldungen im
# Fenster", cites a feed as its evidence, and passes the mechanical audit with 0 flags —
# for a stock that may have had five headlines that day. The auditor cannot catch it: it
# verifies that a [src:] string EXISTS, never that the feed was actually asked.
#
# This is not the same thing as "news is not wired yet". An unwired feed is a building
# site; an unwired feed wearing a provenance badge is a FALSE STATEMENT THAT LOOKS
# AUDITED — the more dangerous of the two for a BaFin-auditable note, because it reads
# as diligence.
#
# NB the news sources exist (Polygon live in the specialist path; Google-News-RSS behind
# SPECIALIST_NEWS_V2, core/specialist/news.py, T3/#1267). The report is simply attached
# to neither. That wiring is separate, known work — this fix must land regardless of
# when it happens, because until then the badge certifies a claim nobody checked.

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set

_AS_OF = date(2026, 7, 15)


def _ctx(news=None):
    """news=None -> NO feed attached (the shipped default). news=[...] -> a feed
    answered. The whole point of the fix is that these are different claims."""
    return InMemoryDataContext(
        bars=[(date(2025, 1, 1), 100.0 + i * 0.1) for i in range(300)],
        lstm_series=[(_AS_OF, 4.1)],
        lstm_cross_section={"NVDA": 4.1},
        news=news,
    )


def _item(days_ago=1):
    ts = datetime(2026, 7, 15, tzinfo=timezone.utc) - timedelta(days=days_ago)
    return NewsItem(
        title="NVIDIA Sets Conference Call for First-Quarter Results",
        published_utc=ts.isoformat(),
        publisher="GlobeNewswire",
        url="https://example.com/nvda-q1",
    )


class TestNoNewsMustNotCiteAFeed:
    def test_no_feed_attached_does_not_claim_a_feed_was_read(self):
        """THE assertion, and the shipped default: no catalyst source is attached to the
        report at all, so the src must not assert that a feed was queried and came back
        empty. That is a claim about the WORLD, not about our plumbing, and we have no
        evidence for it."""
        fs = build_fact_set("NVDA", _AS_OF, _ctx(news=None))

        assert fs.news_items.value == []
        assert "news feed[NVDA]" not in fs.news_items.src, (
            "the report cited a news feed as evidence for 'no relevant news' — but no "
            "feed was attached; the badge certifies a claim nobody checked"
        )

    def test_the_gap_is_named_the_way_fundamentals_names_it(self):
        """Consistency with the correct precedent eleven lines above in the same
        function: say the feed is not wired, do not imply it answered."""
        fs = build_fact_set("NVDA", _AS_OF, _ctx(news=None))
        assert "not wired" in fs.news_items.src

    def test_an_attached_feed_that_returns_nothing_is_a_sourced_finding(self):
        """The distinction the old code could not make, and the reason a blanket
        'never cite on empty' would be its OWN dishonesty: a feed that WAS asked and
        genuinely had nothing is a real, evidenced finding. The citation belongs there.
        """
        fs = build_fact_set("NVDA", _AS_OF, _ctx(news=[]))

        assert fs.news_items.value == []
        assert "news feed[NVDA]" in fs.news_items.src
        assert "not wired" not in fs.news_items.src


class TestRealNewsStillCarriesItsProvenance:
    def test_news_present_keeps_the_feed_citation(self):
        """The guard must not swallow the case it exists to protect: once a feed IS
        attached and returns items, the PIT-bounded citation is exactly right."""
        fs = build_fact_set("NVDA", _AS_OF, _ctx(news=[_item()]))

        assert len(fs.news_items.value) == 1
        assert "news feed[NVDA]" in fs.news_items.src
        assert "published_utc <=" in fs.news_items.src

    def test_a_feed_whose_items_are_all_pit_dropped_is_not_a_gap(self):
        """The subtle case that keeps this honest: a feed WAS attached and returned
        items, but the PIT filter dropped every one. Then 'no relevant news in the
        window' is a real, sourced finding — and the citation belongs there.

        NB the PIT rule drops news published AFTER as_of (lookahead), not old news — a
        400-day-old headline is legitimately in-window. Hence the future timestamp: it
        is the only thing the filter actually removes."""
        lookahead = _item(days_ago=-30)  # published 30 days AFTER as_of
        fs = build_fact_set("NVDA", _AS_OF, _ctx(news=[lookahead]))

        assert fs.news_items.value == []
        assert "news feed[NVDA]" in fs.news_items.src
        assert "not wired" not in fs.news_items.src


class TestTheRenderedSentenceMatchesTheEvidence:
    """The src being honest is not enough — the SENTENCE above it is what the user
    reads. 'Keine relevanten Meldungen im Fenster [src: no feed attached]' contradicts
    itself; a reader takes the claim and ignores the badge."""

    def test_no_feed_does_not_render_a_finding_about_the_world(self):
        from core.report.render import render

        md = render(build_fact_set("NVDA", _AS_OF, _ctx(news=None)), narration=None)

        assert (
            "Keine relevanten Meldungen im Fenster" not in md
        ), "with no feed attached this is a claim we have no evidence for"
        assert "keine Nachrichtenquelle angebunden" in md
        assert "kein Befund" in md

    def test_an_attached_empty_feed_does_render_the_finding(self):
        """Asked and nothing qualified IS a finding — and must still read as one."""
        from core.report.render import render

        md = render(build_fact_set("NVDA", _AS_OF, _ctx(news=[])), narration=None)

        assert "Keine relevanten Meldungen im Fenster" in md
        assert "keine Nachrichtenquelle angebunden" not in md
