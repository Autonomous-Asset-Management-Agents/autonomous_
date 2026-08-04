# tests/unit/test_specialist_grounding_clause.py
# Rich-synthesis card (Direction A) — B1/B1b clause-level binding regression gate.
#
# Gherkin (plan):
#   Szenario: Generische Floskel im Anhang eines echten Satzes faellt weg (B1)
#     Angenommen ein Satz haengt mit seinem Kern an einer echten Headline
#     Und traegt einen nachlaufenden Teilsatz, den die Headline NICHT hergibt
#     Wenn verify_grounding laeuft
#     Dann bleibt der belegte Kern und der ungebundene Teilsatz verschwindet
#   Szenario: Der belegte Kern darf NICHT ueber-gefiltert werden (Gegenkraft)
#     Angenommen der Kern teilt echte Inhalts-Tokens mit seiner Headline
#     Dann ueberlebt er wortwoertlich (kein Platzhalter-Kollaps)
#   Szenario: Kosmetik — kein " ." (Leerzeichen vor Satzzeichen) (B1b)
#     Angenommen der [H#]-Tag wird herausgeschnitten
#     Dann traegt der angezeigte Satz kein " ." / doppeltes Leerzeichen
#
# The five ungrounded trailing clauses below are REAL, captured from a 5-symbol
# live validation of the card (card_validation_results.json). Each survived under
# the too-lax single-token binding and MUST now fall; the paired grounded core
# MUST remain. RED is proven by reverting the clause-split / non-generic binding
# in grounding.py — the trailing clause then reappears in the displayed prose and
# the drop-assertions go red.

from __future__ import annotations

import allure
import pytest

from core.specialist.grounding import verify_grounding


def _gathered(headlines):
    return {
        "recent_headlines": list(headlines),
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "reddit_sentiment": "neutral",
    }


def _ground(symbol, bull, bear, thesis, headlines, company_tokens):
    return verify_grounding(
        bull,
        bear,
        thesis,
        _gathered(headlines),
        symbol,
        company_tokens=set(company_tokens),
    )


# --- Real, self-consistent per-symbol headline sets (the [H#] corpus). ---------

NVDA_HEADLINES = [
    "Bank of America Sees Nvidia's Next $20 Billion Business. "
    "Here's Where NVDA Stock Could Go - 24/7 Wall St.",
    "SNDK, MU, AMD, NVDA - Is It Time to Buy the Dip in Chip Stocks? - TipRanks",
    "Apple, Nvidia vie for title of world's most valuable company - CNBC",
    "Michael Burry Is Skeptical Of NVDA, ORCL And NFLX - "
    "But Bullish On This Sports Betting Stock - Yahoo Finance",
    "Nvidia: The AI Stack Is Changing (NASDAQ:NVDA) - Seeking Alpha",
    "AAPL capitalization squeaks past NVDA, Apple becomes world's most "
    "valuable company - AppleInsider",
]

GOOGL_HEADLINES = [
    "Warren Buffett Says Alphabet (GOOGL) Can Beat 95% of Wall Street "
    "Stock Picks - BeInCrypto",
    "Stocks making the biggest moves premarket: Netflix, SpaceX, Alphabet "
    "and more - CNBC",
    "Ahead of Alphabet Earnings, Here's What Barchart Data Says Comes Next "
    "for GOOGL Stock - Barchart.com",
    "Why Alphabet (GOOGL) Stock Is Falling Today - Yahoo Finance",
    "Alphabet Drops 4%, But Analyst Believes There Is Massive Upside - "
    "24/7 Wall St.",
    "Why Alphabet (GOOGL) Stock Is Falling Today - TradingView",
]

AAPL_HEADLINES = [
    "Apple (AAPL) Raises the Cost of Its Music Streaming Service - TipRanks",
    "Apple, Nvidia vie for title of world's most valuable company - CNBC",
    "Apple (AAPL) Rises As Market Takes a Dip: Key Facts - Yahoo Finance",
    "SA analyst upgrades/downgrades: SNDK, AAPL, PYPL, NOW - Seeking Alpha",
    "AAPL capitalization squeaks past NVDA, Apple becomes world's most "
    "valuable company - AppleInsider",
    "What Apple Stock Was Telling You Before Its 60% Climb - Trefis",
]

KO_HEADLINES = [
    "Is The Coca-Cola Company (KO) A Good Stock To Buy Now? - Yahoo Finance",
    "Serrano aiming for KO record Aug. 21 in title defense on TikTok - ESPN",
    "Danny Sabatello silences critics with brutal KO at Rizin Landmark 15 - " "Sherdog",
    "4 stocks to watch on Friday: RF, COP, GSK, and KO (SP500:) - Seeking Alpha",
    "SeokHyeon Ko | Quietly Making Waves At Welterweight - UFC.com",
    "Coca-Cola Stock (KO) Falls as Company Hit by Ransomware Attack - TipRanks",
]

BRK_HEADLINES = [
    "Berkshire's Equity Portfolio Is Rallying, but the Apple Sales Still "
    "Sting - Barron's",
    "Larry Page's Fortune Hits $300 Billion As Buffett Takes Credit For "
    "Berkshire Investment - Forbes",
    "Warren Buffett Says Alphabet (GOOGL) Can Beat 95% of Wall Street "
    "Stock Picks - Yahoo Finance",
    "Warren Buffett Drops a Bombshell: He Was Responsible for Berkshire's "
    "Push Into AI - 24/7 Wall St.",
    "Berkshire Hathaway (BRK.B) Increased Its Stock Buybacks in Second "
    "Quarter - TipRanks",
    "Berkshire Hathaway Appears to Have Accelerated Stock Buybacks in "
    "Second Quarter - Barron's",
]


def _cited(res, substr: str) -> bool:
    """True iff any citation's claim contains ``substr`` (case-insensitive)."""
    return any(substr in c["claim"].lower() for c in res.citations)


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding — Clause Binding")
class TestUnboundSafeClausesSurviveWithoutCitation:
    """Rebalanced (D) gate: a safe trailing clause (no foreign entities/numbers)
    SURVIVES but earns NO citation; the headline-bound core keeps its citation.
    Only fabrication (entity/number gates, fragments) still drops."""

    def test_nvda_bull_leader_floskel_survives_uncited_core_cited(self):
        res = _ground(
            "NVDA",
            bull="NVDA's AI stack is changing [H5], making it a leader in the field.",
            bear=(
                "Michael Burry is skeptical of NVDA [H4], tempering the "
                "bullish narrative."
            ),
            thesis="",
            headlines=NVDA_HEADLINES,
            company_tokens={"nvidia", "corp"},
        )
        # The intact sentence survives WHOLE (core + safe trailing reasoning),
        # cited once via its bound core — the UBER-card format Georg approved.
        assert "leader in the field" in res.bull.lower()
        assert "stack is changing" in res.bull.lower()
        assert any(
            "ai stack is changing" in c["headline_text"].lower() for c in res.citations
        )
        # And the grounded bear core (H4) keeps its citation too.
        assert "skeptical of nvda" in res.bear.lower()
        assert _cited(res, "skeptical of nvda")

    def test_googl_bull_brand_floskel_survives_upside_and_drop_stay(self):
        res = _ground(
            "GOOGL",
            bull=(
                "GOOGL stock has massive upside potential due to its strong "
                "brand and diversified revenue streams [H5]. The analyst "
                "believes this despite the recent 4% drop [H5]."
            ),
            bear="",
            thesis="",
            headlines=GOOGL_HEADLINES,
            company_tokens={"a", "alphabet", "class", "common", "inc", "stock"},
        )
        # Both grounded cores survive with citations (massive upside + 4% drop).
        assert "massive upside" in res.bull.lower()
        assert "4% drop" in res.bull.lower()
        # The unbound-but-safe reasoning survives inside its intact sentence.
        assert "diversified revenue streams" in res.bull.lower()
        assert res.dropped == []

    def test_aapl_bull_and_thesis_floskeln_survive_uncited(self):
        res = _ground(
            "AAPL",
            bull=(
                "AAPL's rise to the top spot as the world's most valuable "
                "company suggests strong financial performance and growth "
                "potential [H5]."
            ),
            bear="",
            thesis=(
                "Investors may want to consider buying AAPL due to its "
                "increasing value and market dominance [H5]."
            ),
            headlines=AAPL_HEADLINES,
            company_tokens={"apple", "inc"},
        )
        # The bull core (world's most valuable company) stays grounded + cited.
        assert "most valuable company" in res.bull.lower()
        assert _cited(res, "most valuable company")
        # Safe analyst reasoning survives; anything uncitable carries no citation.
        assert "market dominance" in res.thesis.lower()
        assert not _cited(res, "market dominance")
        assert res.dropped == []

    def test_ko_thesis_generic_stock_binding_uncited_bear_core_cited(self):
        res = _ground(
            "KO",
            bull="",
            bear=(
                "The recent ransomware attack on KO may have impacted the "
                "company's operations and stock price, potentially leading to "
                "short-term volatility."
            ),
            thesis=(
                "Investors should consider buying KO as a stable "
                "dividend-paying stock with a strong brand presence [H1]."
            ),
            headlines=KO_HEADLINES,
            company_tokens={"coca", "cola", "company"},
        )
        # The thesis binds ONLY on the generic word "stock" -> it survives as
        # safe reasoning but must NOT earn a fabricated citation.
        assert "brand presence" in res.thesis.lower()
        assert not _cited(res, "brand presence")
        # The genuinely grounded bear core (ransomware attack, H6) stays + cited.
        assert "ransomware attack on ko" in res.bear.lower()
        assert _cited(res, "ransomware attack on ko")

    def test_brkb_bear_stake_uncited_buybacks_and_sting_stay(self):
        res = _ground(
            "BRK.B",
            bull=(
                "Berkshire Hathaway's stock buybacks are accelerating, "
                "indicating confidence in the company's future prospects [H5]."
            ),
            bear=(
                "Declining Apple sales may continue to impact Berkshire "
                "Hathaway's profits, as the company has a significant stake "
                "in the technology giant [H1]."
            ),
            thesis="",
            headlines=BRK_HEADLINES,
            # #2249 FIX 2: the subject-corpus filter keys off symbol|company
            # tokens, so the subject company's own name tokens are supplied here
            # (as the runtime loads them from company_cache/BRK.B.json) — the
            # "Berkshire"-named headlines these cores bind to carry the ticker in
            # only one headline, but every one carries "Berkshire".
            company_tokens={"berkshire", "hathaway"},
        )
        # Safe trailing reasoning survives inside its intact, core-bound sentence.
        assert "significant stake" in res.bear.lower()
        # Both protected cores survive; the bear sentence cites the Barron's
        # "Apple Sales Still Sting" headline via its bound core.
        assert "buybacks are accelerating" in res.bull.lower()
        assert "apple sales" in res.bear.lower()
        assert any(
            "apple sales still sting" in c["headline_text"].lower()
            for c in res.citations
        )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding — Cosmetics")
class TestCosmetics:
    def test_no_space_before_terminal_punctuation(self):
        """B1b: stripping the [H#] tag must not leave a ' .' / double space."""
        res = _ground(
            "NVDA",
            bull="Michael Burry is skeptical of NVDA [H4].",
            bear="",
            thesis="",
            headlines=NVDA_HEADLINES,
            company_tokens={"nvidia", "corp"},
        )
        assert " ." not in res.bull
        assert " ," not in res.bull
        assert "  " not in res.bull
        assert res.bull.strip().endswith(".")
