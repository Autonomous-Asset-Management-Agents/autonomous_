# core/specialist/news.py + stock_specialist - Google+Polygon news merge (RPAR T3)
# TDD Red -> Green. implementation_plan TASK_T3_news_parity_implementation_plan.md.
#
# Flag SPECIALIST_NEWS_V2 (default OFF) gates a Google-News-RSS source that is merged with
# the Polygon headlines already gathered in research(). At OFF the behaviour is byte-identical
# to today (Polygon-only, no RSS fetch, no merge reached, _fetch_google_news NOT called).
#
# Invariants under test:
#   1. Dormancy:  flag OFF -> recent_headlines == polygon_news[:8]; _fetch_google_news
#      NOT called (assert_not_called); DTO byte-identical.
#   2. NEWS-8:    for a FIXED _gemini_synthesize text, the deterministic _build_report math
#      (sentiment_score / recommendation / confidence / reasons / escalate / escalate_reason)
#      is IDENTICAL flag-ON vs flag-OFF - the score math never reads recent_headlines.
#   4. Purity:    merge_headlines does not mutate its inputs, returns a NEW List[str].
#
# Async interfaces (research, _fetch_google_news) use AsyncMock (CODING_POLICY §5.2).

import importlib.util
import os
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config import get_config
from core.specialist.news import merge_headlines
from core.stock_specialist import StockSpecialistAgent


def _agent():
    return StockSpecialistAgent("AAPL", gemini_api_key="x")


def _load_oss_config():
    """Load config.oss.py explicitly (mirrors test_config_oss_get_secret_str)."""
    abs_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "config.oss.py")
    )
    spec = importlib.util.spec_from_file_location("config_oss_news_probe", abs_path)
    assert spec and spec.loader, f"Could not find config.oss.py at {abs_path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A tiny but valid Google-News-style RSS payload (two items).
_RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>AAPL - Google News</title>
  <item><title>Apple unveils new chip</title><link>https://g/1</link></item>
  <item><title>Apple beats earnings estimates</title><link>https://g/2</link></item>
</channel></rss>"""


# ---------------------------------------------------------------------------
# 0. Config flag exists and defaults False (dormant) - both editions
# ---------------------------------------------------------------------------
def test_news_v2_flag_default_false():
    assert get_config().SPECIALIST_NEWS_V2 is False


def test_news_v2_flag_default_false_oss():
    oss = _load_oss_config()
    assert oss.SPECIALIST_NEWS_V2 is False


# ---------------------------------------------------------------------------
# 1. merge_headlines - pure, Google-first order, dedup, cap, empty-tolerant
# ---------------------------------------------------------------------------
def test_merge_google_first_order():
    polygon = ["Polygon one", "Polygon two"]
    google = ["Google one", "Google two"]
    out = merge_headlines(polygon, google)
    # Google-first ordering (reconciled Bundle rule), then Polygon.
    assert out == ["Google one", "Google two", "Polygon one", "Polygon two"]


def test_merge_dedup_case_insensitive_first_wins():
    polygon = ["Apple Beats Earnings", "Polygon unique"]
    google = ["apple beats earnings", "Google unique"]
    out = merge_headlines(polygon, google)
    # The Google occurrence ("apple beats earnings") comes first and wins; the
    # case-insensitively identical Polygon title is dropped.
    assert out == ["apple beats earnings", "Google unique", "Polygon unique"]
    # Exactly one entry for the duplicated title (case-insensitive).
    assert sum(1 for h in out if h.lower() == "apple beats earnings") == 1


def test_merge_caps_at_ten():
    google = [f"G{i}" for i in range(8)]
    polygon = [f"P{i}" for i in range(8)]
    out = merge_headlines(polygon, google)
    assert len(out) == 10
    # Google-first -> the first 8 are all Google, then the first 2 Polygon.
    assert out == [f"G{i}" for i in range(8)] + ["P0", "P1"]


def test_merge_empty_both():
    assert merge_headlines([], []) == []


def test_merge_empty_google_returns_polygon():
    polygon = [f"P{i}" for i in range(12)]
    out = merge_headlines(polygon, [])
    # Polygon-only, capped at 10, order preserved.
    assert out == [f"P{i}" for i in range(10)]


def test_merge_empty_polygon_returns_google():
    google = ["G one", "G two"]
    assert merge_headlines([], google) == ["G one", "G two"]


def test_merge_is_pure_no_mutation():
    polygon = ["P one", "P two"]
    google = ["G one", "G two"]
    poly_before = list(polygon)
    goog_before = list(google)
    out = merge_headlines(polygon, google)
    # Inputs untouched (deep-equal before/after) ...
    assert polygon == poly_before
    assert google == goog_before
    # ... and the return is a NEW list object (not an aliased input).
    assert out is not polygon
    assert out is not google


# ---------------------------------------------------------------------------
# 2. merge output is List[str] (T2 select_headlines / prompt L713 contract)
# ---------------------------------------------------------------------------
def test_merged_headlines_are_str_list():
    out = merge_headlines(["P"], ["G"])
    assert isinstance(out, list)
    assert all(isinstance(h, str) for h in out)


# ---------------------------------------------------------------------------
# 3. _fetch_google_news - flag-FIRST: OFF -> [] without any network call
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_google_news_flag_off_no_network():
    agent = _agent()
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=False)
    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient"
    ) as client_cls:
        out = await agent._fetch_google_news()
    assert out == []
    client_cls.assert_not_called()  # flag-first: no httpx client constructed


# ---------------------------------------------------------------------------
# 4. _fetch_google_news - flag-ON: parses RSS -> List[str]
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_google_news_flag_on_parses_rss():
    agent = _agent()
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=True)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = _RSS_SAMPLE
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient", return_value=client
    ):
        out = await agent._fetch_google_news()

    assert out == ["Apple unveils new chip", "Apple beats earnings estimates"]
    assert all(isinstance(h, str) for h in out)


# ---------------------------------------------------------------------------
# 4b. _fetch_google_news - HTML entities in RSS titles are unescaped (#1312 F-01)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_google_news_unescapes_html_entities():
    agent = _agent()
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=True)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = (
        "<rss><channel>"
        "<item><title>Apple &amp; Tesla&#39;s rally &lt;continues&gt;</title></item>"
        "</channel></rss>"
    )
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient", return_value=client
    ):
        out = await agent._fetch_google_news()

    # Raw entities (&amp; &#39; &lt; &gt;) must not leak into the LLM prompt.
    assert out == ["Apple & Tesla's rally <continues>"]


# ---------------------------------------------------------------------------
# 4c. _fetch_google_news - ticker is URL-encoded in the RSS query (#1312 F-03)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_google_news_url_encodes_symbol():
    agent = StockSpecialistAgent("^GSPC", gemini_api_key="x")  # caret is URL-special
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=True)

    resp = MagicMock()
    resp.status_code = 200
    resp.text = "<rss></rss>"
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient", return_value=client
    ):
        await agent._fetch_google_news()

    called_url = client.get.call_args.args[0]
    assert "q=%5EGSPC" in called_url  # caret encoded
    assert "q=^GSPC" not in called_url  # raw caret must not reach the query


# ---------------------------------------------------------------------------
# 5. _fetch_google_news - flag-ON errors -> [] (logged WARNING, never raises)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_google_news_error_returns_empty():
    agent = _agent()
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=True)

    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("boom"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient", return_value=client
    ):
        out = await agent._fetch_google_news()
    assert out == []


@pytest.mark.anyio
async def test_fetch_google_news_non_200_returns_empty():
    agent = _agent()
    cfg = types.SimpleNamespace(SPECIALIST_NEWS_V2=True)

    resp = MagicMock()
    resp.status_code = 503
    resp.text = ""
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)

    with patch("core.stock_specialist.get_config", return_value=cfg), patch(
        "httpx.AsyncClient", return_value=client
    ):
        out = await agent._fetch_google_news()
    assert out == []


# ---------------------------------------------------------------------------
# 6. research() flag-OFF -> recent_headlines == polygon_news[:8],
#    _fetch_google_news NOT called (dormancy / byte-identity, Papa-Kern)
# ---------------------------------------------------------------------------
_POLYGON = [f"PNews {i}" for i in range(10)]  # 10 -> sliced to 8 downstream


def _patch_all_fetchers(agent):
    """Patch every gatherer to a fixed, deterministic value so research() is hermetic."""
    agent._fetch_edgar_form4 = AsyncMock(return_value=[])
    agent._fetch_edgar_8k = AsyncMock(return_value=[])
    agent._fetch_edgar_13d = AsyncMock(return_value=[])
    agent._fetch_congressional_trades = AsyncMock(return_value=[])
    agent._fetch_polygon_news = AsyncMock(return_value=list(_POLYGON))
    agent._fetch_wiki_pageviews = AsyncMock(return_value={})
    agent._fetch_reddit_mentions = AsyncMock(return_value={})
    agent._fetch_finra_short_interest = AsyncMock(return_value=None)
    agent._fetch_google_trends = AsyncMock(return_value=None)
    agent._fetch_ml_prediction = AsyncMock(return_value=None)
    agent._gemini_synthesize = AsyncMock(return_value={"text": ""})


@pytest.mark.anyio
async def test_research_flag_off_recent_headlines_polygon_only():
    agent = _agent()
    _patch_all_fetchers(agent)
    agent._fetch_google_news = AsyncMock(return_value=["should not appear"])

    captured = {}
    real_build = agent._build_report

    def _capture(*args, **kwargs):
        captured["gathered"] = args[0]
        return real_build(*args, **kwargs)

    cfg = types.SimpleNamespace(
        SPECIALIST_NEWS_V2=False, ML_SENTIMENT_BLEND_ENABLED=False
    )
    with patch("core.stock_specialist.get_config", return_value=cfg):
        agent._build_report = _capture
        await agent.research()

    assert captured["gathered"]["recent_headlines"] == _POLYGON[:8]
    # Flag-first dormancy: the Google fetcher is never reached.
    agent._fetch_google_news.assert_not_called()


# ---------------------------------------------------------------------------
# 7. NEWS-8: fixed _gemini_synthesize text -> score math identical ON vs OFF
# ---------------------------------------------------------------------------
_FIXED_TEXT = (
    "SUMMARY: Strong quarter.\n"
    "SIGNALS: Insider buys.\n"
    "OUTLOOK: bullish\n"
    "SCORE: 71\n"
    "- Solid fundamentals\n"
)


async def _run_research_capture_report(agent, flag_on):
    _patch_all_fetchers(agent)
    agent._fetch_google_news = AsyncMock(
        return_value=["Google A", "Google B", "Google C"]
    )
    agent._gemini_synthesize = AsyncMock(return_value={"text": _FIXED_TEXT})

    cfg = types.SimpleNamespace(
        SPECIALIST_NEWS_V2=flag_on, ML_SENTIMENT_BLEND_ENABLED=False
    )
    with patch("core.stock_specialist.get_config", return_value=cfg):
        return await agent.research()


@pytest.mark.anyio
async def test_news_v2_no_decision_math_delta():
    off = await _run_research_capture_report(_agent(), flag_on=False)
    on = await _run_research_capture_report(_agent(), flag_on=True)

    # The deterministic _build_report math is invariant to the headline SOURCE
    # (it never reads recent_headlines) - NEWS-8.
    assert on.sentiment_score == off.sentiment_score
    assert on.recommendation == off.recommendation
    assert on.confidence == off.confidence
    assert on.reasons == off.reasons
    assert on.escalate == off.escalate
    assert on.escalate_reason == off.escalate_reason


# ---------------------------------------------------------------------------
# 8. Flag-ON integration: recent_headlines carries merged Google+Polygon titles
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_research_flag_on_merges_google_and_polygon():
    agent = _agent()
    _patch_all_fetchers(agent)
    agent._fetch_polygon_news = AsyncMock(return_value=["Polygon headline"])
    agent._fetch_google_news = AsyncMock(return_value=["Google headline"])

    captured = {}
    real_build = agent._build_report

    def _capture(*args, **kwargs):
        captured["gathered"] = args[0]
        return real_build(*args, **kwargs)

    cfg = types.SimpleNamespace(
        SPECIALIST_NEWS_V2=True, ML_SENTIMENT_BLEND_ENABLED=False
    )
    with patch("core.stock_specialist.get_config", return_value=cfg):
        agent._build_report = _capture
        await agent.research()

    headlines = captured["gathered"]["recent_headlines"]
    assert "Google headline" in headlines
    assert "Polygon headline" in headlines
    # Google-first contract preserved through research().
    assert headlines[0] == "Google headline"
    agent._fetch_google_news.assert_awaited_once()
