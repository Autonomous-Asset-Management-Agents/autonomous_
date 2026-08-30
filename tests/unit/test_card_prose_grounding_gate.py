# tests/unit/test_card_prose_grounding_gate.py
# Card-prose fabrication gate — FIX 1 (grounding as a precondition of the
# displayed bull/bear/thesis prose) + FIX 2 (subject-corpus headline filter).
#
# The live #2249 showstopper (2026-07-22): the standalone report registry renders
# rich cards WITHOUT Alpaca, but the displayed prose is UNGROUNDED and partly
# FABRICATED — DIA showed "Tate Museums" (a ticker-hallucinated art museum),
# grounding_ok=None, cites=0. Two independent leaks:
#
#   FIX 1 — _build_report set bull_case_v2/bear_case_v2/investment_thesis_v2 from
#   the raw parser prose and only THEN (behind SPECIALIST_GROUNDING_ENABLED)
#   grounded them. With PROMPT_V2 ON but GROUNDING OFF (the live miscfg) the raw
#   fabricated prose flowed straight to the card. The fix makes grounding the ONLY
#   writer of the displayed prose: without it those fields stay "" (DTO default).
#
#   FIX 2 — even with GROUNDING ON, a bare-ticker Google-News fetch pulls an
#   off-subject headline (the Tate headline for ticker "DIA") into the corpus, so
#   the entity gate accepts "Tate Museums" and the sentence binds and survives.
#   The fix filters recent_headlines to subject-bearing ones (symbol|company
#   tokens) BEFORE the corpus + binding targets are built.
#
# TDD: RED is proven by REALLY reverting the edit (never inspect.getsource):
#   * T1 RED — restore `bull_case_v2 = parsed.bull_case` -> raw prose reaches the
#     displayed field -> report.bull_case != "".
#   * T3 RED — restore the unfiltered headlines -> the Tate headline re-enters the
#     corpus -> "Tate Museums" binds and survives -> it lands in res.bull.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import allure
import pytest

from core.specialist.grounding import verify_grounding
from core.stock_specialist import StockSpecialistAgent


# ---------------------------------------------------------------------------
# FIX 1 — grounding is the ONLY writer of the displayed prose (via _build_report)
# ---------------------------------------------------------------------------
def _gathered(headlines):
    return {
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": list(headlines),
        "wiki_spike": False,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }


# A V2 synthesis text whose BULL prose is FABRICATED (the DIA/Tate class): it
# parses to a non-empty bull_case that no headline of the symbol supports.
_FABRICATED_SYNTH = {
    "text": (
        "SUMMARY: neutral tape\n"
        "SCORE: 55\n"
        "BULL: The appointment of Jessica Morgan as director of the Tate "
        "Museums benefits DIA operations [H1].\n"
        "BEAR: Some concern about the fund outlook [H1].\n"
        "THESIS: A neutral thesis on the fund [H1].\n"
    )
}


@allure.feature("VC-1 Research & Analysis")
@allure.story("Card Prose Grounding Gate — FIX 1")
class TestGroundingIsPreconditionOfDisplayedProse:
    def test_prompt_v2_on_grounding_off_shows_empty_not_fabrication(self):
        """PROMPT_V2 ON / GROUNDING OFF (the live miscfg): the raw fabricated
        prose must NOT reach the card — the displayed fields stay "" and
        grounding_ok stays None. RED: restore `bull_case_v2 = parsed.bull_case`."""
        agent = StockSpecialistAgent("DIA", gemini_api_key="x")
        cfg = SimpleNamespace(
            SPECIALIST_PROMPT_V2=True,
            SPECIALIST_GROUNDING_ENABLED=False,
            SPECIALIST_CARDS_ENABLED=False,
            ML_SENTIMENT_BLEND_ENABLED=False,
        )
        with patch("core.stock_specialist.get_config", return_value=cfg):
            report = agent._build_report(
                _gathered(["Some unrelated market headline"]), _FABRICATED_SYNTH
            )
        assert report.bull_case == ""
        assert report.bear_case == ""
        assert report.investment_thesis == ""
        assert report.grounding_ok is None

    def test_prompt_v2_off_is_byte_identical_empty(self):
        """Dormant default (PROMPT_V2 OFF): the V2 prose is never produced -> the
        displayed fields are "" and grounding_ok None, byte-identical to today."""
        agent = StockSpecialistAgent("DIA", gemini_api_key="x")
        cfg = SimpleNamespace(
            SPECIALIST_PROMPT_V2=False,
            SPECIALIST_GROUNDING_ENABLED=False,
            SPECIALIST_CARDS_ENABLED=False,
            ML_SENTIMENT_BLEND_ENABLED=False,
        )
        with patch("core.stock_specialist.get_config", return_value=cfg):
            report = agent._build_report(
                _gathered(["Some unrelated market headline"]), _FABRICATED_SYNTH
            )
        assert report.bull_case == ""
        assert report.bear_case == ""
        assert report.investment_thesis == ""
        assert report.grounding_ok is None

    def test_grounding_on_fills_displayed_prose_and_sets_ok(self):
        """PROMPT_V2 ON / GROUNDING ON: a subject-bearing, headline-bound bull
        sentence survives into the DISPLAYED field and grounding_ok is a real
        bool (never None)."""
        agent = StockSpecialistAgent("NVDA", gemini_api_key="x")
        synth = {
            "text": (
                "SUMMARY: strong tape\n"
                "SCORE: 66\n"
                "BULL: Nvidia raised its full-year revenue guidance after "
                "record data-center sales, signalling durable demand [H1].\n"
            )
        }
        headlines = [
            "Nvidia raised its full-year revenue guidance after record "
            "data-center sales"
        ]
        cfg = SimpleNamespace(
            SPECIALIST_PROMPT_V2=True,
            SPECIALIST_GROUNDING_ENABLED=True,
            SPECIALIST_CARDS_ENABLED=False,
            ML_SENTIMENT_BLEND_ENABLED=False,
        )
        with patch("core.stock_specialist.get_config", return_value=cfg):
            with patch.object(
                agent, "_load_company_tokens", return_value={"nvidia", "corp"}
            ):
                report = agent._build_report(_gathered(headlines), synth)
        assert report.bull_case != ""
        assert "guidance" in report.bull_case.lower()
        assert report.grounding_ok is True


# ---------------------------------------------------------------------------
# FIX 2 — subject-corpus headline filter (verify_grounding directly)
# ---------------------------------------------------------------------------
_DIA_COMPANY_TOKENS = {
    "spdr",
    "dow",
    "jones",
    "industrial",
    "average",
    "etf",
    "trust",
}


def _grounding_gathered(headlines):
    return {
        "recent_headlines": list(headlines),
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "reddit_sentiment": "neutral",
    }


@allure.feature("VC-1 Research & Analysis")
@allure.story("Card Prose Grounding Gate — FIX 2 subject filter")
class TestSubjectCorpusFilter:
    def test_off_subject_tate_headline_is_dropped_for_dia(self):
        """The bare-ticker "DIA" fetch pulls a Tate-museum headline into the news
        pool. The subject filter must keep it OUT of the corpus so the fabricated
        "Tate Museums" sentence is dropped, while the genuinely on-subject SPDR/Dow
        sentence survives. RED: restore the unfiltered headlines -> Tate binds."""
        bull = (
            "The Tate Museums named a new director for its art collection [H1]. "
            "The SPDR Dow Jones Industrial Average ETF saw record inflows as the "
            "Dow rose [H2]."
        )
        headlines = [
            "Tate Museums Tap Leader of Major U.S. Art Group as Next Director "
            "- The New York Times",
            "SPDR Dow Jones Industrial Average ETF sees inflows as Dow Jones " "rises",
        ]
        res = verify_grounding(
            bull,
            "",
            "",
            _grounding_gathered(headlines),
            "DIA",
            company_tokens=_DIA_COMPANY_TOKENS,
        )
        # The Tate sentence is dropped (entity / no-headline), never displayed.
        tate_drops = [d for d in res.dropped if "tate" in d["claim"].lower()]
        assert tate_drops, "the Tate sentence must be dropped"
        assert tate_drops[0]["reason"].startswith("entity") or tate_drops[0][
            "reason"
        ].startswith("no supporting headline")
        assert "tate" not in res.bull.lower()
        # The genuinely on-subject SPDR/Dow sentence survives.
        assert "inflows" in res.bull.lower()
        assert res.grounding_ok is False

    def test_real_dia_tate_headline_carrying_dia_token_is_dropped(self):
        """#2249 FIX 3 — the REAL live-feed leak. Captured 2026-07-22 (artforum.com):
        "Dia's Jessica Morgan Named Director of Tate" CARRIES the bare ticker token
        "dia" (Dia Art Foundation), which is exactly why the symbol-token filter
        (FIX 2) failed to drop it — it survived, bound, and shipped as bull prose.

        DIA is an AMBIGUOUS ticker: when the company name tokens are known the
        headline must carry a COMPANY-NAME token (spdr/dow/jones/...), not merely
        the bare "dia" token, to enter the corpus / binding pool. So the Tate
        headline is filtered OUT and the fabricated Tate sentence is dropped, while
        the genuinely on-subject SPDR/Dow sentence still survives.
        RED: restore the symbol|company union filter -> "dia" keeps the Tate
        headline -> the Tate sentence binds and lands in res.bull."""
        bull = (
            "Jessica Morgan was named the new director of Tate [H1]. "
            "The SPDR Dow Jones Industrial Average ETF drew record inflows [H2]."
        )
        headlines = [
            # REAL captured headline — carries the bare ticker token "dia".
            "Dia's Jessica Morgan Named Director of Tate",
            # Genuinely on-subject: carries the company-name tokens spdr/dow/jones.
            "SPDR Dow Jones Industrial Average ETF sees record inflows",
        ]
        res = verify_grounding(
            bull,
            "",
            "",
            _grounding_gathered(headlines),
            "DIA",
            company_tokens=_DIA_COMPANY_TOKENS,
        )
        assert "tate" not in res.bull.lower()
        assert "morgan" not in res.bull.lower()
        # The genuinely on-subject SPDR/Dow sentence survives.
        assert "inflows" in res.bull.lower()
        assert res.grounding_ok is False

    def test_real_googl_clean_core_survives_distinctive_ticker(self):
        """Non-over-filter guard for a DISTINCTIVE ticker. GOOGL does NOT collide
        with an everyday word, so the symbol|company UNION filter stays — its real
        clean core must NOT be over-dropped by the new ambiguous-ticker rule.
        Real captured headline (2026-07, Reuters via Yahoo Finance)."""
        bull = (
            "Alphabet aims to raise $80 billion in equity offerings [H1], "
            "diluting existing holders."
        )
        headlines = [
            "Alphabet (GOOGL) Aims to Raise $80 Billion in Equity Offerings - Reuters"
        ]
        res = verify_grounding(
            bull,
            "",
            "",
            _grounding_gathered(headlines),
            "GOOGL",
            company_tokens={"alphabet", "inc"},
        )
        assert "alphabet" in res.bull.lower()
        assert res.grounding_ok is True

    def test_union_keeps_ticker_only_headline_for_nvda(self):
        """Non-over-filtering guard: a ticker-only headline ("SNDK, MU, AMD,
        NVDA ...") carries the SYMBOL token but not the company name. The UNION
        (symbol|company) must keep it so a sentence bound to it survives — a
        company-only filter would wrongly drop it."""
        bull = "NVDA looks attractive on the chip dip [H1]."
        headlines = [
            "SNDK, MU, AMD, NVDA - Is It Time to Buy the Dip in Chip Stocks? "
            "- TipRanks"
        ]
        res = verify_grounding(
            bull,
            "",
            "",
            _grounding_gathered(headlines),
            "NVDA",
            company_tokens={"nvidia", "corp"},
        )
        assert "chip" in res.bull.lower()
        assert res.grounding_ok is True

    def test_grounding_fallback_binding_when_h_tag_missing(self):
        """Fallback binding guard: when LLM omits explicit [H#] tags, post-hoc best match binds headline."""
        bull = "Revvity reported upbeat Q2 earnings driven by strong diagnostics performance."
        headlines = [
            "Revvity Reports Upbeat Q2 Earnings Driven By Strong Diagnostics Performance - TradingView"
        ]
        res = verify_grounding(
            bull,
            "",
            "",
            _grounding_gathered(headlines),
            "RVTY",
            company_tokens={"revvity", "inc"},
        )
        assert "upbeat" in res.bull.lower()
        assert res.grounding_ok is True
