# tests/unit/test_report_news_reader.py
# R7 — attach a real catalyst feed to the report.
#
# BEFORE THIS: EngineFactSetReader.get_news() returned None for every symbol, forever —
# no production caller passed one. #2151 made that HONEST ("keine Nachrichtenquelle
# angebunden", instead of claiming "no relevant news" and citing a feed nobody called).
# This makes it PRESENT.
#
# ⚠ THE TRAP THIS FILE EXISTS TO AVOID — the fixture is NOT the producer:
#   tests/fixtures/report/nvda_news.json is ALREADY MAPPED. It carries
#   {title, published_utc, publisher, url} — exactly NewsItem's shape. The LIVE endpoint
#   does not:
#     * publisher is a DICT {name, homepage_url, logo_url, favicon_url} — not a string
#     * the link field is `article_url` — there is no `url`
#     * published_utc ends in "Z", not "+00:00"
#   A reader written against that fixture renders a Python dict where the publisher
#   should be, and drops every link. That is exactly how the fundamentals agents shipped
#   dead this morning: consumer written against an invented schema, tests green because
#   the fixture agreed with the invention.
#   So the mapping below is pinned against the REAL response shape, verified live.

from __future__ import annotations

import logging
from datetime import date

import pytest

from core.report.fact_set import NewsItem
from core.report.news_feed import map_polygon_article

# The real /v2/reference/news article shape, verified against the live endpoint.
_RAW = {
    "id": "abc123",
    "title": "Down 50% From Its High, Is CoreWeave a Bargain?",
    "author": "Some Author",
    "published_utc": "2026-07-16T17:27:00Z",
    "article_url": "https://www.fool.com/investing/2026/07/16/down-50/",
    "image_url": "https://example.com/img.png",
    "description": "A long blurb we do not render.",
    "keywords": ["AI"],
    "tickers": ["NVDA", "CRWV"],
    "insights": [{"sentiment": "negative"}],
    "publisher": {
        "name": "The Motley Fool",
        "homepage_url": "https://www.fool.com/",
        "logo_url": "https://example.com/logo.svg",
        "favicon_url": "https://example.com/fav.ico",
    },
}


class TestTheRealSchemaIsWhatWeMap:
    def test_a_real_article_becomes_a_provenanced_news_item(self):
        item = map_polygon_article(_RAW)

        assert isinstance(item, NewsItem)
        assert item.title == "Down 50% From Its High, Is CoreWeave a Bargain?"
        assert item.published_utc == "2026-07-16T17:27:00Z"
        assert item.url == "https://www.fool.com/investing/2026/07/16/down-50/"

    def test_publisher_is_flattened_from_the_dict(self):
        """⚠ The trap: raw `publisher` is a DICT. Passing it through renders
        "{'name': 'The Motley Fool', 'homepage_url': ...}" into the report."""
        item = map_polygon_article(_RAW)
        assert item.publisher == "The Motley Fool"

    def test_the_link_comes_from_article_url_not_url(self):
        """⚠ The other trap: there is no `url` key on the live response. A reader
        written against the (already-mapped) fixture drops every link — and §5's whole
        point is that each headline carries a source the reader can open."""
        assert "url" not in _RAW
        assert map_polygon_article(_RAW).url.startswith("https://www.fool.com/")


class TestAnArticleWeCannotProvenanceIsDropped:
    """§5 renders each item as a sourced bullet. An item missing its provenance is not a
    catalyst, it is a rumour — and the auditor would strike it. Better dropped at the
    boundary than rendered and flagged."""

    @pytest.mark.parametrize("missing", ["title", "published_utc", "article_url"])
    def test_missing_load_bearing_field_is_dropped(self, missing):
        raw = {k: v for k, v in _RAW.items() if k != missing}
        assert map_polygon_article(raw) is None

    def test_a_missing_publisher_is_not_fatal(self):
        """The publisher is attribution, not provenance — the URL carries that. An
        article without one is still checkable, so it survives with an honest label."""
        raw = {k: v for k, v in _RAW.items() if k != "publisher"}
        item = map_polygon_article(raw)
        assert item is not None
        assert item.publisher == "unbekannt"

    def test_a_string_publisher_still_works(self):
        """Defensive: if the API ever flattens it (or a cache stores it mapped), do not
        crash on a string where a dict was expected."""
        item = map_polygon_article({**_RAW, "publisher": "Reuters"})
        assert item.publisher == "Reuters"

    def test_garbage_is_dropped_not_raised(self):
        assert map_polygon_article({}) is None
        assert map_polygon_article(None) is None


class TestTheFeedActuallyReachesTheReport:
    """⚠ THE TESTS THAT WERE MISSING — and the omission shipped the feature DEAD.

    The first cut of this file tested the mapper and stopped. The mapper was correct;
    the feature was still a no-op, because the only production construction site
    (`specialist_registry.py:328`) passes just the gemini key, and `polygon_api_key`
    defaulted to "". So the fetch returned before any I/O and §5 rendered "keine
    Nachrichtenquelle angebunden" for every symbol, forever.

    A verified schema plus an unverified CALLER is still a dead feature. These tests pin
    the caller.
    """

    def test_an_agent_built_the_way_the_registry_builds_it_has_a_key(self, monkeypatch):
        """Constructs EXACTLY as specialist_registry.py:328 does — symbol + gemini key,
        no polygon key. This is the test that would have caught the dead feature."""
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", "test-key-123", raising=False)
        from core.stock_specialist import StockSpecialistAgent

        agent = StockSpecialistAgent("NVDA", "fake-gemini-key")

        assert agent._polygon_api_key == "test-key-123", (
            "the specialist resolved no Polygon key — _fetch_polygon_news will return "
            "before any I/O and §5 is dead for every symbol"
        )

    def test_no_configured_key_degrades_honestly_rather_than_crashing(
        self, monkeypatch
    ):
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", None, raising=False)
        from core.stock_specialist import StockSpecialistAgent

        agent = StockSpecialistAgent("NVDA", "fake-gemini-key")
        assert agent._polygon_api_key == ""
        assert agent._news_items is None  # -> §5 honest gap, no crash

    def test_an_explicit_empty_key_still_forces_a_keyless_agent(self, monkeypatch):
        """ "" must stay meaningful: it is how a caller says 'no feed on purpose'."""
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", "test-key-123", raising=False)
        from core.stock_specialist import StockSpecialistAgent

        assert (
            StockSpecialistAgent("NVDA", "g", polygon_api_key="")._polygon_api_key == ""
        )

    def test_the_items_reach_the_report_reader(self, monkeypatch):
        """The side-channel must actually arrive at the FactSet reader — otherwise the
        articles are retained and then dropped on the floor one call later."""
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", "k", raising=False)
        monkeypatch.setattr(
            config.get_config(), "REPORT_GENERATOR_V2_ENABLED", True, raising=False
        )
        from core.stock_specialist import StockSpecialistAgent

        agent = StockSpecialistAgent("NVDA", "g")
        agent._news_items = [map_polygon_article(_RAW)]

        captured = {}

        class _Spy:
            def __init__(self, as_of, **kw):
                captured.update(kw)

        import core.report.engine_reader as er

        monkeypatch.setattr(er, "EngineFactSetReader", _Spy)
        agent._default_report_reader(date(2026, 7, 16), {})

        assert (
            "news" in captured
        ), "the reader was built without a news= argument at all"
        assert captured["news"] == agent._news_items


class TestAllDroppedIsNotNoNews:
    """⚠ F2 — the three-state invariant. Collapsing 'unusable feed' into [] makes §5
    state 'keine relevanten Meldungen im Fenster' — a FALSE claim about the world that
    carries a [src:] badge and passes the auditor with 0 flags (the auditor checks that
    a citation exists, never that anything survived the mapper)."""

    def _agent(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", "k", raising=False)
        from core.stock_specialist import StockSpecialistAgent

        return StockSpecialistAgent("NVDA", "g")

    def test_a_feed_we_cannot_use_at_all_is_not_no_news(self, monkeypatch):
        agent = self._agent(monkeypatch)
        # 10 articles, every one missing its link — e.g. a schema rename.
        junk = [
            {"title": f"t{i}", "published_utc": "2026-07-16T10:00:00Z"}
            for i in range(10)
        ]

        assert agent._reconcile_news_items(junk) is None, (
            "10 unusable articles collapsed to [] -> §5 would claim 'no relevant news' "
            "while the feed was actually broken"
        )

    def test_an_empty_feed_is_a_true_no_news(self, monkeypatch):
        assert self._agent(monkeypatch)._reconcile_news_items([]) == []

    def test_a_partial_drop_keeps_the_survivors_and_warns(self, monkeypatch, caplog):
        agent = self._agent(monkeypatch)
        mixed = [_RAW, {"title": "no link", "published_utc": "2026-07-16T10:00:00Z"}]

        with caplog.at_level(logging.WARNING):
            items = agent._reconcile_news_items(mixed)

        assert len(items) == 1
        assert "kept 1 of 2" in caplog.text, "a silent partial drop reads as exhaustive"

    def test_a_non_list_payload_is_unusable_not_empty(self, monkeypatch):
        assert self._agent(monkeypatch)._reconcile_news_items({"error": "x"}) is None


class TestStaleNewsIsNeverRenderedAsCurrent:
    """⚠ F3 — the agent is CACHED ACROSS CYCLES by the registry, so _news_items outlives
    a research pass. A fetch that fails this cycle must not leave last cycle's articles
    in place to be stamped with today's as_of."""

    @pytest.mark.asyncio
    async def test_a_failed_fetch_clears_the_previous_cycles_items(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "POLYGON_API_KEY", "", raising=False)
        from core.stock_specialist import StockSpecialistAgent

        agent = StockSpecialistAgent("NVDA", "g")
        agent._news_items = [map_polygon_article(_RAW)]  # cycle 1 succeeded

        await agent._fetch_polygon_news()  # cycle 2: no key -> early return

        assert (
            agent._news_items is None
        ), "last cycle's news survived a failed fetch and would render as current"


class TestPitCompatibility:
    """The FactSet PIT-filters on published_utc. The mapping must produce something the
    filter can actually parse — a Z-suffixed stamp is the live format."""

    def test_the_z_suffix_survives_the_pit_filter(self):
        from core.report.fact_set import InMemoryDataContext, build_fact_set

        item = map_polygon_article(_RAW)  # published 2026-07-16
        ctx = InMemoryDataContext(
            bars=[(date(2025, 1, 1), 100.0 + i * 0.1) for i in range(300)],
            lstm_series=[(date(2026, 7, 16), 4.1)],
            lstm_cross_section={"NVDA": 4.1},
            news=[item],
        )
        fs = build_fact_set("NVDA", date(2026, 7, 16), ctx)

        assert len(fs.news_items.value) == 1, (
            "the live Z-suffixed timestamp was dropped by the PIT filter — the article "
            "is in-window and must survive"
        )

    def test_a_future_article_is_still_dropped(self):
        """The PIT guarantee must keep holding on the real format: no lookahead."""
        from core.report.fact_set import InMemoryDataContext, build_fact_set

        item = map_polygon_article(_RAW)  # 2026-07-16
        ctx = InMemoryDataContext(
            bars=[(date(2025, 1, 1), 100.0 + i * 0.1) for i in range(300)],
            lstm_series=[(date(2026, 7, 1), 4.1)],
            lstm_cross_section={"NVDA": 4.1},
            news=[item],
        )
        fs = build_fact_set("NVDA", date(2026, 7, 1), ctx)  # as-of BEFORE the article

        assert fs.news_items.value == []
        assert "news feed[NVDA]" in fs.news_items.src  # asked, nothing qualified
