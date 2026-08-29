# tests/unit/test_specialist_count_bonus_flag.py
"""RQ-1 B3 (#1523): the count-driven sentiment is removed permanently.

Supersedes A3 (#1519): the +4/+5 filing-COUNT bonuses are gone entirely -- raw filing counts
no longer inflate the score (a Form 4 may be a sell, a 13D may be neutral; real direction
needs the filing documents, tracked as a follow-up). The >=82 auto-escalation is restored
(un-gated) now that the score is no longer count-inflated. SPECIALIST_COUNT_BONUS_ENABLED is
removed; a stale config that still sets it is ignored. (Epic #1516, Phase B.)
"""

from __future__ import annotations

from unittest.mock import patch

from core.stock_specialist import StockSpecialistAgent


class _LegacyFlagOn:
    """A stale config that still sets the removed flag -- B3 must IGNORE it (no bonus)."""

    SPECIALIST_COUNT_BONUS_ENABLED = True


def _gathered(insider: int = 0, activists: int = 0) -> dict:
    return {
        "insider_trades": [{"filed": "2026-06-20", "filer": "CEO", "form": "Form 4"}]
        * insider,
        "material_events": [],
        "activist_stakes": [{"filed": "2026-06-20", "filer": "Icahn", "form": "13D"}]
        * activists,
        "political_trades": [],
        "recent_headlines": [],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }


class TestNoCountInflation:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_filing_counts_never_inflate_score(self):
        """3 insider + an activist do NOT add +4/+5 -- the score stays at the LLM base."""
        synthesis = {"text": "SCORE: 50\nOUTLOOK: neutral\n"}
        report = self.agent._build_report(_gathered(insider=3, activists=1), synthesis)
        assert report.sentiment_score == 50.0

    def test_legacy_count_bonus_flag_is_ignored(self):
        """Even a stale SPECIALIST_COUNT_BONUS_ENABLED=True yields NO bonus (B3 removed it)."""
        synthesis = {"text": "SCORE: 50\nOUTLOOK: neutral\n"}
        with patch("core.stock_specialist.get_config", return_value=_LegacyFlagOn()):
            report = self.agent._build_report(
                _gathered(insider=3, activists=1), synthesis
            )
        assert report.sentiment_score == 50.0  # NOT 59

    def test_no_cluster_insider_reason(self):
        """The count-as-bullish 'Cluster insider activity' reason is gone."""
        synthesis = {"text": "SCORE: 50\nOUTLOOK: neutral\n"}
        report = self.agent._build_report(_gathered(insider=3, activists=1), synthesis)
        assert not any("Cluster insider" in r for r in report.reasons)

    def test_high_genuine_score_escalates(self):
        """A genuine >=82 LLM score auto-escalates again (no longer gated behind the bonus)."""
        synthesis = {"text": "SCORE: 85\nOUTLOOK: bullish\n"}
        report = self.agent._build_report(_gathered(insider=1), synthesis)
        assert report.escalate is True
