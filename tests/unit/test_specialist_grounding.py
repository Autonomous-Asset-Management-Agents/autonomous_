# tests/unit/test_specialist_grounding.py
# Rich-synthesis card (Direction A) — mechanical headline-grounding gate.
#
# Gherkin (plan):
#   Szenario: Erfundene Fremd-Firma wird gestrichen (der #2202-Fänger)
#     Angenommen die V2-Bull-Prosa behauptet "Partnership with Cerebras Systems"
#     Und "Cerebras" steht in KEINER Headline dieses Symbols
#     Wenn verify_grounding läuft
#     Dann steht der Satz in synthesis_dropped, NICHT im angezeigten bull_case
#     Und grounding_ok ist False
#   Szenario: Erfundene Zahl wird gestrichen
#     Angenommen die Prosa behauptet "$500 price target" ohne Quelle
#     Dann wird der Satz mit Zahl-Grund gestrichen
#   Szenario: Falscher [H#]-Tag wird nachgerechnet (Post-hoc-Bindung)
#     Angenommen der Satz zitiert [H1], passt aber inhaltlich zu H3
#     Dann bindet die Karte an H3, nicht an die falsche Behauptung
#   Szenario: Geerdeter Satz bleibt
#     Angenommen jeder Satz hängt an einer echten Headline
#     Dann überlebt die Prosa unverändert und grounding_ok ist True
#
# HARD INVARIANT: the mechanical auditor (core/report/auditor.py) is BLIND to a
# fabricated sentence (it checks numbers/entities in the FactSet pool, never the
# synthesis prose). This gate is the ONLY brake — so the entity gate (step B) MUST
# drop an unknown named entity. RED is proven by removing step B (see the module
# walkthrough): the Cerebras sentence then survives and this test goes red.

from __future__ import annotations

import allure
import pytest

from core.specialist.grounding import verify_grounding

# Real, self-consistent NVDA headline set — the [H#] corpus.
NVDA_HEADLINES = [
    "Nvidia raises full-year revenue guidance after record data-center sales",  # H1
    "Analysts lift Nvidia price target on strong AI chip demand",  # H2
    "Nvidia unveils new Blackwell GPU architecture at its developer conference",  # H3
]


def _gathered(headlines=None):
    return {
        "recent_headlines": (
            headlines if headlines is not None else list(NVDA_HEADLINES)
        ),
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "reddit_sentiment": "neutral",
    }


# Nvidia's own name tokens (as the runtime loads them from company_cache/NVDA.json).
NVDA_TOKENS = {"nvidia", "corp"}


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding")
class TestEntityGate:
    def test_fabricated_foreign_company_is_dropped(self):
        """The #2202 catcher: a sentence naming a company absent from every
        headline is dropped, never displayed, and flips grounding_ok False."""
        bull = (
            "Nvidia's data-center revenue hit a record high [H1]. "
            "A new partnership with Cerebras Systems was announced [H2]."
        )
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        # The fabricated sentence is dropped with a Cerebras reason ...
        assert any("cerebras" in d["reason"].lower() for d in res.dropped), res.dropped
        # ... and never reaches the displayed prose.
        assert "cerebras" not in res.bull.lower()
        # The grounded first sentence survives.
        assert "record high" in res.bull.lower()
        assert res.grounding_ok is False

    def test_known_and_subject_entities_pass(self):
        """The subject company + entities that DO appear in a headline survive."""
        bull = "Nvidia unveiled its Blackwell GPU architecture [H3]."
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert res.dropped == []
        assert "blackwell" in res.bull.lower()
        assert res.grounding_ok is True


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding")
class TestNumericGate:
    def test_fabricated_number_is_dropped(self):
        """A price/percent/money figure absent from the corpus drops the sentence
        (fundamentals are NOT in the synthesis prompt — no exemption)."""
        bull = "Nvidia's price target was raised to $500 [H2]."
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert any("500" in d["reason"] for d in res.dropped), res.dropped
        assert "500" not in res.bull
        assert res.grounding_ok is False

    def test_calendar_year_and_small_count_are_exempt(self):
        """A bare year (1900-2099) / small count is not a financial figure and
        must not drop an otherwise-grounded sentence."""
        bull = "Nvidia raised its 2026 revenue guidance [H1]."
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert res.dropped == []
        assert "2026" in res.bull


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding")
class TestPositiveBinding:
    def test_wrong_tag_is_rebound_by_content(self):
        """The model's [H#] is verified, never trusted: a wrong tag is corrected
        to the best content match (here H3, the Blackwell headline)."""
        bull = "Nvidia unveiled the Blackwell GPU architecture [H1]."
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert res.dropped == []
        assert len(res.citations) == 1
        assert res.citations[0]["headline_index"] == 3
        assert "Blackwell" in res.citations[0]["headline_text"]

    def test_no_supporting_headline_survives_without_citation(self):
        """Rebalanced (D) gate: a safe sentence (no foreign entities, no foreign
        numbers) with no content overlap with any headline SURVIVES — but earns
        NO citation (no fabricated 'Tied to'). Only fabrication drops."""
        bull = "The board reiterated its long-standing dividend policy [H2]."
        res = verify_grounding(
            bull, "", "", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert "dividend policy" in res.bull.lower()
        assert res.citations == []
        assert res.dropped == []
        assert res.grounding_ok is True


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding — Entity Gate Capitalization")
class TestEntityGateCapitalization:
    """The entity gate (B) counts only CAPITALIZED word-parts as entity evidence.
    Live false drops 2026-07-23: "entity 'The AI-driven' not in any source" /
    "entity 'There' not in any source" — sentence-initial articles and the
    lowercase halves of hyphen compounds are prose, not names."""

    def test_sentence_initial_there_and_hyphen_compound_survive(self):
        g = _gathered(
            headlines=[
                "Gene Munster predicts AI-driven upgrade cycle for Apple - CNBC",
                "Apple: Sell Before Q3 (NASDAQ:AAPL) - Seeking Alpha",
            ]
        )
        bull = (
            "The AI-driven upgrade cycle predicted by Gene Munster will "
            "likely boost Apple's sales and market share [H1]."
        )
        bear = "There is no explicit bear case mentioned in the headlines."
        res = verify_grounding(bull, bear, "", g, "AAPL", company_tokens={"apple"})
        assert "upgrade cycle" in res.bull.lower()
        # A written-out "no explicit bear case" is the NONE sentinel in words —
        # it maps to the retention-phrased honest empty, never displayed raw.
        assert "no supported" in res.bear.lower()
        assert res.dropped == []

    def test_fully_capitalized_foreign_names_still_drop(self):
        g = _gathered(headlines=["Apple: Sell Before Q3 (NASDAQ:AAPL) - Seeking Alpha"])
        res = verify_grounding(
            "The appointment of Jessica Morgan as the Tate Museums new "
            "director benefits Apple [H1].",
            "Cerebras Systems partnership will transform Apple's roadmap [H1].",
            "",
            g,
            "AAPL",
            company_tokens={"apple"},
        )
        reasons = " | ".join(d["reason"] for d in res.dropped)
        assert "Jessica Morgan" in reasons
        assert "Cerebras Systems" in reasons
        assert res.grounding_ok is False


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding")
class TestHonestEmpties:
    def test_none_field_yields_placeholder_not_drop(self):
        """The prompt's own NONE sentinel is an honest empty, not a drop."""
        res = verify_grounding(
            "NONE", "NONE", "NONE", _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert res.dropped == []
        assert res.grounding_ok is True
        assert "no supported" in res.bull.lower()
        assert res.citations == []

    def test_all_fields_grounded_is_ok(self):
        """A fully grounded set survives verbatim and reports grounding_ok True."""
        bull = "Nvidia raised its full-year revenue guidance on record data-center sales [H1]."
        bear = "Analysts' higher price target hinges on continued AI chip demand [H2]."
        thesis = "The Blackwell GPU architecture launch anchors the growth story [H3]."
        res = verify_grounding(
            bull, bear, thesis, _gathered(), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert res.dropped == []
        assert res.grounding_ok is True
        assert len(res.citations) == 3
        assert res.bull.endswith(".") and "[H1]" not in res.bull  # tag stripped

    def test_empty_headlines_drop_everything(self):
        """No corpus (cold cache) → nothing is verifiable → every claim is dropped
        by the no-corpus guard, an honest empty card (never fabricated)."""
        bull = "Nvidia posted record data-center revenue [H1]."
        res = verify_grounding(
            bull, "", "", _gathered(headlines=[]), "NVDA", company_tokens=NVDA_TOKENS
        )
        assert "no supported" in res.bull.lower()
        assert any("no sources in corpus" in d["reason"] for d in res.dropped)
        assert res.grounding_ok is False


@allure.feature("VC-1 Research & Analysis")
@allure.story("Synthesis Card Grounding")
class TestPurity:
    def test_pure_no_input_mutation(self):
        import copy

        g = _gathered()
        snap = copy.deepcopy(g)
        verify_grounding(
            "Nvidia revenue hit a record [H1].",
            "",
            "",
            g,
            "NVDA",
            company_tokens=NVDA_TOKENS,
        )
        assert g == snap
