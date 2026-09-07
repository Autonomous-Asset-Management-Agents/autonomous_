# tests/unit/test_report_narrator_derived_prompt.py
"""The narrator's slot instructions must be DERIVED per symbol, not dictated.

Regression cover for the NVDA-dictation defect: ``_SLOT_INSTRUCTIONS`` hardcoded
NVDA's story (top-quintile, rising-score divergence, price above the MAs, an
earnings date "shortly after the as-of", "belegte Wettbewerbs-/Wachstumssorgen")
into EVERY symbol's prompt. The model was not hallucinating — it was OBEDIENT:
a real GD note named the ETF "Global X's SHLD" as a competitor because the prompt
ORDERED a competition risk and the FactSet proved none.

Each test builds a REAL FactSet through ``build_fact_set`` (no MagicMock: the
code does ``if x <= 0`` on these values) and inspects the ASSEMBLED PROMPT.
"""

import json
import os
from datetime import date

from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set
from core.report.narrator import SLOT_NAMES, _build_prompt, serialize_fact_set

_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)


def _load_json(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _load_card():
    with open(_CARD, encoding="utf-8") as f:
        return json.load(f)


def _xsec_at(pct_below, n=100, symbol="NVDA"):
    """A real cross-section putting ``symbol`` at percentile ``pct_below``.

    ``cross_section_standing`` (lstm_panel_store.py:196) defines
    percentile = 100 * (# peers scoring BELOW) / n. So n=100 with ``pct_below``
    peers under the symbol lands it exactly on that percentile.
    """
    xsec = {symbol: 0.50}
    for i in range(pct_below):
        xsec[f"LOW{i}"] = 0.10
    for i in range(n - pct_below - 1):
        xsec[f"HIGH{i}"] = 0.90
    return xsec


def _ctx(**overrides):
    kw = {
        "bars": [
            (date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")
        ],
        "lstm_series": [
            (date.fromisoformat(d), float(s))
            for d, s in _load_json("nvda_lstm_series.json")
        ],
        "lstm_cross_section": _xsec_at(25),
        "model_card": _load_card(),
        "fundamentals": None,
        "news": [
            NewsItem(
                title=n["title"],
                published_utc=n["published_utc"],
                publisher=n["publisher"],
                url=n["url"],
            )
            for n in _load_json("nvda_news.json")
        ],
        "alt_signals": {},
    }
    kw.update(overrides)
    return InMemoryDataContext(**kw)


def _fs(**overrides):
    return build_fact_set("NVDA", AS_OF, _ctx(**overrides))


def _prompts(fs):
    """Every slot's assembled prompt — what the model actually receives."""
    serialized = serialize_fact_set(fs)
    return {n: _build_prompt(fs, n, serialized, None) for n in SLOT_NAMES}


def _instruction(prompt):
    """The AUFGABE block only — excludes the FACT SET and the model-card line.

    Scoped deliberately: the model card legitimately names the strategy bucket
    ("Brutto-Sharpe Top-Quintile ... vs EqualWeight") — that is an aggregate
    metric about the METHOD, correctly scoped, and not a claim about THIS symbol.
    The defect is a band claim about the SYMBOL.
    """
    return prompt.split("AUFGABE:", 1)[1]


def _factset_score_line(prompt):
    for line in prompt.splitlines():
        if line.startswith("LSTM-Score:"):
            return line
    raise AssertionError("no LSTM-Score line in the FACT SET")


# ---------------------------------------------------------------------------
# THE key test: a mid-field symbol is never ordered into the top quintile.
# ---------------------------------------------------------------------------
def test_midfield_symbol_is_never_told_it_is_top_quintile():
    """Percentile ~25 -> no prompt may assert a top-quintile band. THE defect.

    LYB at cross-sectional percentile 25.5 was written up as "Top-Quintil".
    Ordered, not hallucinated.
    """
    fs = _fs()
    assert fs.lstm_xsec_percentile.value == 25.0  # fixture sanity, not a mock

    for name, prompt in _prompts(fs).items():
        assert "Top-Quintile" not in _instruction(prompt), (
            f"slot {name!r} ORDERS a top-quintile claim for a symbol at "
            f"percentile {fs.lstm_xsec_percentile.value}"
        )
    assert "Top-Quintile" not in _factset_score_line(_prompts(fs)["thesis"])


def test_midfield_factset_line_states_the_real_band():
    """The band comes from render._rec_parts, the renderer's own helper."""
    line = _factset_score_line(_prompts(_fs())["thesis"])
    assert "Mittelfeld" in line
    assert "Perzentil 25.0" in line


def test_top_quintile_symbol_still_gets_its_real_band():
    """The fix must not simply delete the band — a real top name keeps it."""
    fs = _fs(lstm_cross_section=_xsec_at(95))
    assert fs.lstm_xsec_percentile.value == 95.0
    line = _factset_score_line(_prompts(fs)["thesis"])
    assert "oberen" in line and "Fünftel" in line


def test_no_rank_means_no_band_claim_anywhere():
    """Fail-closed, like _rec_parts: no rank -> no band, not a neutral one."""
    fs = _fs(lstm_cross_section={})
    assert fs.lstm_xsec_percentile.value is None
    for name, prompt in _prompts(fs).items():
        instruction = _instruction(prompt)
        for band in ("Top-Quintile", "Mittelfeld", "Fünftel", "Quintil"):
            assert band not in instruction, f"slot {name!r} claims band {band!r}"


# ---------------------------------------------------------------------------
# The earnings-date mandate: the FactSet has NO earnings field at all.
# ---------------------------------------------------------------------------
def test_catalysts_does_not_mandate_an_earnings_date_without_event_news():
    """No event headline -> no "Ergebnistermin kurz nach dem Stichtag" order.

    This is the GD failure mode: the prompt mandated a date the FactSet could
    never carry, so the model manufactured one.
    """
    fs = _fs(
        news=[
            NewsItem(
                title="Defense ETFs: SHLD Has Lower Fees, PPA Boasts More Holdings",
                published_utc="2026-04-20T12:00:00+00:00",
                publisher="The Motley Fool",
                url="https://example.com/a",
            )
        ]
    )
    instruction = _instruction(_prompts(fs)["catalysts"])
    assert "Ergebnistermin" not in instruction
    assert "Stichtag liegt" not in instruction


def test_catalysts_may_mention_the_event_when_a_headline_proves_one():
    fs = _fs(
        news=[
            NewsItem(
                title="Nvidia to report first-quarter earnings on May 28",
                published_utc="2026-04-20T12:00:00+00:00",
                publisher="Reuters",
                url="https://example.com/b",
            )
        ]
    )
    assert "Ergebnistermin" in _instruction(_prompts(fs)["catalysts"])


# ---------------------------------------------------------------------------
# The risks mandate: "belegte Wettbewerbs-/Wachstumssorgen" with no evidence.
# ---------------------------------------------------------------------------
def test_risks_does_not_mandate_competition_concerns_without_evidence():
    """The GD/SHLD bug: no structural headline, no cons -> no ordered rival."""
    fs = _fs(
        news=[
            NewsItem(
                title="Which Defense ETF Is the Better Investment: Global X's "
                "SHLD or iShares' ITA?",
                published_utc="2026-04-20T12:00:00+00:00",
                publisher="The Motley Fool",
                url="https://example.com/c",
            )
        ],
        alt_signals={},
    )
    instruction = _instruction(_prompts(fs)["risks"])
    assert "Wettbewerb" not in instruction
    assert "Wachstumssorgen" not in instruction


def test_risks_keeps_the_execution_risk_which_the_model_card_evidences():
    assert "EqualWeight" in _instruction(_prompts(_fs())["risks"])
