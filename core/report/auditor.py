# core/report/auditor.py
# RPT-4 (#2003, Epic #1998) — the mechanical auto-audit gate: the Moat / BaFin
# core of the auditable GS-quality research reports.
"""Deterministic, LLM-free auditor: prove every claim of a report against a FactSet.

This is the *audit* layer of the auditable report pipeline (Epic #1998). It is
the mechanical gate that makes the report **falsifiable**: it extracts every
number, date, ticker/entity, source-URL and provenance string from a rendered
report and proves each one against the frozen, fully-provenanced
:class:`~core.report.fact_set.FactSet` that the report was built from. A claim
that cannot be traced to a Fact value, a news item, a model-card constant or a
small, explicitly-allowed structural/editorial set is a **STRIKE** — an
unsourced (fabricated) claim. A report with zero strikes is ``integrity_ok``.

Why this is the moat: a BaFin/MiFID audit trail needs machine-checkable
attribution. The renderer (RPT-3) promises it invents nothing; this module is
the adversary that *verifies* that promise on the finished text, so a
hallucinated number, price target, merger amount or ticker can never pass.

Design contract (BORA / pure core):

* **Pure & deterministic.** No I/O, no persistence backend, no network, **no
  LLM**, no GPU, no ``pandas``, no clock (``datetime.now``), no randomness.
  Given the same ``(report_md, fact_set)`` it returns an identical
  :class:`AuditResult`. It is edition-agnostic (no Redis / SQLite /
  ``DEPLOYMENT_MODE`` / edition special-casing).
* **Report-only and DORMANT.** Nothing here is read by the decision path;
  ``SPECIALIST_ALPHA_WEIGHT`` stays 0.

What the mechanical check DOES catch (fabrication):

* invented numbers (a merger amount, a Sharpe of 3.0, a fabricated hit-rate);
* a hallucinated price target (``$250``) where the model emits no return number;
* a magnitude dressed as verified fact (``"verifizierte 5 Bio"`` vs the FactSet's
  own ``4.87`` Bio market cap — caught because a *money-unit* number may never be
  explained away by a small structural integer);
* a fabricated ticker / entity, a fabricated date, a fabricated source URL.

What it does NOT catch — the honest SEMANTIC boundary (see
:func:`semantic_attribution_hook`):

* a **real** fact wired to the **wrong** subject. "Burry schaetzte die
  $5-Bio-Marktkap" contains a number that IS in a source; the mechanical gate
  confirms the number is real, but it cannot judge whether the *attribution* is
  faithful. That semantic-attribution pass is RPT-4b — an optional LLM judge
  behind the ``get_llm()`` seam — and is deliberately **not** built here so the
  deterministic core stays LLM-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from datetime import date, datetime
from typing import Any, Callable, List, Optional, Tuple

from core.report.fact_set import Extreme, FactSet, NewsItem

# ---------------------------------------------------------------------------
# Verdicts (module-level string constants — stable, machine-parseable).
# ---------------------------------------------------------------------------
VALID = "VALID"  # matched a FactSet Fact / news / model-card value
DERIVED = "DERIVED"  # matched a value in the derived registry (P/E, P/S, ...)
STRUCTURAL = "STRUCTURAL"  # a section number / year / small bare integer
EDITORIAL = "EDITORIAL"  # a labelled, non-model editorial calibration constant
UNSOURCED = "UNSOURCED"  # a number / date / URL that traces to nothing -> STRIKE
UNSOURCED_ENTITY = "UNSOURCED_ENTITY"  # a ticker/entity that traces to nothing
CONTRADICTED = "CONTRADICTED"  # a QUALITATIVE claim the FactSet denies -> STRIKE

# ---------------------------------------------------------------------------
# Tunables (all display/heuristic — no order or capital path reads them).
# ---------------------------------------------------------------------------
# ADR-RPT4-01: rounding tolerance. A rendered number is a *rounded* view of a raw
#   Fact, so an exact-string match is wrong. We accept a number N as sourced when
#   it lands within max(_ABS_TOL, _REL_TOL * |V|) of a pool value V. 2 % relative
#   is deliberate: it forgives display rounding (198.45 <- 198.4499) yet rejects a
#   magnitude dressed as a real fact ("5 Bio" is 2.67 % away from 4.87 -> STRIKE).
_REL_TOL = 0.02
_ABS_TOL = 0.005

# A float fact with |value| <= this is treated as a *fraction* and may legitimately
# render as a percent (value * 100), e.g. realized vol 0.349 -> "34.9 %". Bounded
# so a real score like 4.10 can never be re-read as 410.
_FRACTION_MAX = 1.5

# ADR-RPT4-02: structural tolerance. Bare integers in [0, _STRUCTURAL_MAX_INT]
#   ("~5 days", "Abschnitt 2") and 4-digit years are template scaffolding, never
#   quantitative claims — UNLESS they carry a magnitude unit.
_STRUCTURAL_MAX_INT = 9
_YEAR_LO, _YEAR_HI = 1900, 2100

# ADR-RPT4-07: section-heading numbers are scaffolding, matched STRUCTURALLY on
#   their POSITION (the "N." of a "## N. Titel" heading), not on their magnitude.
#   Without this, a heading number is only saved by the small-int pass, which caps
#   at 9 — so "## 10." survived purely by ACCIDENT (it matched the model card's
#   unrelated cost_bps=10) and "## 11." struck outright. That is the same latent
#   class as a bare "MA200" auditing only when the price happens to sit near 200:
#   scaffolding must never depend on a numeric coincidence. Anchored to the line
#   start + the heading marker, so a body number can never borrow this pass.
_HEADING_NUM_RE = re.compile(r"^(#{1,6}[ \t]+)(\d{1,2})\.", re.M)

# ADR-RPT4-03: editorial calibration allow-list. The Abschnitt-7 scenario weights
#   are fixed, explicitly-labelled analyst calibration (render ADR-RPT3-02), not
#   model outputs — so they are not in the FactSet, yet must not strike. Kept tiny
#   and money-blind (a money magnitude can never be "explained" by these). 30 is
#   the symmetric-HOLD weight (Bull=Bear=30, Base=40) so a neutral call reads as
#   genuinely balanced rather than forced into an asymmetric split.
_EDITORIAL_VALUES: Tuple[float, ...] = (40.0, 35.0, 30.0, 25.0)

# ADR-RPT4-08: the recommendation verbs render._rec_parts can emit. Bound to the bold
# marker so prose ABOUT a call ("...wird bewusst keine Empfehlung ausgesprochen...")
# is never mistaken for one. VERKAUFEN must precede KAUFEN in the alternation — it
# CONTAINS it, and Python alternation is first-match-wins, so the shorter branch would
# otherwise swallow the longer one and report a SELL as a BUY. (Same trap that made an
# earlier test assert "KAUFEN" not in head and pass while VERKAUFEN sat right there.)
_RECOMMENDATION_VERB_RE = re.compile(r"\*\*(VERKAUFEN|KAUFEN|HALTEN)\b")

# ---------------------------------------------------------------------------
# ADR-AUDIT-BAND-01: a RANK-BAND claim is a claim too — and it can LIE.
# ---------------------------------------------------------------------------
# Every rule above extracts a NUMBER / date / entity / URL / source and proves it
# against the FactSet. That is structurally blind to a *qualitative* claim, and a
# real LYB note (2026-07-17) walked straight through the gate with ZERO flags:
#
#   "LYB liegt im Top-Quintil des Modells, was darauf hindeutet, dass das
#    Unternehmen eine starke Position innerhalb seines Universums einnimmt."
#
# LYB was rank 372 of 499 — the 25.5th percentile. BOTTOM quartile. The sentence
# asserts the exact opposite of the data, contains no number, and therefore no
# rule could see it. It reached the customer unmediated: the round table votes on
# data, not on report prose, so there is no downstream guard.
#
# The rule: a band phrase is a testable assertion about ``lstm_xsec_percentile``.
# We hold it to the SAME single validated field the recommendation is derived
# from (render._rec_parts), so the prose can never disagree with the call.
#
# Thresholds MIRROR render's quintile constants deliberately (drift is pinned by
# test_auditor_bands_mirror_render_quintile_constants). They are NOT re-derived
# from taste: the renderer emits "im **Mittelfeld**" for the whole open interval
# (20, 80), so a tighter "midfield = 25..75" reading would flag the RENDERER'S
# OWN honest output at pct 22 — inventing a defect instead of catching one. The
# auditor stays import-independent of render (it is the adversary, not a client);
# the mirror is asserted by test, not by import.
_TOP_QUINTILE_PCT = 80.0
_BOTTOM_QUINTILE_PCT = 20.0
_TOP_QUARTILE_PCT = 75.0
_BOTTOM_QUARTILE_PCT = 25.0
_MEDIAN_PCT = 50.0

# German declension + the two spellings the narrator actually emits: its prompt is
# ASCII-transliterated ("zuverlaessig"), so the model returns both "Fuenftel" and
# "Fünftel". Matching only one spelling is a silent bypass.
_ADJ_TOP = r"(?:ober|oberst)(?:es|en|em|er|e)?"
_ADJ_BOT = r"(?:unter|unterst)(?:es|en|em|er|e)?"
# Trailing \b + an explicit genitive/plural tail: "des oberen Fuenftels" must match,
# "im oberen Viertelbereich" / "eine starke Positionsveraenderung" must NOT. Without
# the boundary these are the same class the "Schlusskurse" guard above avoids.
_FUENFTEL = r"F(?:ü|ue)nftels?\b"
_HAELFTE = r"H(?:ä|ae)lften?\b"
_VIERTEL = r"Viertels?\b"

# One alternation of NAMED groups -> ``m.lastgroup`` names the band and each span
# is consumed exactly once, so overlapping phrases can never double-count.
# ⚠ "Schluss" is never matched bare: insight_patterns.py:207 says "Schlusskurse".
_BAND_CLAIM_RE = re.compile(
    r"\b(?:"
    r"(?P<top_quintile>Top[-\s]?Quintil\w*|" + _ADJ_TOP + r"\s+" + _FUENFTEL + r"|"
    r"Spitzengruppe\w*|Spitzenreiter\w*|Spitzenposition\w*|Spitzenfeld\w*)"
    r"|(?P<top_quartile>Top[-\s]?Quartil\w*|" + _ADJ_TOP + r"\s+" + _VIERTEL + r")"
    r"|(?P<top_half>" + _ADJ_TOP + r"\s+" + _HAELFTE + r"|besseren\s+" + _HAELFTE + r")"
    r"|(?P<midfield>Mittelfeld\w*)"
    r"|(?P<bottom_half>" + _ADJ_BOT + r"\s+" + _HAELFTE + r"|"
    r"schlechteren\s+" + _HAELFTE + r")"
    r"|(?P<bottom_quartile>Schluss[-\s]?Quartil\w*|"
    + _ADJ_BOT
    + r"\s+"
    + _VIERTEL
    + r")"
    r"|(?P<bottom_quintile>Schlusslicht\w*|Schlussgruppe\w*|Schlussfeld\w*|"
    + _ADJ_BOT
    + r"\s+"
    + _FUENFTEL
    + r")"
    r"|(?P<strong>stark\s+positioniert|starke[nrms]?\s+(?:Position|Stellung)(?:en)?\b)"
    r"|(?P<weak>schwach\s+positioniert|schwache[nrms]?\s+(?:Position|Stellung)(?:en)?\b)"
    r")",
    re.IGNORECASE,
)

# ADR-AUDIT-BAND-01b: "starke Position" is ordinary business German for a MOAT, not a
# rank band. Unqualified, it is not a claim about lstm_xsec_percentile at all:
#   "eine starke Position im europaeischen Polyolefin-Markt"       -> a moat statement
#   "Wettbewerber greifen die starke Stellung des Unternehmens an" -> about a RIVAL
# Flagging those is a false positive, and the mirror image is worse: at pct 90 the
# rival sentence would be recorded VALID — "a strong standing is consistent with the
# 90.0th percentile" — a BaFin audit row attesting that a rank claim was verified
# when the sentence never made one. The risks slot is explicitly ASKED for
# "Wettbewerbs-/Wachstumssorgen" (narrator.py:229), so this text is expected.
# So strong/weak count ONLY with rank vocabulary in the immediate window. The real
# LYB sentence qualifies ("eine starke Position innerhalb seines UNIVERSUMS"), and
# "Top-Quintil" in that same sentence catches the regression independently — these
# two groups are precision, never the load-bearing catch. The other seven groups are
# unambiguous rank vocabulary and need no qualifier.
_RANK_CONTEXT_RE = re.compile(
    r"Universum\w*|Rang\w*|Perzentil\w*|Quintil\w*|Quartil\w*|Vergleich\w*|Modell\w*",
    re.IGNORECASE,
)
# The context is the phrase's OWN SENTENCE, not a character window. A window bleeds
# across section boundaries and picks up the renderer's neighbouring "Rang"/"Modell"
# prose, which would re-flag the very moat sentence this gate exists to spare.
_SENTENCE_END = (".", "!", "?", "\n")
_CONTEXT_REQUIRED = frozenset({"strong", "weak"})


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence containing ``text[start:end]`` (bounded by .!? or a newline)."""
    left = max(text.rfind(c, 0, start) for c in _SENTENCE_END)
    rights = [p for p in (text.find(c, end) for c in _SENTENCE_END) if p != -1]
    return text[left + 1 : min(rights) if rights else len(text)]


# (inclusive_low, inclusive_high, human description). ``None`` = unbounded.
_BANDS = {
    "top_quintile": (_TOP_QUINTILE_PCT, None, "top quintile"),
    "top_quartile": (_TOP_QUARTILE_PCT, None, "top quartile"),
    "top_half": (_MEDIAN_PCT, None, "upper half"),
    "midfield": (_BOTTOM_QUINTILE_PCT, _TOP_QUINTILE_PCT, "midfield"),
    "bottom_half": (None, _MEDIAN_PCT, "lower half"),
    "bottom_quartile": (None, _BOTTOM_QUARTILE_PCT, "bottom quartile"),
    "bottom_quintile": (None, _BOTTOM_QUINTILE_PCT, "bottom quintile"),
    "strong": (_MEDIAN_PCT, None, "a strong standing"),
    "weak": (None, _MEDIAN_PCT, "a weak standing"),
}

# The ``## 7. Szenarien`` block is carved out of the band pass. It is 100 %
# DETERMINISTIC renderer output (``_section_scenarios(fs)`` takes no ``narration``
# — verified render.py:423), so excluding it costs ZERO detection power against
# the LLM, which is the only thing that can lie here. And it must be excluded: the
# scenario table states DIRECTIONAL hypotheticals that are contradictory by
# design — "Aufstieg ins obere Fünftel aus dem Mittelfeld" at mid-rank,
# "Erholung aus der Schlussgruppe ... Richtung Mittelfeld" at pct <= 20. Scanning
# them would flag the renderer on essentially every report, blank all three slots
# via the narrator's persisting-strike path, and fail the release gate.
# Anchored to the heading AND the renderer's exact table header, and pinned to real
# rendered output by test_scenarios_carveout_matches_the_renderers_actual_heading.
#
# ⚠ The table header is part of the anchor on purpose. ``render()`` is public API and
# takes an arbitrary ``narration`` mapping, so a slot containing a bare line
# "## 7. Szenarien" would otherwise carve out the whole rest of the report and
# silently DISABLE this rule — the auditor is the adversary and must not be
# switchable by the text it audits. (Not reachable through ``narrate()`` today only
# because ``_strip_preamble`` folds slots to one line — an internal of the very
# module we audit, which is not a guarantee we may lean on.) Reproducing the heading
# AND the exact table header is not something narration does by accident.
_SCENARIOS_BLOCK_RE = re.compile(
    r"^#{1,6}[ \t]+\d+\.[ \t]+Szenarien\b[^\n]*\n\s*"
    r"\|[ \t]*Szenario[ \t]*\|[ \t]*Gewichtung\b.*?(?=^#{1,6}[ \t]|\Z)",
    re.M | re.S,
)

# Units that make a number a *magnitude* claim. A money/percent number can never
# fall back to the small-integer structural pass.
_MONEY_UNITS = frozenset(
    {"$", "Bio", "Mrd", "Mio", "USD", "EUR", "Billion", "Trillion"}
)
_UNIT_ALTERNATION = r"%|Bio|Mrd|Mio|bps|USD|EUR|Billion|Trillion"

# Known ALL-CAPS report vocabulary that is *not* a ticker: German connective /
# recommendation words + finance acronyms. Anything here is skipped by the entity
# pass so it can never be mistaken for an unsourced ticker (precision guard). The
# FactSet's own vocabulary (symbol, news titles) is checked separately and wins.
_ENTITY_STOPWORDS = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "PIT",
        "RPT",
        "ADR",
        "MA",
        "IC",
        "EPS",
        "EK",
        "CF",
        "BUY",
        "SELL",
        "HOLD",
        "KEIN",
        "KEINE",
        "KEINER",
        "ABER",
        "UNTER",
        "UEBER",
        "NICHT",
        "BELOW",
        "ABOVE",
        "EIN",
        "EINE",
        "DER",
        "DIE",
        "DAS",
        "UND",
        "VON",
        "MIT",
        "FY",
        "YOY",
        "PEG",
        "KGV",
        "KUV",
        "BIO",
        "MRD",
        "MIO",
        "BPS",
        "AND",
        "THE",
        "OR",
        "NOT",
        "CEO",
        "CFO",
        "IPO",
        "USA",
        "EU",
        "AG",
        "GMBH",
        "UG",
        "SE",
        "PE",
        "PS",
    }
)

# ---------------------------------------------------------------------------
# Regexes (compiled once; deterministic).
# ---------------------------------------------------------------------------
# ISO calendar date.
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# A standalone number: optional sign / currency prefix, integer with optional
# thousands groups, optional decimals, NOT glued to a following letter/digit
# (so "MA20", "63d", "FY2026" are labels, not numbers), then an optional unit.
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9.,])([-+$]?)(\d{1,3}(?:,\d{3})+|\d+)"
    r"(\.\d+|,\d{1,2}(?![\d,]))?"  # decimal: dot (EN) OR comma 1-2 digits (DE); a
    r"(?![A-Za-z0-9])"  # 3-digit comma group stays thousands via the int alt above
    r"\s*(" + _UNIT_ALTERNATION + r")?"
)
# A cashtag ($AAPL) and a bare ticker-shaped ALL-CAPS run.
_CASHTAG_RE = re.compile(r"\$([A-Z]{1,5})\b")
_BARE_CAPS_RE = re.compile(r"(?<![A-Za-z$])([A-Z]{2,5})(?![A-Za-z])")
# Provenance annotations emitted by the renderer. A ``[src: ...]`` string itself
# may carry a bracketed symbol (``bars[NVDA] close ...``), so the src pattern
# tolerates one level of nested brackets; URLs never contain ``]``.
_URL_ANNOT_RE = re.compile(r"\[url:\s*([^\]]+?)\s*\]")
_SRC_ANNOT_RE = re.compile(r"\[src:\s*((?:[^\[\]]|\[[^\]]*\])*?)\s*\]")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_STR_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

# ADR-RPT4-05: news-headline figures. A number that appears VERBATIM in a PIT
#   news title/description is a *sourced* figure — the headline itself is the
#   provenance a BaFin/MiFID trail needs — so it must not strike. Unlike the
#   computed number pool, a news number keeps its thousands separators
#   ("13,600%", "$25,000"). A number that is in NO news AND NO fact still strikes.
#
#   The small-int money-guard (a small integer with a money unit may never be
#   "explained" — "5 Bio" must not match horizon_days=5 nor a 4.87 market cap)
#   is bypassed for a news figure ONLY when the news presented that number with a
#   matching *money-SCALE class* (Trillion/Billion/Million/Thousand). This is
#   deliberately strict: a bare currency ("$5"), a percent ("5 %"), or a scale
#   word buried inside a longer word ("8 billionaires", "3 biotech") does NOT
#   grant the bypass, and a wrong scale class ("5 million" vs a "5 Bio" claim)
#   does not either — so no small "$"/"%"/lookalike news figure can ever source a
#   fabricated money magnitude of the same mantissa.
# Word-bounded scale/percent units (English + German), matched next to a number.
_NEWS_MAG_WORD = (
    r"%|percent|prozent|bps|"
    r"trillion|billionen|billion|millionen|million|thousand|"
    r"bn|tn|mn|bio|mrd|mio|milliarden|milliarde"
)
# Boundary-disciplined (mirrors _NUMBER_RE): the number is not glued to a label,
# and the unit must be a whole word (so "biotech"/"billionaires" do not match).
_NEWS_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])"  # left boundary — not the tail of a label (MA20, FY2026)
    r"([$€£])?\s?"  # optional currency prefix ($, EUR, GBP)
    r"(\d{1,3}(?:,\d{3})+|\d+)"  # integer part: thousands-grouped OR plain
    # decimal: dot (English) OR comma with 1-2 digits (German "8,3" = 8.3). A
    # 3-digit comma group is a thousands separator (kept whole by the int alt),
    # so "8,3 Mrd" is ONE figure (8.3, scale B) and never leaves a stray "3 Mrd".
    r"(\.\d+|,\d{1,2}(?![\d,]))?"
    r"(?![A-Za-z0-9])"  # right boundary of the number
    r"(?:\s?(" + _NEWS_MAG_WORD + r")(?![A-Za-z]))?",  # optional whole-word unit
    re.IGNORECASE,
)
# Money-SCALE classes (order/magnitude of a figure). English news feed convention:
# "billion" = 1e9 (B); German "Bio"/"Billion(en)" = 1e12 (T), "Mrd"/"Milliarde" =
# 1e9 (B). Percent / bps / bare currency are NOT a scale class -> value None ->
# they never grant the money-guard bypass.
_SCALE_CLASSES = {
    "trillion": "T",
    "tn": "T",
    "bio": "T",
    "billionen": "T",
    # KNOWN LIMIT (RPT-4c): bare "billion" is EN 1e9 (B) but DE singular "Billion"
    # is 1e12 (T) — a token-level ambiguity we cannot resolve without locale
    # context. We keep the EN convention (billion->B) to preserve EN-source ->
    # DE-report ("5 billion" -> "5 Mrd") recall. The residual DE collision needs
    # grammatically-atypical German (Germans write "Billionen"->T, already
    # correct) AND a same-mantissa fabrication; the durable fix is the RPT-4b
    # semantic judge. "bio"/"billionen" (the real DE forms) map to T correctly.
    "billion": "B",
    "bn": "B",
    "mrd": "B",
    "milliarde": "B",
    "milliarden": "B",
    "million": "M",
    "millionen": "M",
    "mn": "M",
    "mio": "M",
    "thousand": "K",
}


# ---------------------------------------------------------------------------
# Value objects (frozen -> hashable, comparable, determinism-friendly).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Claim:
    """One extracted claim and its verdict.

    ``token`` is the verbatim text as it appears in the report (e.g. ``"198.45
    USD"``, ``"2026-05-01"``, ``"$TSLA"``). ``kind`` is one of ``"number"`` /
    ``"date"`` / ``"entity"`` / ``"url"`` / ``"source"``. ``value`` is the parsed
    float for numbers (else ``None``). ``matched`` is the load-bearing boolean.
    ``verdict`` is one of the module verdict constants. ``note`` is a short,
    human-readable audit reason (the provenance it matched, or why it struck).
    """

    token: str
    kind: str
    matched: bool
    verdict: str
    value: Optional[float] = None
    unit: Optional[str] = None
    note: str = ""


@dataclass(frozen=True)
class AuditResult:
    """The verdict of auditing one report against one FactSet."""

    claims: Tuple[Claim, ...]
    integrity_ok: bool
    flags: Tuple[Claim, ...]

    def summary(self) -> str:
        """One-line machine-parseable summary."""
        n = len(self.claims)
        s = len(self.flags)
        status = "OK" if self.integrity_ok else "STRIKE"
        return f"integrity={status} claims={n} flags={s}"

    def to_table(self) -> str:
        """Render a deterministic Markdown audit table (cf. nvda_audit_table.md)."""
        header = (
            "| # | Behauptung (Token) | Typ | Urteil | Quelle / Grund |\n"
            "|---|---|---|---|---|"
        )
        rows = [header]
        for i, c in enumerate(self.claims, start=1):
            mark = "" if c.matched else " (STRIKE)"
            note = c.note.replace("|", "\\|")
            token = c.token.replace("|", "\\|")
            rows.append(f"| {i} | {token} | {c.kind} | {c.verdict}{mark} | {note} |")
        return "\n".join(rows)


# ---------------------------------------------------------------------------
# Pool construction (what a claim is allowed to match).
# ---------------------------------------------------------------------------
class _Pool:
    """The set of legitimate values a report may reference, built from a FactSet.

    Everything here is derived from the FactSet alone — the auditor invents no
    reference of its own, exactly as the renderer invents no number of its own.
    """

    def __init__(self, fact_set: FactSet) -> None:
        self.numbers: List[float] = []
        self.derived: List[float] = []
        # ADR-AUDIT-BAND-01b: the raw PIT news text, lower-cased, for the band rule.
        # Same doctrine as ADR-RPT4-05 for numbers: a band phrase that appears
        # VERBATIM in a headline is the PUBLISHER's characterization, quoted with its
        # provenance — not our claim about the rank — so it must not strike. Without
        # this, a headline like "Analysts say NVDA ranks in the top quintile" renders
        # verbatim into the news section and produces an UNRECOVERABLE red report: the
        # token is in no narration slot, so _slots_with_flags implicates all three
        # (narrator.py:361), blanking every slot cannot clear it, and the release gate
        # (generate_report.py, exit 0 iff integrity_ok) fails forever with all prose
        # gone. Latent today (no caller passes ``news=``) and live the moment the feed
        # is wired, which fact_set.py plans.
        self.news_text: str = ""
        # News-headline figures (ADR-RPT4-05): numbers that appear verbatim in a
        # PIT news title/description. ``news_scales`` maps a value -> the set of
        # money-SCALE classes (T/B/M/K) it was presented with in news; only a
        # matching scale class may bypass the small-int money-guard. Kept separate
        # from ``numbers`` so a match is reported with its own audit note and never
        # perturbs the computed-fact matching.
        self.news_numbers: set = set()
        self.news_scales: dict = {}
        self.words: set = {fact_set.symbol.lower()}
        self.dates: set = {fact_set.as_of.isoformat()}
        self.urls: set = set()
        self.sources: set = set()

        for f in dataclass_fields(fact_set):
            if f.name in ("symbol", "as_of"):
                continue
            fact = getattr(fact_set, f.name)
            # Fact.src is provenance the renderer surfaces verbatim in [src: ...].
            if getattr(fact, "src", None):
                self.sources.add(fact.src)
            self._absorb(fact.value)

        # Derived registry (ADR-RPT4-04): ratios a report may legitimately compute
        # from Facts even though they are not stored as their own field. Kept in a
        # separate pool so such a match is reported as DERIVED, not VALID.
        fund = fact_set.fundamentals.value or {}
        close = fact_set.close.value
        eps = fund.get("eps_diluted")
        if close and eps:
            self.derived.append(close / eps)  # P/E = close / diluted EPS
        mktcap = fund.get("market_cap_bn")
        revenue = fund.get("revenue_bn")
        if mktcap and revenue:
            self.derived.append(mktcap * 1000.0 / revenue)  # P/S (Bio -> Mrd)
        # PEG = P/E / revenue-growth-% — the ratio the RPT-7 valuation-vs-growth
        # insight surfaces (e.g. 40.5 / 65.5 = 0.62). Derived from Facts, so a
        # report that states it is DERIVED, not a fabrication.
        pe = fund.get("pe")
        rev_yoy = fund.get("revenue_yoy_pct")
        if pe and rev_yoy:
            self.derived.append(float(pe) / float(rev_yoy))  # PEG

        # Point-in-time dates the report may legitimately carry.
        for ni in fact_set.news_items.value or []:
            self.dates.add(_date_part(ni.published_utc))
            self.urls.add(ni.url)
        if fund.get("filing_date"):
            self.dates.add(str(fund["filing_date"]))

        self.numbers = sorted(set(self.numbers))
        self.derived = sorted(set(self.derived))

    # -- absorbers ----------------------------------------------------------
    def _add_number(self, value: Any) -> None:
        try:
            f = float(value)
        except (TypeError, ValueError):
            return
        self.numbers.append(f)
        if abs(f) <= _FRACTION_MAX:  # a fraction may render as a percent
            self.numbers.append(f * 100.0)

    def _add_words(self, text: str) -> None:
        for w in _WORD_RE.findall(text):
            self.words.add(w.lower())

    def _absorb_news_numbers(self, text: str) -> None:
        """Collect verbatim figures from a news string (ADR-RPT4-05).

        Thousands-aware (so "13,600%" / "$25,000" stay whole, unlike the plain
        title scan). A figure carrying a whole-word money-SCALE unit records that
        scale class in ``news_scales`` — the only thing that may bypass the
        small-int money-guard. A bare integer, a percent, or a bare currency
        figure is recorded only as a plain ``news_number``, so it can never
        source a fabricated money magnitude of the same mantissa.
        """
        for m in _NEWS_NUMBER_RE.finditer(text or ""):
            _prefix, int_part, dec_part, unit = m.groups()
            # dec_part keeps its leading separator: ".3" (English) or ",3"
            # (German comma-decimal). Normalise to a dot before float parsing.
            frac = ("." + dec_part[1:]) if dec_part else ""
            try:
                value = float(int_part.replace(",", "") + frac)
            except (TypeError, ValueError):
                continue
            self.news_numbers.add(value)
            cls = _scale_class(unit)
            if cls is not None:
                self.news_scales.setdefault(value, set()).add(cls)

    def _absorb(self, value: Any) -> None:
        """Recursively collect numbers/words from a Fact value (no I/O)."""
        if value is None or isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            self._add_number(value)
            return
        if isinstance(value, str):
            self._add_words(value)
            for m in _STR_NUMBER_RE.findall(value):
                self._add_number(m)
            return
        if isinstance(value, Extreme):
            self._add_number(value.price)
            self.dates.add(value.on.isoformat())
            return
        if isinstance(value, NewsItem):
            desc = getattr(value, "description", "") or ""
            self._add_words(value.title + " " + desc + " " + value.publisher)
            # ADR-AUDIT-BAND-01b: keep the raw text for the band rule's provenance pass.
            self.news_text += " " + (value.title + " " + desc).lower()
            # ADR-RPT4-05: news figures go to the dedicated (thousands/magnitude
            # aware) news pool, not the computed-number pool.
            self._absorb_news_numbers(value.title + " " + desc)
            return
        if isinstance(value, (date, datetime)):
            self.dates.add(_date_part(value.isoformat()))
            return
        if isinstance(value, dict):
            for v in value.values():
                self._absorb(v)
            return
        if isinstance(value, (list, tuple)):
            for v in value:
                self._absorb(v)
            return

    # -- matchers -----------------------------------------------------------
    def match_number(
        self, value: float, unit: Optional[str], decimals: int = 0
    ) -> Tuple[bool, str, str]:
        """Return ``(matched, verdict, note)`` for a numeric claim.

        ``decimals`` is how many decimal places the rendered token carried; it
        drives the faithful display-rounding tolerance (ADR-RPT4-06).
        """
        money = unit in _MONEY_UNITS

        def _band(candidate: float) -> float:
            tol = max(_ABS_TOL, _REL_TOL * abs(candidate))
            # ADR-RPT4-06: faithful display rounding. A number shown with >= 1
            # decimals is a rounded view of a raw fact; for a *small* value the
            # 2 % band is tighter than one display ULP ("0.7 %" <- raw 0.72 is
            # 0.02 away, > 0.0144), so a faithful rounding false-rejects. Widen to
            # half a display ULP. Restricted to decimals >= 1 ON PURPOSE: an
            # integer band of 0.5 would let "5 Bio" round-match a 4.87 market cap
            # -> that fabrication guard must stay exact.
            if decimals >= 1:
                tol = max(tol, 0.5 * 10 ** (-decimals))
            return tol

        def _hit(candidate: float) -> bool:
            # A money magnitude can never be explained by a small structural int
            # (guards "5 Bio" from matching horizon_days=5 / a section number).
            if (
                money
                and float(candidate).is_integer()
                and 0 <= candidate <= _STRUCTURAL_MAX_INT
            ):
                return False
            return abs(value - candidate) <= _band(candidate)

        for c in self.numbers:
            if _hit(c):
                return True, VALID, f"matched FactSet value {c:g} (<=2%)"
        for c in self.derived:
            if _hit(c):
                return True, DERIVED, f"derived registry value {c:g} (P/E, P/S; <=2%)"
        # ADR-RPT4-05: a figure quoted verbatim in a PIT news headline is sourced.
        # A small-int money claim additionally requires the news to have carried a
        # MATCHING money-scale class (so a "$5"/"5 %"/"8 billionaires" mantissa can
        # never source a fabricated "5 Bio"); every other news figure matches on
        # value alone.
        claim_scale = _scale_class(unit)
        for c in self.news_numbers:
            if abs(value - c) > _band(c):
                continue
            small_money_int = (
                money and float(c).is_integer() and 0 <= c <= _STRUCTURAL_MAX_INT
            )
            if small_money_int and (
                claim_scale is None or claim_scale not in self.news_scales.get(c, ())
            ):
                continue  # guard intact: no matching money-scale figure in news
            return True, VALID, f"matched news-headline figure {c:g}"
        if not money:
            for e in _EDITORIAL_VALUES:
                if abs(value - e) <= max(_ABS_TOL, _REL_TOL * abs(e)):
                    return (
                        True,
                        EDITORIAL,
                        "labelled editorial calibration (ADR-RPT3-02)",
                    )
        return False, UNSOURCED, "no FactSet value / derived value within tolerance"


# ---------------------------------------------------------------------------
# Small deterministic helpers.
# ---------------------------------------------------------------------------
def _date_part(iso: str) -> str:
    """Return the ``YYYY-MM-DD`` head of an ISO timestamp."""
    return iso[:10]


def _scale_class(unit: Optional[str]) -> Optional[str]:
    """Map a unit token to its money-SCALE class (T/B/M/K), else ``None``.

    ``None`` for percent / bps / bare currency (``$``, ``USD``, ``EUR``) — those
    are magnitudes but not a money *scale*, so they never grant the small-int
    money-guard bypass (ADR-RPT4-05).
    """
    if not unit:
        return None
    return _SCALE_CLASSES.get(unit.lower())


def _parse_number(match: "re.Match[str]") -> Tuple[float, int, Optional[str], str]:
    """Return ``(value, decimals, unit, token)`` for a number regex match.

    ``decimals`` is the number of rendered decimal places (0 for an integer),
    used by the auditor's faithful display-rounding tolerance (ADR-RPT4-06).
    """
    prefix, int_part, dec_part, unit = match.groups()
    if prefix == "$":
        unit = unit or "$"
    sign = -1.0 if prefix == "-" else 1.0
    # dec_part carries its leading separator: ".45" (English) or ",3" (German
    # comma-decimal). Normalise the separator to a dot before float parsing.
    frac = ("." + dec_part[1:]) if dec_part else ""
    value = sign * float(int_part.replace(",", "") + frac)
    decimals = len(dec_part) - 1 if dec_part else 0  # ".45"/",35" -> 2, None -> 0
    return value, decimals, unit, match.group(0).strip()


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def audit(report_md: str, fact_set: FactSet) -> AuditResult:
    """Mechanically prove every claim of ``report_md`` against ``fact_set``.

    Parameters
    ----------
    report_md:
        The rendered report Markdown (typically from
        :func:`core.report.render.render`), but any string is accepted — the
        gate is a pure text-vs-FactSet check.
    fact_set:
        The frozen, PIT-filtered fact foundation the report was built from
        (:func:`core.report.fact_set.build_fact_set`).

    Returns
    -------
    AuditResult
        ``claims`` (every extracted token with its verdict), ``integrity_ok``
        (``True`` iff nothing struck), and ``flags`` (the struck subset). The
        same inputs always yield an identical result.
    """
    pool = _Pool(fact_set)
    claims: List[Claim] = []

    # -- 1. verify source URLs, then strip all provenance annotations --------
    for m in _URL_ANNOT_RE.finditer(report_md):
        url = m.group(1)
        ok = url in pool.urls
        claims.append(
            Claim(
                token=url,
                kind="url",
                matched=ok,
                verdict=VALID if ok else UNSOURCED,
                note="news item URL" if ok else "URL not in FactSet news set",
            )
        )
    # -- 2. verify [src: ...] provenance strings -----------------------------
    for m in _SRC_ANNOT_RE.finditer(report_md):
        src = m.group(1)
        ok = src in pool.sources
        claims.append(
            Claim(
                token=src,
                kind="source",
                matched=ok,
                verdict=VALID if ok else UNSOURCED,
                note="Fact.src provenance" if ok else "provenance not in FactSet",
            )
        )

    body = _URL_ANNOT_RE.sub(" ", report_md)
    body = _SRC_ANNOT_RE.sub(" ", body)
    body = _COMMENT_RE.sub(" ", body)

    # -- 3. dates ------------------------------------------------------------
    for m in _DATE_RE.finditer(body):
        tok = m.group(1)
        ok = tok in pool.dates
        claims.append(
            Claim(
                token=tok,
                kind="date",
                matched=ok,
                verdict=VALID if ok else UNSOURCED,
                note="PIT date" if ok else "date not in FactSet (as-of/news/filing)",
            )
        )
    body = _DATE_RE.sub(" ", body)

    # -- 3b. section-heading numbers (ADR-RPT4-07) ---------------------------
    # Scaffolding by POSITION, so it never depends on a numeric coincidence.
    # Consumed here so the general number pass below cannot re-read them.
    for m in _HEADING_NUM_RE.finditer(body):
        claims.append(
            Claim(
                token=m.group(2),
                kind="number",
                matched=True,
                verdict=STRUCTURAL,
                value=float(m.group(2)),
                unit=None,
                note="section heading number",
            )
        )
    body = _HEADING_NUM_RE.sub(lambda m: m.group(1), body)

    # -- 4. numbers ----------------------------------------------------------
    for m in _NUMBER_RE.finditer(body):
        value, decimals, unit, token = _parse_number(m)
        is_int = decimals == 0
        unit_bearing = unit is not None
        if not unit_bearing and is_int and 0 <= value <= _STRUCTURAL_MAX_INT:
            claims.append(
                Claim(token, "number", True, STRUCTURAL, value, unit, "small integer")
            )
            continue
        if not unit_bearing and is_int and _YEAR_LO <= value <= _YEAR_HI:
            claims.append(Claim(token, "number", True, STRUCTURAL, value, unit, "year"))
            continue
        ok, verdict, note = pool.match_number(value, unit, decimals)
        claims.append(Claim(token, "number", ok, verdict, value, unit, note))

    # -- 5. entities / tickers ----------------------------------------------
    for m in _CASHTAG_RE.finditer(body):
        tok = m.group(1)
        ok = tok.lower() in pool.words
        claims.append(
            Claim(
                token="$" + tok,
                kind="entity",
                matched=ok,
                verdict=VALID if ok else UNSOURCED_ENTITY,
                note="FactSet entity" if ok else "ticker not in FactSet",
            )
        )
    for m in _BARE_CAPS_RE.finditer(body):
        tok = m.group(1)
        if tok in _ENTITY_STOPWORDS:
            continue  # known non-ticker report vocabulary
        ok = tok.lower() in pool.words
        claims.append(
            Claim(
                token=tok,
                kind="entity",
                matched=ok,
                verdict=VALID if ok else UNSOURCED_ENTITY,
                note="FactSet entity" if ok else "entity not in FactSet",
            )
        )

    # -- ADR-RPT4-08: a RECOMMENDATION is a claim too -----------------------
    #
    # Everything above proves that numbers PRESENT in the prose trace to the FactSet.
    # That is structurally blind to the most dangerous failure this report can have:
    # a recommendation with nothing behind it. "HALTEN" is not a number, so no rule
    # above can see it, and a report with no price, no rank, no fundamentals and no
    # news passed with ZERO flags while printing "HALTEN — neutral / abwarten" and
    # "Wie sicher: Mittel" — indistinguishable, to a reader, from a real call.
    #
    # That is not hypothetical: the panel's only writer is LSTMDynamic and
    # ACTIVE_STRATEGY defaults to RLAgent, so rank-missing is the DEFAULT state of the
    # shipped build. A full-universe run would have produced ~500 confident-looking
    # notes built on nothing — and the release gate (generate_report.py, exit 0 iff
    # integrity_ok) would have waved every one of them through.
    #
    # The rule: the cross-sectional rank is the ONLY validated basis for the call
    # (render._rec_parts derives the verb from it and nothing else). So a verb without
    # a rank is, by construction, fabricated. This is what lets a 500-report run be
    # gated on the audit at all.
    verb = _RECOMMENDATION_VERB_RE.search(report_md)
    if verb is not None:
        has_rank = getattr(fact_set.lstm_xsec_rank, "value", None) is not None
        claims.append(
            Claim(
                token=verb.group(1),
                kind="recommendation",
                matched=has_rank,
                verdict=VALID if has_rank else UNSOURCED,
                note=(
                    "recommendation backed by a cross-sectional rank"
                    if has_rank
                    else "RECOMMENDATION WITHOUT A RANK — the call has no validated "
                    "basis in the FactSet and must not be made"
                ),
            )
        )

    # -- ADR-AUDIT-BAND-01: a RANK-BAND claim is a claim too ------------------
    #
    # Mirrors the ADR-RPT4-08 shape above: hold a qualitative assertion to the ONE
    # validated field. See the constants block for the LYB defect this closes.
    #
    # The scan runs on its OWN text derived from report_md — NOT on ``body``. body
    # has already had its heading numbers substituted out ("## 7." -> "## "), which
    # would defeat the scenarios carve-out silently.
    band_text = _URL_ANNOT_RE.sub(" ", report_md)
    band_text = _SRC_ANNOT_RE.sub(" ", band_text)
    band_text = _COMMENT_RE.sub(" ", band_text)
    band_text = _SCENARIOS_BLOCK_RE.sub(" ", band_text)

    # ``audit()`` must never raise — it is the release gate, and a raise is an abort,
    # not an exit-1. Every other rule here is total (match_number try/excepts,
    # ADR-RPT4-08 uses ``is not None``); this one must not be the exception that
    # trusts a FactSet field's runtime type. A non-numeric percentile is a malformed
    # FactSet: treat it as "no usable rank" (fail-closed) rather than crash.
    _raw_pct = getattr(fact_set.lstm_xsec_percentile, "value", None)
    try:
        band_pct = None if _raw_pct is None else float(_raw_pct)
    except (TypeError, ValueError):
        band_pct = None

    for m in _BAND_CLAIM_RE.finditer(band_text):
        lo, hi, human = _BANDS[m.lastgroup]
        token = m.group(0)

        # ADR-AUDIT-BAND-01b: "starke Position" only asserts a RANK when the context
        # says so; unqualified it is a moat statement (see _RANK_CONTEXT_RE).
        if m.lastgroup in _CONTEXT_REQUIRED and not _RANK_CONTEXT_RE.search(
            _sentence_around(band_text, m.start(), m.end())
        ):
            continue

        # ADR-AUDIT-BAND-01b: quoted from a PIT headline -> the publisher's claim,
        # carrying its own provenance. Same doctrine as ADR-RPT4-05 for numbers.
        if token.lower() in pool.news_text:
            claims.append(
                Claim(
                    token=token,
                    kind="band",
                    matched=True,
                    verdict=VALID,
                    value=band_pct,
                    unit=None,
                    note="band phrase quoted verbatim from a PIT news headline",
                )
            )
            continue

        if band_pct is None:
            # FAIL-CLOSED, deliberately — and this is NOT "inventing a defect".
            #
            # The tempting reading is "no percentile -> cannot check -> fail open".
            # That would be right if the datum were merely missing for the CHECK.
            # It is not: cross_section_standing returns (None, None, None) JOINTLY
            # (lstm_panel_store.py:193), so percentile-None means the symbol has no
            # cross-sectional standing AT ALL. The prose then asserts a ranking that
            # does not exist — a fabrication we can prove, not a claim we cannot
            # judge. Fail-open here would wave through exactly the report that has
            # the least behind it.
            #
            # This is our own precedent, twice over: ADR-RPT4-08 strikes a
            # recommendation verb without a rank, and render._rec_parts returns
            # (None, None, None) rather than a plausible HALTEN. The renderer emits
            # no band word at all in the no-rank state (pinned by
            # test_renderer_has_no_band_words_when_there_is_no_rank), so this can
            # only ever fire on narrator prose.
            claims.append(
                Claim(
                    token=token,
                    kind="band",
                    matched=False,
                    verdict=CONTRADICTED,
                    value=None,
                    unit=None,
                    note=(
                        f"claims {human} but the FactSet has no rank at all — the "
                        "asserted standing does not exist"
                    ),
                )
            )
            continue
        ok = (lo is None or band_pct >= lo) and (hi is None or band_pct <= hi)
        claims.append(
            Claim(
                token=token,
                kind="band",
                matched=ok,
                verdict=VALID if ok else CONTRADICTED,
                value=band_pct,
                unit=None,
                note=(
                    f"{human} is consistent with the {band_pct:.1f}th percentile"
                    if ok
                    else f"CONTRADICTS THE RANK — claims {human}, but the symbol "
                    f"sits at the {band_pct:.1f}th percentile of its universe"
                ),
            )
        )

    flags = tuple(c for c in claims if not c.matched)
    return AuditResult(
        claims=tuple(claims),
        integrity_ok=(len(flags) == 0),
        flags=flags,
    )


# ---------------------------------------------------------------------------
# RPT-4b seam — documented hook, deliberately NOT implemented here.
# ---------------------------------------------------------------------------
def semantic_attribution_hook(
    report_md: str,
    fact_set: FactSet,
    *,
    get_llm: Optional[Callable[..., Any]] = None,
) -> None:
    """RPT-4b HOOK — the semantic-attribution judge (NOT built in this task).

    The mechanical :func:`audit` proves that every *number/entity/date/URL* is
    real. It cannot judge whether a real fact is wired to the right subject —
    e.g. "Burry schaetzte die $5-Bio-Marktkap" carries a number that IS in a
    source, so it passes the mechanical gate, yet the *attribution* may be
    unfaithful. Catching that requires reading meaning, i.e. an LLM judge.

    This function documents that seam and is intentionally inert so the
    deterministic core stays **LLM-free** (BORA): it imports no model, calls no
    network, and — with no ``get_llm`` provided — simply returns ``None`` (the
    "not run" sentinel). RPT-4b will implement the judge behind the injected
    ``get_llm`` factory (mirroring the RPT-5 narrator seam) and return a
    structured semantic verdict; until then this MUST NOT be called from the
    deterministic path.
    """
    # No LLM in the deterministic core. RPT-4b wires get_llm() here.
    return None


__all__ = [
    "VALID",
    "DERIVED",
    "STRUCTURAL",
    "EDITORIAL",
    "UNSOURCED",
    "UNSOURCED_ENTITY",
    "CONTRADICTED",
    "Claim",
    "AuditResult",
    "audit",
    "semantic_attribution_hook",
]
