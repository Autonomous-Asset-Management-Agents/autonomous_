# tests/unit/test_specialist_confidence_quality.py
"""RQ-1 B4 (#1524): confidence reflects DATA QUALITY, and a degraded report clamps to hold.

Supersedes the score-extremity confidence (min(0.9, 0.3 + abs(score-50)/100)) -- a thin-data
report was previously HIGH-confidence whenever the LLM score was extreme. With the guard on
(B5), integrity.data_quality drives confidence, and a degraded report (low-quality /
insufficient sources) cannot present a confident directional call (MiFID: no confident BUY on
bad data). Generalises A4's empty-cycle abstention. (Epic #1516, Phase B.)
"""

from __future__ import annotations

from core.data_integrity import DataIntegrityResult
from core.stock_specialist import StockSpecialistAgent


def _gathered_with_signal():
    return {
        "insider_trades": [{"filed": "2026-06-20", "filer": "CEO", "form": "Form 4"}],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": ["AAPL beats earnings"],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }


class TestConfidenceFromDataQuality:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_confidence_tracks_data_quality(self):
        """More data -> more confidence, independent of the LLM score's extremity."""
        syn = {"text": "SCORE: 60\nOUTLOOK: bullish\n"}
        hi = self.agent._build_report(
            _gathered_with_signal(),
            syn,
            integrity=DataIntegrityResult(0.9, False, False),
        )
        lo = self.agent._build_report(
            _gathered_with_signal(),
            syn,
            integrity=DataIntegrityResult(0.6, False, False),
        )
        assert hi.confidence > lo.confidence
        assert hi.confidence == round(0.25 + 0.55 * 0.9, 2)

    def test_degraded_clamps_to_hold(self):
        """A degraded report cannot show a confident BUY -> hold + low confidence."""
        syn = {"text": "SCORE: 80\nOUTLOOK: bullish\n"}
        r = self.agent._build_report(
            _gathered_with_signal(),
            syn,
            integrity=DataIntegrityResult(0.4, True, False),
        )
        assert r.recommendation == "hold"
        assert r.confidence <= 0.3

    def test_guard_off_keeps_parsed_confidence(self):
        """integrity None (guard off) -> the parsed score-based confidence is untouched."""
        syn = {"text": "SCORE: 80\nOUTLOOK: bullish\n"}
        r = self.agent._build_report(_gathered_with_signal(), syn, integrity=None)
        assert r.confidence != round(0.25 + 0.55 * 1.0, 2)
