# tests/unit/test_specialist_guard_default_on.py
"""RQ-1 B5 (#1525): the data-integrity guard is enabled by default.

B1/B2/B3 made the specialist inputs entity-correct, recent, and count-unbiased, so the
guard's presence-based data_quality / degraded signals are now trustworthy -> the guard is
activated by default. It stays DECISION-NEUTRAL (display-only data_quality / degraded +
skip_llm on near-empty data); clamping the recommendation on `degraded` is B4. (Epic #1516.)
"""

from __future__ import annotations

from config import get_config
from core.data_integrity import assess
from core.stock_specialist import StockSpecialistAgent


def test_data_integrity_guard_enabled_by_default():
    assert getattr(get_config(), "DATA_INTEGRITY_GUARD_ENABLED", False) is True


def test_guard_surfaces_degraded_for_sparse_data():
    """All primary sources empty -> the guard marks the report degraded + low data_quality."""
    gathered = {
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
    integrity = assess(gathered)
    agent = StockSpecialistAgent("AAPL", "dummy-key")
    report = agent._build_report(gathered, {"text": "SCORE: 50\n"}, integrity=integrity)
    assert report.data_quality < 1.0
    assert report.degraded is True
