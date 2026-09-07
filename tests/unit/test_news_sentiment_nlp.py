# tests/unit/test_news_sentiment_nlp.py
# RTR-1 (#1948) — NewsSentiment: echtes NLP-Sentiment statt headline-losem LLM-Guess
# (TDD Red→Green, Plan §8).
#
# Scope of this file (the NLP module + the free point-in-time news producer):
#   R1  FinBERT score from headlines (pipeline mocked/deterministic) → score ∈ [0,1]
#   R2  empty headlines → None (no signal, agent abstains)
#   R3  FinBERT unavailable → vendored-VADER-lexicon fallback + WARNING (§5.6)
#   R4  both backends unavailable → None + WARNING
#   R5  details carry title+source+label for the MiFID audit trail
#   R10 news_headlines is a DECLARED SymbolEvalState channel (LangGraph keeps it)
#   R11 point-in-time filter drops published_utc > current_time (no look-ahead)
#   R12 no-paid-API guard: producer touches ONLY Google-News-RSS, never Polygon
#   +   config parity: 3 keys in config.py AND config.oss.py, identical defaults
#       (False / 8 / "finbert") — BORA dual-edition guarantee.
#
# Agent-side branch tests (flag off/on, cache content-hash, abstain) live in
# tests/unit/test_news_sentiment_agent.py.

from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"
_NLP_DIR = _AI_BOT / "core" / "nlp"


def _headlines():
    return [
        {
            "title": "AAPL beats Q3 estimates, record profit and strong growth",
            "published_utc": "2026-07-22T12:00:00+00:00",
            "source": "reuters",
        },
        {
            "title": "Analysts praise robust iPhone demand",
            "published_utc": "2026-07-22T13:30:00+00:00",
            "source": "bloomberg",
        },
    ]


def _fake_finbert_pipe(label: str = "positive", prob: float = 0.9):
    """Deterministic stand-in for a transformers text-classification pipeline."""

    def pipe(texts):
        return [{"label": label, "score": prob} for _ in texts]

    return pipe


# ---------------------------------------------------------------------------
# R1–R5 — core/nlp/news_sentiment.py scoring cascade
# ---------------------------------------------------------------------------


def test_r1_finbert_score_from_headlines_deterministic(monkeypatch):
    """R1: with a (mocked) FinBERT pipeline, score comes from the classifier,
    lands in [0,1] and is deterministic (same input → same output)."""
    from core.nlp import news_sentiment as ns

    monkeypatch.setattr(
        ns, "_load_finbert_pipeline", lambda model: _fake_finbert_pipe("positive", 0.9)
    )
    ns._reset_backends_for_tests()
    r1 = ns.score_headlines(_headlines(), model="finbert")
    r2 = ns.score_headlines(_headlines(), model="finbert")
    assert r1 is not None
    assert 0.0 <= r1.score <= 1.0
    assert r1.score > 0.5, "all-positive classification must score bullish"
    assert r1.backend == "FinBERT"
    assert r1.score == r2.score, "argmax classification must be deterministic"


def test_r1b_finbert_negative_scores_bearish(monkeypatch):
    from core.nlp import news_sentiment as ns

    monkeypatch.setattr(
        ns, "_load_finbert_pipeline", lambda model: _fake_finbert_pipe("negative", 0.8)
    )
    ns._reset_backends_for_tests()
    res = ns.score_headlines(_headlines(), model="finbert")
    assert res is not None
    assert res.score < 0.5


def test_r2_empty_headlines_returns_none():
    from core.nlp import news_sentiment as ns

    assert ns.score_headlines([], model="finbert") is None
    assert ns.score_headlines(None, model="finbert") is None


def test_r3_finbert_missing_falls_back_to_vader(monkeypatch, caplog):
    """R3: FinBERT assets/dep missing → vendored VADER lexicon scores instead of a
    total abstain, and the degradation is logged at WARNING (§5.6, never DEBUG)."""
    from core.nlp import news_sentiment as ns

    monkeypatch.setattr(
        ns,
        "_load_finbert_pipeline",
        MagicMock(side_effect=ImportError("no transformers")),
    )
    ns._reset_backends_for_tests()
    with caplog.at_level(logging.WARNING):
        res = ns.score_headlines(_headlines(), model="finbert")
    assert res is not None
    assert res.backend == "VADER-Lexikon"
    assert 0.0 <= res.score <= 1.0
    assert res.score > 0.5, "'beats estimates/record profit/strong' must score bullish"
    assert any(
        "FinBERT" in rec.message and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), "FinBERT→VADER degradation must be WARNING-visible"


def test_r3b_vader_orders_positive_above_negative():
    """Vendored lexicon sanity: clearly bullish text scores above clearly bearish."""
    from core.nlp import news_sentiment as ns

    pos = ns.score_headlines(
        [{"title": "Great record profit, strong growth", "source": "r"}],
        model="vader",
    )
    neg = ns.score_headlines(
        [{"title": "Fraud investigation, shares plunge amid crisis", "source": "r"}],
        model="vader",
    )
    assert pos is not None and neg is not None
    assert pos.backend == "VADER-Lexikon"
    assert pos.score > 0.5 > neg.score


def test_r4_both_backends_missing_returns_none(monkeypatch, caplog):
    from core.nlp import news_sentiment as ns

    monkeypatch.setattr(
        ns,
        "_load_finbert_pipeline",
        MagicMock(side_effect=ImportError("no transformers")),
    )
    monkeypatch.setattr(
        ns, "_load_vader_lexicon", MagicMock(side_effect=OSError("lexicon gone"))
    )
    ns._reset_backends_for_tests()
    with caplog.at_level(logging.WARNING):
        res = ns.score_headlines(_headlines(), model="finbert")
    assert res is None, "no backend → no signal (agent must abstain, never guess)"
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


def test_r5_details_contain_title_source_label(monkeypatch):
    from core.nlp import news_sentiment as ns

    monkeypatch.setattr(
        ns, "_load_finbert_pipeline", lambda model: _fake_finbert_pipe("positive", 0.9)
    )
    ns._reset_backends_for_tests()
    res = ns.score_headlines(_headlines(), model="finbert")
    assert res is not None
    assert len(res.details) == 2
    for det, src in zip(res.details, _headlines()):
        assert det["title"] == src["title"]
        assert det["source"] == src["source"]
        assert det["label"] in {"pos", "neg", "neu"}
        assert 0.0 <= det["prob"] <= 1.0


# ---------------------------------------------------------------------------
# R10/R11/R12 — point-in-time producer (core/nlp/headlines.py) + state channel
# ---------------------------------------------------------------------------

_RSS = """<?xml version="1.0"?><rss><channel>
<item><title>AAPL beats Q3 estimates</title>
<pubDate>Wed, 22 Jul 2026 12:00:00 GMT</pubDate>
<source url="https://reuters.com">Reuters</source></item>
<item><title>Future leak headline</title>
<pubDate>Thu, 23 Jul 2026 09:00:00 GMT</pubDate>
<source url="https://x.com">X</source></item>
<item><title>No date headline</title>
<source url="https://y.com">Y</source></item>
</channel></rss>"""


def test_r10_news_headlines_is_declared_state_channel():
    """R10: LangGraph only keeps DECLARED channels (graph.py precedence:
    _portfolio_context/features) — news_headlines must be a SymbolEvalState key."""
    from core.orchestration.graph import SymbolEvalState

    assert "news_headlines" in SymbolEvalState.__annotations__


def test_r11_point_in_time_filter_drops_future_and_undated():
    """R11: only published_utc <= current_time survives; items without a parseable
    pubDate are DROPPED (cannot prove point-in-time → precision over recall)."""
    from core.nlp.headlines import filter_point_in_time, parse_rss_items

    items = parse_rss_items(_RSS)
    assert [i["title"] for i in items] == [
        "AAPL beats Q3 estimates",
        "Future leak headline",
    ], "undated item must already be dropped at parse time"
    now = datetime(2026, 7, 22, 14, 0, 0, tzinfo=timezone.utc)
    kept = filter_point_in_time(items, now)
    assert [i["title"] for i in kept] == ["AAPL beats Q3 estimates"]
    assert kept[0]["source"] == "Reuters"
    assert kept[0]["published_utc"].startswith("2026-07-22T12:00:00")


def _pin_producer_config(monkeypatch, **keys) -> None:
    """CI-Prevention R2 (ac375ee8 pattern): patch get_config on ALL import paths
    — `config`, `ai_trading_bot.config` and the consumer module (raising=False:
    headlines.py does `import config`, no module-level get_config attribute)."""
    import config

    cfg_obj = SimpleNamespace(**keys)
    monkeypatch.setattr(config, "get_config", lambda: cfg_obj)
    try:
        import ai_trading_bot.config as bot_cfg

        monkeypatch.setattr(bot_cfg, "get_config", lambda: cfg_obj)
    except Exception:
        pass
    try:
        from core.nlp import headlines as hl_mod

        monkeypatch.setattr(hl_mod, "get_config", lambda: cfg_obj, raising=False)
    except Exception:
        pass


@pytest.mark.anyio
async def test_producer_flag_off_returns_none_without_network(monkeypatch):
    """Flag-first: OFF (default) → None WITHOUT any httpx client construction, so the
    dormant path stays byte-neutral (no key added to the state, no I/O)."""
    from core.nlp import headlines as hl

    hl._reset_cache_for_tests()
    _pin_producer_config(monkeypatch, NEWS_SENTIMENT_NLP_ENABLED=False)
    fetch = AsyncMock()
    monkeypatch.setattr(hl, "_fetch_rss", fetch)
    res = await hl.produce_news_headlines(
        "AAPL", datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    )
    assert res is None
    fetch.assert_not_awaited()


@pytest.mark.anyio
async def test_producer_flag_on_filters_caps_and_uses_google_rss(monkeypatch):
    """Flag ON → Google-News-RSS is fetched, PIT-filtered and capped; a fetch error
    degrades to [] (transparent abstain downstream), never to a crash."""
    from core.nlp import headlines as hl

    hl._reset_cache_for_tests()
    _pin_producer_config(
        monkeypatch, NEWS_SENTIMENT_NLP_ENABLED=True, NEWS_SENTIMENT_MAX_HEADLINES=1
    )
    seen_urls = []

    async def fake_fetch(url):
        seen_urls.append(url)
        return _RSS

    monkeypatch.setattr(hl, "_fetch_rss", fake_fetch)
    res = await hl.produce_news_headlines(
        "AAPL", datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    )
    assert res == [
        {
            "title": "AAPL beats Q3 estimates",
            "published_utc": "2026-07-22T12:00:00+00:00",
            "source": "Reuters",
        }
    ]
    assert len(seen_urls) == 1 and "news.google.com/rss" in seen_urls[0]

    # error path → [] (not None: flag is ON, the gap must be visible downstream)
    hl._reset_cache_for_tests()
    monkeypatch.setattr(
        hl, "_fetch_rss", AsyncMock(side_effect=OSError("network down"))
    )
    res2 = await hl.produce_news_headlines(
        "MSFT", datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    )
    assert res2 == []


@pytest.mark.anyio
async def test_producer_ttl_cache_serves_second_call(monkeypatch):
    """RSS rate-limit protection (plan §12.2): the second call for the same symbol
    within the TTL must NOT re-fetch — mirrors the agents.py:_CACHE_TTL posture."""
    from core.nlp import headlines as hl

    hl._reset_cache_for_tests()
    _pin_producer_config(
        monkeypatch, NEWS_SENTIMENT_NLP_ENABLED=True, NEWS_SENTIMENT_MAX_HEADLINES=8
    )
    fetch = AsyncMock(return_value=_RSS)
    monkeypatch.setattr(hl, "_fetch_rss", fetch)
    now = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc)
    await hl.produce_news_headlines("AAPL", now)
    await hl.produce_news_headlines("AAPL", now)
    assert fetch.await_count == 1


def test_r12_no_paid_api_static_guard():
    """R12 (feedback_no_paid_apis): the new NLP/news modules must never reference
    Polygon (paid) — the free Google-News-RSS source is the ONLY news channel."""
    offenders = []
    for py in _NLP_DIR.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore").lower()
        if "polygon" in text:
            offenders.append(py.name)
    assert not offenders, f"paid Polygon API referenced in core/nlp: {offenders}"


# ---------------------------------------------------------------------------
# Config parity (BORA) — 3 keys, identical defaults in both editions
# ---------------------------------------------------------------------------


def _load_oss_config():
    spec = importlib.util.spec_from_file_location("config_oss_ns_test", _OSS_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_parity_news_sentiment_keys(monkeypatch):
    monkeypatch.delenv("NEWS_SENTIMENT_NLP_ENABLED", raising=False)
    monkeypatch.delenv("NEWS_SENTIMENT_MAX_HEADLINES", raising=False)
    monkeypatch.delenv("NEWS_SENTIMENT_MODEL", raising=False)
    import config as enterprise

    ent = enterprise.get_config()
    oss = _load_oss_config().get_config()
    for cfg, edition in ((ent, "config.py"), (oss, "config.oss.py")):
        # ADR-NS-07 (2026-08-04): default flipped ON by owner activation — both
        # editions in lock-step (BORA parity). Backend selector stays "finbert"
        # (degrades to the vendored VADER lexicon when transformers/weights absent).
        assert cfg.NEWS_SENTIMENT_NLP_ENABLED is True, edition
        assert cfg.NEWS_SENTIMENT_MAX_HEADLINES == 8, edition
        assert cfg.NEWS_SENTIMENT_MODEL == "finbert", edition
