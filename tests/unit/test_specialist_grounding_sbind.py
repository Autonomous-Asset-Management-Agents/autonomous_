# tests/unit/test_specialist_grounding_sbind.py
# Rich-synthesis card (Direction A) — possessive-"s" binding-leak regression gate.
#
# Gherkin (plan):
#   Szenario: Eine ungegroundete Floskel darf NICHT allein ueber ein nacktes "s"
#             binden (Possessiv-Leck)
#     Angenommen ein Satz teilt mit JEDER Headline nur das 1-Zeichen-Token "s"
#       (aus "company's" / "Apple's" — fast jede echte Headline traegt "Here's",
#        "World's", "Berkshire's", "Page's" und emittiert ebenfalls "s")
#     Wenn verify_grounding laeuft
#     Dann faellt die Floskel (kein FALSE PASS unter echt-aussehender Quelle)
#   Szenario: Der Fix darf NICHT ueber-filtern (Gegenkraft)
#     Angenommen der Kern bindet ueber ein Mehr-Zeichen-Inhalts-Token
#       ODER ueber das generische-aber-on-topic 2-Token-Netz ("revenue guidance")
#     Dann ueberlebt er wortwoertlich
#
# Belegt am LIVE-Pfad (Refute-Agent): _tokenize("company's") -> ["company","s"].
# Das nackte "s" steht WEDER in _STOPWORDS NOCH in _BIND_GENERIC -> grounding.py
# behandelte es als NON-generic content token, sodass ein einzelnes "s" die
# Bedingung (ng >= 1) erfuellte und eine thematisch fremde Editorial-Floskel an
# eine beliebige Headline band. Die fuenf Faelle unten sind die belegten Lecks;
# jeder Overlap ist NUR {'s'}. RED wird bewiesen, indem der Min-Laenge-/Clitic-
# Filter in grounding.py zurueckgenommen wird -> mindestens eine Floskel bindet
# wieder ueber "s" und die Drop-Assertion geht rot.

from __future__ import annotations

import allure

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


# --- Real, self-consistent per-symbol headline sets (every set carries a real
# --- possessive -> a stray "s" token: "Here's", "world's", "Berkshire's",
# --- "Page's"). This is exactly what let a foreign floskel bind. ---------------

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


def _cited(res, substr):
    """True iff any citation's claim contains ``substr`` (case-insensitive)."""
    return any(substr in c["claim"].lower() for c in res.citations)


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding — Possessive-s Binding Leak")
class TestPossessiveSFloskelnFall:
    """Rebalanced (D) gate: a floskel whose ONLY corpus overlap is the bare "s"
    survives as safe reasoning but must NOT earn a citation via that "s"
    (the possessive-s binding leak stays closed at the CITATION level)."""

    def test_googl_bear_business_model_floskel_falls(self):
        # Overlap with the corpus is ONLY {'s'} (from "Here's" in H3).
        res = _ground(
            "GOOGL",
            bull="",
            bear="GOOGL faces underlying issues with the company's business model.",
            thesis="",
            headlines=GOOGL_HEADLINES,
            company_tokens={"a", "alphabet", "class", "common", "inc", "stock"},
        )
        assert "business model" in res.bear.lower()
        assert not _cited(res, "business model")
        assert not _cited(res, "underlying issues")

    def test_aapl_bull_cash_reserves_floskel_falls(self):
        # No headline mentions cash / reserves / investments -> overlap ONLY {'s'}.
        res = _ground(
            "AAPL",
            bull=(
                "The company's large cash reserves position it well for "
                "future investments."
            ),
            bear="",
            thesis="",
            headlines=AAPL_HEADLINES,
            company_tokens={"apple", "inc"},
        )
        assert "cash reserves" in res.bull.lower()
        assert not _cited(res, "cash reserves")
        assert not _cited(res, "future investments")

    def test_aapl_bull_apple_dominance_floskel_falls(self):
        # "Apple" is a company token (excluded from overlap) -> overlap ONLY {'s'}.
        res = _ground(
            "AAPL",
            bull="Apple's dominance in the market continues to grow.",
            bear="",
            thesis="",
            headlines=AAPL_HEADLINES,
            company_tokens={"apple", "inc"},
        )
        assert "dominance" in res.bull.lower()
        assert not _cited(res, "dominance")

    def test_brk_bull_investments_paying_off_floskel_falls(self):
        # "investments"/"company" are generic; "paying" in no headline -> ONLY {'s'}.
        res = _ground(
            "BRK.B",
            bull="the company's investments are paying off.",
            bear="",
            thesis="",
            headlines=BRK_HEADLINES,
            company_tokens=set(),
        )
        assert "paying off" in res.bull.lower()
        assert not _cited(res, "paying off")
        assert not _cited(res, "investments")

    def test_brkb_bull_future_prospects_trailing_clause_falls_core_stays(self):
        # The grounded core (buybacks accelerating, H5) MUST stay; the trailing
        # "confidence in the company's future prospects" bound ONLY via "s" -> falls.
        res = _ground(
            "BRK.B",
            bull=(
                "Berkshire Hathaway's stock buybacks are accelerating, "
                "indicating confidence in the company's future prospects [H5]."
            ),
            bear="",
            thesis="",
            headlines=BRK_HEADLINES,
            company_tokens=set(),
        )
        # The intact sentence (core + safe trailing reasoning) survives whole,
        # cited via its bound core — the "s"-leak stays closed because the "s"
        # token alone still cannot BIND (no citation would exist without the
        # multi-char "buybacks" overlap).
        assert "future prospects" in res.bull.lower()
        assert "buybacks are accelerating" in res.bull.lower()
        assert any("buybacks" in c["headline_text"].lower() for c in res.citations)


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding — Anti-Over-Filter (cores + 2-token net)")
class TestSFilterDoesNotOverFilter:
    """The min-length/clitic filter must NOT drop real cores or the 2-token net."""

    def test_multichar_content_cores_still_bind(self):
        # Every protected core binds via a MULTI-char content token, never "s".
        res = _ground(
            "NVDA",
            bull="NVDA's AI stack is changing [H1], making it a leader in the field.",
            bear=(
                "Michael Burry is skeptical of NVDA [H2], tempering the "
                "bullish narrative."
            ),
            thesis="",
            headlines=[
                "Nvidia: The AI Stack Is Changing (NASDAQ:NVDA) - Seeking Alpha",
                "Michael Burry Is Skeptical Of NVDA, ORCL And NFLX - Yahoo",
                "Berkshire Hathaway Increased Its Stock Buybacks - TipRanks",
            ],
            company_tokens={"nvidia", "corp"},
        )
        # cores survive (stack / burry are >=2-char content tokens) ...
        assert "stack is changing" in res.bull.lower()
        assert "skeptical of nvda" in res.bear.lower()
        # ... with citations (the multi-char content-token binding works) ...
        assert any(
            "ai stack is changing" in c["headline_text"].lower() for c in res.citations
        )
        assert any(
            "skeptical of nvda" in c["headline_text"].lower() for c in res.citations
        )
        # ... while the generic trailing floskel survives inside its sentence.
        assert "leader in the field" in res.bull.lower()

    def test_two_token_generic_safety_net_still_binds(self):
        # All-generic clause, ZERO non-generic overlap, but shares TWO generic
        # tokens ("revenue" + "guidance") with a headline -> must STILL bind.
        res = _ground(
            "ACME",
            bull="Raised revenue guidance keeps the outlook constructive [H1].",
            bear="",
            thesis="",
            headlines=[
                "ACME lifts full-year revenue guidance after strong quarter - Reuters",
                "Some Unrelated Market Headline About Rates - CNBC",
            ],
            company_tokens={"acme", "inc"},
        )
        # "revenue" and "guidance" are BOTH in _BIND_GENERIC, so ng-overlap is 0;
        # the 2-token total-overlap net is the ONLY thing keeping this alive.
        assert "revenue guidance" in res.bull.lower()
