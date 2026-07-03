# tests/unit/test_specialist_empty_cycle_abstention.py
"""RQ-1 A4 (#1520): abstain on an empty research cycle.

When a cycle gathered NO substantive data (no insider/8-K/13D filings, no congressional
trades, no headlines, no alt-signals), the LLM score/recommendation is an ungrounded guess.
The report must abstain -- recommendation=hold + capped confidence -- instead of emitting a
confident BUY/SELL next to "overview unavailable this cycle". Reports WITH real data are
untouched. (Epic #1516, Phase A.)
"""

from __future__ import annotations

from core.stock_specialist import StockSpecialistAgent


def _empty_gathered() -> dict:
    return {
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


def _gathered_with_signal() -> dict:
    g = _empty_gathered()
    g["insider_trades"] = [{"filed": "2026-06-20", "filer": "CEO", "form": "Form 4"}]
    return g


class TestEmptyCycleAbstention:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_empty_cycle_abstains(self):
        """No signals at all -> hold + capped confidence, not a confident BUY."""
        synthesis = {"text": "OUTLOOK: bullish\nSCORE: 80\n"}
        report = self.agent._build_report(_empty_gathered(), synthesis)
        assert report.recommendation == "hold"
        assert report.confidence <= 0.3

    def test_report_with_signal_keeps_recommendation(self):
        """A report with a real signal (insider filing) is NOT forced to hold."""
        synthesis = {"text": "OUTLOOK: bullish\nSCORE: 70\n"}
        report = self.agent._build_report(_gathered_with_signal(), synthesis)
        assert report.recommendation == "buy"
