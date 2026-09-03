# core/specialist/grounding.py
# Rich-synthesis card (Direction A) — mechanical headline-grounding gate.
"""Deterministic, LLM-free grounding of the V2 synthesis prose.

The rich synthesis card (bull / bear / thesis prose) is produced by the same
free-form LLM path that fabricated cross-company entities in the past
(#2202: "Cerebras" injected onto GOOGL/AAPL/Kroger). The mechanical auditor
(``core/report/auditor.py``) only checks *numeric* provenance and is blind to a
fabricated *sentence* — so this module is the ONLY mechanical brake on the prose.

``verify_grounding`` re-derives, per sentence, whether every claim is anchored to
a real ``news_item`` of THIS symbol. It builds a corpus from *exactly* the strings
that were fed into the prompt (mirroring ``prompt.py::_data_sections``) and:

  A) extracts capitalized named entities from each sentence,
  B) HARD ENTITY GATE — every non-generic, non-subject entity must appear in the
     corpus (the #2202 catcher); a single unknown token drops the sentence,
  C) NUMERIC GATE — every price / percent / money figure must appear in the
     corpus (fundamentals are NOT in the synthesis prompt, so no exemption),
  D) POSITIVE BINDING — the sentence is bound to a real headline (the model's
     ``[H#]`` tag is *verified*, never trusted; a post-hoc best-match is used when
     the tag is missing/invalid). No binding → the sentence is dropped.

Only surviving sentences are displayed, each with its bound headline. The
function is pure: it reads its inputs and returns a value; no I/O, no LLM, no
shared state. ``company_tokens`` (the subject company's own name tokens) is
supplied by the caller from ``data/company_cache/<sym>.json``; when absent it is
empty and company-name sentences are (harmlessly) over-dropped.

PRECISION BIAS: a false *drop* is harmless (an honest empty line); a false *pass*
is dangerous (a fabricated claim reaches a paying customer). The generic-stoplist
is therefore kept deliberately tight — when in doubt, drop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class GroundedSynthesis:
    """Result of grounding the three prose fields.

    ``bull`` / ``bear`` / ``thesis`` are the FILTERED display strings (only
    surviving sentences, ``[H#]`` tags stripped; an honest placeholder when
    nothing survives). ``citations`` binds every displayed claim to its headline;
    ``dropped`` records every rejected claim with a reason. ``grounding_ok`` is
    True only when nothing was dropped (every displayed claim is source-bound).
    """

    bull: str
    bear: str
    thesis: str
    citations: List[Dict[str, Any]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    grounding_ok: bool = True


# #2249 FIX 3 — AMBIGUOUS TICKERS. A bare ticker token is a fine subject anchor for
# a DISTINCTIVE symbol (NVDA/GOOGL/AAPL never appear off-topic), but for a symbol
# that is ALSO an everyday English word or a well-known NON-issuer entity, a bare-
# ticker news fetch reliably drags OFF-SUBJECT stories that carry the ticker token
# yet nothing to do with the company. The live #2249 leak: ticker "DIA" (SPDR Dow
# ETF) pulled the REAL headline "Dia's Jessica Morgan Named Director of Tate" (Dia
# ART FOUNDATION) — it carries "dia", so the symbol-token corpus filter kept it and
# the fabricated Tate sentence shipped. For these tickers, WHEN the subject company's
# own name tokens are known, ``verify_grounding`` requires a headline to carry a
# COMPANY-NAME token (not merely the bare ticker) to enter the corpus / [H#] pool.
# Distinctive tickers keep the symbol|company UNION so a legitimate ticker-only
# headline ("SNDK, MU, AMD, NVDA ...") still survives; an empty company cache is a
# no-op (falls back to the union) so we never drop everything. PRECISION BIAS: a
# false drop on an ambiguous ticker is an honest empty line; a false pass ships a
# hallucinated entity to a paying customer.

# Issuer name tokens for the curated card universe (base.py _CARD_REGISTRY_STOCKS).
# Runtime fallback for the unpopulated desktop company_cache: guarantees the
# subject's own name is never entity-gated as foreign, and gives ambiguous tickers
# their company-name filter. Lowercase single tokens, matched via _tokenize.
_COMPANY_NAME_FALLBACK: Dict[str, Set[str]] = {
    "AAPL": {"apple"},
    "MSFT": {"microsoft"},
    "GOOGL": {"google", "alphabet"},
    "AMZN": {"amazon"},
    "NVDA": {"nvidia"},
    "META": {"meta", "facebook", "instagram"},
    "TSLA": {"tesla"},
    "NFLX": {"netflix"},
    "AMD": {"amd"},
    "INTC": {"intel"},
    "CSCO": {"cisco"},
    "ORCL": {"oracle"},
    "ADBE": {"adobe"},
    "QCOM": {"qualcomm"},
    "AVGO": {"broadcom"},
    "TXN": {"texas", "instruments"},
    "JPM": {"jpmorgan", "morgan", "chase"},
    "WFC": {"wells", "fargo"},
    "JNJ": {"johnson"},
    "MRK": {"merck"},
    "PFE": {"pfizer"},
    "UNH": {"unitedhealth", "unitedhealthcare"},
    "WMT": {"walmart"},
    "COST": {"costco"},
    "MCD": {"mcdonald", "mcdonalds"},
    "NKE": {"nike"},
    "SBUX": {"starbucks"},
    "PEP": {"pepsico", "pepsi"},
    "XOM": {"exxon", "exxonmobil", "mobil"},
    "CVX": {"chevron"},
    "UBER": {"uber"},
    "ABNB": {"airbnb"},
    "PYPL": {"paypal"},
}

_AMBIGUOUS_TICKERS: Set[str] = {
    # name / acronym collisions (bare ticker is also a well-known non-issuer entity)
    "dia",  # SPDR Dow ETF — BUT ALSO Dia Art Foundation / Defense Intelligence Agency
    "mmm",  # 3M — but "mmm" is also an interjection
    # Stracke-persona live finds 2026-07-23: bare-ticker news search dragged an
    # obituary for a deacon nicknamed "Pep" onto PEP, Pentagon cost coverage onto
    # COST, a Marines workshop onto MCD, and University of New Hampshire onto UNH.
    # All four have company-name fallbacks, so the company-token corpus filter
    # engages and those stories can neither enter the corpus nor bind.
    "pep",
    "cost",
    "mcd",
    "unh",
    # everyday-English-word tickers (the bare symbol is a common word)
    "a",
    "all",
    "key",
    "on",
    "it",
    "so",
    "are",
    "for",
    "out",
    "now",
    "one",
    "two",
    "cat",
    "see",
    "run",
    "big",
    "has",
    "gold",
    "real",
    "well",
    "fun",
    "love",
    "play",
    "work",
    "life",
    "safe",
    "job",
    "car",
    "up",
    "by",
    "go",
    "we",
}


def _ticker_is_ambiguous(symbol_tokens: Set[str]) -> bool:
    """True if the bare ticker collides with a common word / non-issuer entity, so
    it must NOT bind an off-subject headline on its ticker token alone (#2249)."""
    return bool(symbol_tokens & _AMBIGUOUS_TICKERS)


# Honest placeholders — shown when a field has no surviving grounded sentence.
# NEVER invent prose; say plainly that nothing was provable this cycle.
_PLACEHOLDER = {
    "bull": (
        "No supported bull case from this news cycle yet — "
        "updated with the next research round."
    ),
    "bear": (
        "No supported bear case from this news cycle yet — "
        "updated with the next research round."
    ),
    "thesis": (
        "No supported thesis from this news cycle yet — "
        "updated with the next research round."
    ),
}

# Generic capitalized words that are NOT entities: sentence-initials, common
# finance vocabulary, calendar words. Kept tight on purpose (precision bias).
_GENERIC_CAPS: Set[str] = {
    # articles / pronouns / conjunctions / prepositions (sentence initials)
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "there",
    "it",
    "its",
    "they",
    "their",
    "we",
    "our",
    "he",
    "she",
    "his",
    "her",
    "but",
    "and",
    "or",
    "if",
    "as",
    "with",
    "for",
    "in",
    "on",
    "at",
    "to",
    "of",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "has",
    "have",
    "had",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "no",
    "not",
    "while",
    "however",
    "though",
    "although",
    "despite",
    "given",
    "based",
    "amid",
    "over",
    "under",
    "into",
    "from",
    "by",
    "up",
    "down",
    "out",
    "off",
    "than",
    "then",
    "so",
    "such",
    "both",
    "either",
    "neither",
    "more",
    "most",
    "less",
    "least",
    "very",
    "strong",
    "strongest",
    "weak",
    "weakest",
    "high",
    "higher",
    "highest",
    "low",
    "lower",
    "lowest",
    "new",
    "recent",
    "recently",
    "continued",
    "raised",
    "rising",
    "growing",
    "increasing",
    "increased",
    "declining",
    "positive",
    "negative",
    "overall",
    "key",
    "major",
    "significant",
    "potential",
    "possible",
    "likely",
    "reliance",
    "concerns",
    "concern",
    # generic finance vocabulary
    "revenue",
    "revenues",
    "growth",
    "earnings",
    "sales",
    "margin",
    "margins",
    "profit",
    "profits",
    "loss",
    "losses",
    "price",
    "target",
    "stock",
    "shares",
    "share",
    "market",
    "markets",
    "data",
    "analyst",
    "analysts",
    "investor",
    "investors",
    "confidence",
    "demand",
    "supply",
    "guidance",
    "outlook",
    "quarter",
    "quarterly",
    "annual",
    "fiscal",
    "year",
    "years",
    "bull",
    "bear",
    "case",
    "thesis",
    "company",
    "business",
    "sector",
    "industry",
    "dividend",
    "buyback",
    "valuation",
    "forecast",
    "estimate",
    "estimates",
    "results",
    "report",
    "reports",
    "filing",
    "filings",
    "insider",
    "insiders",
    "congressional",
    "activist",
    "sentiment",
    "volatility",
    "risk",
    "risks",
    "trend",
    "trends",
    "signal",
    "signals",
    "product",
    "products",
    "service",
    "services",
    "technology",
    "tech",
    "artificial",
    "intelligence",
    "cloud",
    "chip",
    "chips",
    "semiconductor",
    "semiconductors",
    "gross",
    "net",
    "operating",
    "cash",
    "flow",
    "debt",
    "equity",
    "capital",
    "expansion",
    "adoption",
    "competition",
    "competitive",
    "trading",
    "trade",
    "trades",
    "buy",
    "sell",
    "hold",
    "long",
    "short",
    "term",
    "near",
    "mid",
    "upside",
    "downside",
    "momentum",
    "catalyst",
    "catalysts",
    "headwind",
    "headwinds",
    "tailwind",
    "tailwinds",
    # acronyms that recur generically
    "ai",
    "eps",
    "pe",
    "peg",
    "gdp",
    "ceo",
    "cfo",
    "coo",
    "cto",
    "us",
    "usa",
    "eu",
    "uk",
    "sec",
    "fed",
    "gaap",
    "yoy",
    "ttm",
    "fy",
    "q1",
    "q2",
    "q3",
    "q4",
    "etf",
    "ipo",
    "roi",
    "roe",
    "ebitda",
    # months / days
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
    "jan",
    "feb",
    "mar",
    "apr",
    "jun",
    "jul",
    "aug",
    "sep",
    "sept",
    "oct",
    "nov",
    "dec",
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}

# GRAMMATICAL-only stopwords for the content-token overlap of positive binding
# (D). Deliberately NOT the finance vocabulary of ``_GENERIC_CAPS``: for BINDING,
# "price target" / "chip demand" / "revenue guidance" ARE the content that ties a
# sentence to its headline. Only articles / prepositions / pronouns / auxiliaries
# (which carry no topical signal) are stripped — otherwise a legitimate,
# finance-heavy sentence would have nothing left to bind and be over-dropped.
_STOPWORDS: Set[str] = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "these",
    "those",
    "it",
    "its",
    "they",
    "their",
    "them",
    "we",
    "our",
    "us",
    "he",
    "she",
    "his",
    "her",
    "i",
    "you",
    "your",
    "my",
    "me",
    "but",
    "and",
    "or",
    "nor",
    "if",
    "as",
    "with",
    "for",
    "in",
    "on",
    "at",
    "to",
    "of",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "has",
    "have",
    "had",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "no",
    "not",
    "while",
    "however",
    "though",
    "although",
    "despite",
    "given",
    "amid",
    "over",
    "under",
    "into",
    "from",
    "by",
    "up",
    "down",
    "out",
    "off",
    "than",
    "then",
    "so",
    "such",
    "both",
    "either",
    "neither",
    "about",
    "after",
    "again",
    "all",
    "also",
    "any",
    "because",
    "before",
    "between",
    "during",
    "each",
    "few",
    "further",
    "here",
    "how",
    "just",
    "now",
    "only",
    "other",
    "own",
    "same",
    "some",
    "still",
    "there",
    "through",
    "too",
    "until",
    "what",
    "when",
    "where",
    "which",
    "who",
    "whom",
    "why",
    "at",
    "per",
    "via",
    "amongst",
    "among",
    "toward",
    "towards",
    "its",
    "it",
    "on",
    "continued",
    "continues",
    "remains",
    "remain",
}

# B1 — GENERIC BINDING STOPLIST. Words that, alone, must NOT tie a clause to its
# headline (the too-lax pre-fix binding let a real core drag a generic floskel —
# "…making it a leader in the field", "…as a stable dividend-paying stock" — past
# the gate on a single shared generic word). These are editorializing / generic
# finance nouns+adjectives: a clause binds only via a NON-generic content token
# OR via >=2 shared tokens total (see ``_bind_headline``). This list is
# deliberately SEPARATE from the total-overlap count — a generic word still counts
# toward the 2-token safety net (so "revenue guidance" ~ a revenue-guidance
# headline still binds), it just cannot bind *on its own*. Proper nouns and the
# specific topical tokens of a real thesis (buybacks, ransomware, Burry, massive,
# Blackwell, …) are intentionally NOT here.
_BIND_GENERIC: Set[str] = {
    "stock",
    "stocks",
    "share",
    "shares",
    "company",
    "companies",
    "business",
    "corporation",
    "firm",
    "strong",
    "strength",
    "weak",
    "growth",
    "grow",
    "growing",
    "potential",
    "potentially",
    "revenue",
    "revenues",
    "leader",
    "leaders",
    "leadership",
    "market",
    "markets",
    "dominance",
    "dominant",
    "stable",
    "stability",
    "dividend",
    "dividends",
    "brand",
    "brands",
    "value",
    "valuable",
    "values",
    "performance",
    "performing",
    "financial",
    "financials",
    "presence",
    "global",
    "consistent",
    "consistency",
    "source",
    "income",
    "steady",
    "prospect",
    "prospects",
    "future",
    "success",
    "successful",
    "skills",
    "investment",
    "investments",
    "profit",
    "profits",
    "profitable",
    "sales",
    "earnings",
    "guidance",
    "outlook",
    "position",
    "opportunity",
    "opportunities",
    "rebound",
    "recovery",
    "upside",
    "downside",
    "diversified",
    "diversification",
    "stream",
    "streams",
    "significant",
    "technology",
    "tech",
    "giant",
    "momentum",
    "quality",
    "sentiment",
    "confidence",
    "volatility",
    "operations",
    "operational",
    "portfolio",
    "holdings",
    "capital",
    "returns",
}

# B1 — clause boundaries. A sentence is split into clauses so an UNGROUNDED
# trailing clause falls on its own while the grounded core stays. Delimiters:
# punctuation (comma / semicolon / colon / dash) AND a curated set of connective
# "pivot" words that grammatically introduce a trailing dependent / editorial
# clause (participles + "due to" / "which"). The marker is CONSUMED at the split
# (like punctuation), so a surviving clause does not carry the pivot word.
_CLAUSE_SPLIT_RE = re.compile(
    r"\s*[;:,]\s*"
    r"|\s*[—–]\s*"
    r"|\s+-\s+"
    r"|\s+(?:due\s+to|making|indicating|suggesting|suggests|suggest|"
    r"reflecting|showing|demonstrating|highlighting|signaling|signalling|"
    r"underscoring|reinforcing|cementing|positioning|providing|leading|"
    r"which)\s+",
    re.IGNORECASE,
)

# B1b — leading connective words stripped from a surviving clause for display
# (split artifacts like a leading "as" / "due" / "which"). Articles ("the"/"a")
# are NOT stripped — only topic-free connectors — so real content is preserved.
_LEADING_STRIP: Set[str] = {
    "as",
    "and",
    "but",
    "or",
    "nor",
    "so",
    "yet",
    "because",
    "since",
    "while",
    "although",
    "though",
    "whereas",
    "given",
    "thereby",
    "thus",
    "which",
    "making",
    "indicating",
    "suggesting",
    "reflecting",
    "showing",
    "demonstrating",
    "highlighting",
    "providing",
    "leading",
    "due",
    "to",
}

# A capitalized named-entity phrase: one or more Capitalized tokens in a row.
_ENTITY_RE = re.compile(r"\b[A-Z][A-Za-z0-9.&\-]*(?:\s+[A-Z][A-Za-z0-9.&\-]*)*\b")

# Any numeric figure (optionally $-prefixed / %-suffixed, with , or . separators).
_NUM_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")

# The model's positive-binding tag, e.g. "[H3]".
_HTAG_RE = re.compile(r"\[H(\d+)\]", re.IGNORECASE)


def _tokenize(text: str) -> List[str]:
    """Lowercase alnum tokens (split on any non-[a-z0-9])."""
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


# B1c — CLITIC / POSSESSIVE FRAGMENTS. ``_tokenize("company's")`` -> ["company",
# "s"]; the bare "s" is NOT a stopword and NOT generic, so pre-fix it counted as a
# NON-generic content token and a single shared "s" (nearly EVERY real headline
# carries "Here's" / "world's" / "Berkshire's" / "Page's" -> also emits "s") tied
# an ungrounded editorial floskel to a thematically foreign headline (a FALSE PASS
# under a real-looking source — the exact "ungebundene Floskel"-class B1 closes,
# only via a different token). ``_bindable`` therefore bars any binding token that
# is a possessive/contraction fragment: length < 2 (kills "s"/"t"/"d"/"m") OR one
# of the two-char contraction tails ("ll"/"re"/"ve" from we'll/they're/we've). A
# real content core binds on MULTI-char tokens (stack, massive, buybacks,
# ransomware, apple, burry) and the generic-but-on-topic 2-token net ("revenue
# guidance") is untouched, so this filter cannot over-drop.
_CLITIC_FRAGMENTS: Set[str] = {"ll", "re", "ve"}


def _bindable(token: str) -> bool:
    """True if ``token`` may participate in positive-binding overlap (B1c)."""
    return len(token) >= 2 and token not in _CLITIC_FRAGMENTS


def _norm_number(raw: str) -> Optional[str]:
    """Normalise a numeric match to a comparable core (strip $ % and thousands
    commas). Returns None if there is no digit left."""
    core = raw.replace("$", "").replace("%", "").replace(",", "").strip()
    return core if re.search(r"\d", core) else None


def _split_sentences(text: str) -> List[str]:
    """Split prose into sentences on ``.!?`` boundaries (tags kept in place)."""
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _clean_ws(text: str) -> str:
    """Collapse whitespace and remove any space BEFORE punctuation (B1b: the
    ``[H#]`` tag left a stray ' .' / ' ,' when it was cut out)."""
    t = re.sub(r"\s+", " ", (text or "")).strip()
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return t


def _split_clauses(text: str) -> List[str]:
    """Split a sentence into clauses on punctuation / connective pivots (B1)."""
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(text or "") if c and c.strip()]


def _finalize_display(clause: str) -> str:
    """Render one surviving sentence: capitalize, ensure terminal punctuation,
    normalise whitespace (B1b). NOTE (2026-07-23): the clause-era leading-
    connector strip is GONE — display is sentence-level (all-or-nothing), and
    the strip mangled real sentences ("As a result, ..." -> "A result, ...")."""
    t = _clean_ws(clause)
    if not t:
        return t
    if t[0].islower():
        t = t[0].upper() + t[1:]
    if t[-1] not in ".!?":
        t = t + "."
    return t


def _build_corpus(
    symbol: str, gathered: Dict[str, Any], subject_tokens: Set[str]
) -> Tuple[Set[str], Set[str], Set[str], List[str]]:
    """Build (corpus_tokens, corpus_bigrams, corpus_numbers, headlines).

    The corpus is assembled from EXACTLY the strings that ``prompt.py``
    (``_data_sections``) puts in front of the model — same sources, same caps —
    so a claim is grounded iff it traces to something the model actually saw.
    ``headlines`` is the ``recent_headlines[:6]`` list (the ``[H#]`` targets).

    #2249 FIX 2: a bare-ticker news fetch can pull an OFF-SUBJECT headline into the
    pool (e.g. a "Tate Museums" headline for ticker "DIA"). ``subject_tokens`` (the
    subject symbol|company name tokens) filters the headlines to subject-bearing
    ones BEFORE the corpus AND the [H#] binding targets are built — so an off-subject
    entity can neither pass the entity gate nor find a headline to bind to.
    """
    strings: List[str] = []

    headlines_raw = gathered.get("recent_headlines", []) or []
    headlines: List[str] = [str(h) for h in headlines_raw[:6]]
    if subject_tokens:
        headlines = [h for h in headlines if set(_tokenize(h)) & subject_tokens]
    # Direction A: the system's own model-signal lines are citable sources in the
    # SAME [H#] sequence (prompt.py numbers them after the news headlines). They
    # always carry the subject symbol, so they survive the subject filter by
    # construction — appended after it to keep the news filter semantics intact.
    headlines.extend(str(m) for m in (gathered.get("model_signal_lines", []) or []))
    strings.extend(headlines)

    for t in (gathered.get("insider_trades", []) or [])[:5]:
        strings.append(f"{t.get('filer', '')} {t.get('form', '')}")
    for e in (gathered.get("material_events", []) or [])[:3]:
        strings.append(str(e.get("entity", "")))
    for a in (gathered.get("activist_stakes", []) or [])[:3]:
        strings.append(f"{a.get('filer', '')} {a.get('form', '')}")
    for p in (gathered.get("political_trades", []) or [])[:3]:
        strings.append(
            f"{p.get('politician', '')} {p.get('transaction', '')} "
            f"{p.get('amount', '')}"
        )
    strings.append(str(gathered.get("reddit_sentiment", "")))

    corpus_tokens: Set[str] = set()
    corpus_bigrams: Set[str] = set()
    corpus_numbers: Set[str] = set()
    for s in strings:
        toks = _tokenize(s)
        corpus_tokens.update(toks)
        for i in range(len(toks) - 1):
            corpus_bigrams.add(f"{toks[i]} {toks[i + 1]}")
        for m in _NUM_RE.finditer(s):
            n = _norm_number(m.group())
            if n:
                corpus_numbers.add(n)

    # The subject symbol itself is always "known".
    corpus_tokens.update(_tokenize(symbol))
    return corpus_tokens, corpus_bigrams, corpus_numbers, headlines


def _entity_ok(
    phrase: str,
    corpus_tokens: Set[str],
    corpus_bigrams: Set[str],
    symbol_tokens: Set[str],
    company_tokens: Set[str],
) -> Tuple[bool, str]:
    """HARD ENTITY GATE (B). Returns (ok, offending_phrase).

    Strip subject-symbol / company / generic tokens from the phrase; whatever
    remains is a claimed foreign entity and EVERY remaining token must be in the
    corpus (a multi-word remainder must also appear as a corpus bigram). A single
    unknown token → the sentence is ungrounded.
    """
    # Only CAPITALIZED word-parts are entity evidence. A hyphen compound like
    # "AI-driven" contributes "ai" (an acronym, generic) but NOT "driven" — the
    # lowercase part is ordinary prose, not a name (live false drop 2026-07-23:
    # "entity 'The AI-driven' not in any source"). Real names ("Gene Munster",
    # "Jessica Morgan") stay fully gated: every part is capitalized.
    entityish: Set[str] = set()
    for word in phrase.split():
        for part in word.split("-"):
            if part and part[0].isupper():
                entityish.update(_tokenize(part))
    tokens = [t for t in _tokenize(phrase) if t in entityish]
    remaining = [
        t
        for t in tokens
        if t not in symbol_tokens and t not in company_tokens and t not in _GENERIC_CAPS
    ]
    if not remaining:
        return True, ""
    for t in remaining:
        if t not in corpus_tokens:
            return False, phrase
    # Multi-word remainder must appear contiguously (kills scattered-token luck).
    for i in range(len(remaining) - 1):
        if f"{remaining[i]} {remaining[i + 1]}" not in corpus_bigrams:
            return False, phrase
    return True, ""


def _numbers_ok(sentence: str, corpus_numbers: Set[str]) -> Tuple[bool, str]:
    """NUMERIC GATE (C). Every financial figure must appear in the corpus.

    A bare calendar year (1900-2099) and a small bare integer (<100, e.g. a
    filing count) are not financial figures and are exempt; every $-prefixed,
    %-suffixed, or decimal figure is checked strictly.
    """
    for m in _NUM_RE.finditer(sentence):
        raw = m.group()
        core = _norm_number(raw)
        if core is None:
            continue
        is_financial = ("$" in raw) or ("%" in raw) or ("." in core)
        if not is_financial:
            try:
                ival = int(core)
            except ValueError:
                continue
            if 1900 <= ival <= 2099 or ival < 100:
                continue  # calendar year / small count — not a financial figure
        if core not in corpus_numbers:
            return False, raw
    return True, ""


def _bind_headline(
    clause: str,
    claimed_idx: Optional[int],
    headlines: List[str],
    extra_stop: Set[str],
) -> Optional[Tuple[int, str]]:
    """POSITIVE BINDING (D). Returns (1-based index, headline_text) or None.

    B1 — the binding is STRICTER than a single shared token: a clause binds to a
    headline only when it shares >=1 NON-generic content token with it, OR shares
    >=2 tokens total. A lone generic word ("stock", "market", "strong") therefore
    can no longer tie an editorial floskel to a headline, while a genuinely
    on-topic clause whose words are all generic finance vocabulary ("revenue
    guidance") still binds via the 2-token safety net. The model's claimed
    ``[H#]`` is verified against the SAME threshold — never trusted on its own —
    and a missing/invalid tag falls back to the best-binding headline.
    ``extra_stop`` (the subject symbol + company tokens) is excluded from every
    overlap so the ubiquitous company name cannot trivially "bind".
    """
    if not headlines:
        return None
    stop = _STOPWORDS | extra_stop
    stop_ng = stop | _BIND_GENERIC
    toks = _tokenize(clause)
    # B1c — possessive/contraction fragments ("s"/"ll"/"re"/"ve") must never carry
    # binding: a lone "s" (from "company's") would otherwise tie a foreign floskel
    # to any headline that also carries a possessive.
    total_tokens = {t for t in toks if t not in stop and _bindable(t)}
    ng_tokens = {t for t in toks if t not in stop_ng and _bindable(t)}
    if not total_tokens:
        return None

    def _score(h: str) -> Tuple[int, int]:
        """(non_generic_overlap, total_overlap) — the binding strength."""
        h_toks = _tokenize(h)
        h_total = {t for t in h_toks if t not in stop and _bindable(t)}
        h_ng = {t for t in h_toks if t not in stop_ng and _bindable(t)}
        return len(ng_tokens & h_ng), len(total_tokens & h_total)

    def _binds(score: Tuple[int, int]) -> bool:
        ng, total = score
        return ng >= 1 or total >= 2

    if claimed_idx is not None and 1 <= claimed_idx <= len(headlines):
        h = headlines[claimed_idx - 1]
        if _binds(_score(h)):
            return claimed_idx, h

    best_idx, best_score = 0, (0, 0)
    for i, h in enumerate(headlines):
        score = _score(h)
        if _binds(score) and score > best_score:
            best_idx, best_score = i + 1, score
    if best_idx:
        return best_idx, headlines[best_idx - 1]
    return None


def _ground_field(
    kind: str,
    text: str,
    corpus_tokens: Set[str],
    corpus_bigrams: Set[str],
    corpus_numbers: Set[str],
    headlines: List[str],
    symbol_tokens: Set[str],
    company_tokens: Set[str],
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Ground one prose field. Returns (display_text, citations, dropped)."""
    citations: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    stripped = (text or "").strip()
    # A bare "NONE" (the prompt's own "no headline supports this" sentinel) or an
    # empty field is an honest empty, NOT a drop.
    if not stripped or stripped.upper().strip(".") == "NONE":
        return _PLACEHOLDER[kind], citations, dropped

    survivors: List[str] = []
    for raw_sentence in _split_sentences(stripped):
        claimed = _HTAG_RE.search(raw_sentence)
        claimed_idx = int(claimed.group(1)) if claimed else None
        sent_notag = _HTAG_RE.sub("", raw_sentence)
        # NONE-equivalent prose: the model sometimes writes the sentinel out in
        # words ("There is no clear bear case supported by the headlines.")
        # instead of NONE. Map it to the honest empty (the retention-phrased
        # placeholder) rather than displaying a sentence that reads like a
        # system failure (live feedback 2026-07-23).
        if re.search(
            r"\bno\s+(?:clear\s+|explicit\s+|strong\s+)?"
            r"(?:bull|bear)\s+case\b|\bno\s+(?:clear\s+|explicit\s+|strong\s+)?"
            r"(?:investment\s+)?thesis\b",
            sent_notag,
            re.IGNORECASE,
        ):
            continue
        # B1 — gate each CLAUSE independently so a fabricated clause falls while
        # its clean siblings stay. DISPLAY however is sentence-level: when every
        # clause of the sentence survives, the ORIGINAL sentence is shown intact
        # (live finding 2026-07-23: per-clause display produced fragment lines
        # like "Including a driver being shot in Philadelphia." — halves of one
        # analyst sentence). Only a sentence that LOST a clause to a gate falls
        # back to per-clause display of its survivors.
        sent_survivors: List[Tuple[str, Optional[Tuple[int, str]]]] = []
        sent_had_drop = False
        for raw_clause in _split_clauses(sent_notag):
            clause = _clean_ws(raw_clause)
            if not clause or clause.upper().strip(".") == "NONE":
                continue

            # (B) entity gate
            entity_bad = ""
            for m in _ENTITY_RE.finditer(clause):
                ok, bad = _entity_ok(
                    m.group(),
                    corpus_tokens,
                    corpus_bigrams,
                    symbol_tokens,
                    company_tokens,
                )
                if not ok:
                    entity_bad = bad
                    break
            if entity_bad:
                dropped.append(
                    {
                        "kind": kind,
                        "claim": clause,
                        "reason": f"entity '{entity_bad}' not in any source",
                    }
                )
                sent_had_drop = True
                continue

            # (C) numeric gate
            num_ok, bad_num = _numbers_ok(clause, corpus_numbers)
            if not num_ok:
                dropped.append(
                    {
                        "kind": kind,
                        "claim": clause,
                        "reason": f"number '{bad_num}' not in any source",
                    }
                )
                sent_had_drop = True
                continue

            # (D) positive binding (clause-level). A BOUND clause earns a citation
            # ("Tied to: <headline>"). An UNBOUND clause that already passed the
            # entity and numeric gates above is safe analyst REASONING — it
            # SURVIVES, just without a citation. Rationale (live finding
            # 2026-07-23): dropping every clause that shares no headline token
            # reduced cards to stubs (AAPL: 9/11 dropped, incl. "consumer
            # ecosystem will continue to drive growth") — the report must CONNECT
            # news to the model view, which requires reasoning prose. The actual
            # fabrication danger (foreign entities, phantom numbers — the
            # Cerebras/Tate/"$40B" class) is fully caught by gates (B) and (C).
            bound = _bind_headline(
                clause, claimed_idx, headlines, symbol_tokens | company_tokens
            )
            sent_survivors.append((clause, bound))

        if not sent_survivors:
            continue

        # Display-quality gate (both paths): a display unit under 5 words is a
        # fragment ("Higher margins.") — not informative standing alone.
        # Threshold is deliberately 5, not higher: legitimate terse cores like
        # "NVDA's AI stack is changing." (5 words) must survive.
        if not sent_had_drop:
            # Whole sentence clean → display it INTACT (one analyst sentence),
            # cited once per distinct bound headline.
            display = _finalize_display(sent_notag)
            if not display:
                continue
            if len(display.split()) < 5:
                dropped.append(
                    {
                        "kind": kind,
                        "claim": display,
                        "reason": "fragment (too short to stand alone)",
                    }
                )
                continue
            # Headline-echo gate: a sentence whose content tokens are a PURE
            # SUBSET of its bound headline merely restates the news ("looks
            # like the model just relays the news", live feedback 2026-07-23).
            # One novel content token is enough to count as added analysis —
            # deliberately permissive so terse grounded cores survive; the
            # deeper-prompt tasks push real interpretation on top. Applies only
            # to BOUND sentences: unbound reasoning is by definition no echo.
            first_bound = next((b for _c, b in sent_survivors if b), None)
            if first_bound is not None:
                h_tokens = set(_tokenize(first_bound[1])) - _STOPWORDS
                s_tokens = set(_tokenize(display)) - _STOPWORDS
                if s_tokens and not (s_tokens - h_tokens):
                    dropped.append(
                        {
                            "kind": kind,
                            "claim": display,
                            "reason": "headline echo (adds no analysis)",
                        }
                    )
                    continue
            survivors.append(display)
            cited_idx: Set[int] = set()
            for _clause, bound in sent_survivors:
                if bound is not None and bound[0] not in cited_idx:
                    cited_idx.add(bound[0])
                    citations.append(
                        {
                            "kind": kind,
                            "claim": display,
                            "headline_index": bound[0],
                            "headline_text": bound[1],
                        }
                    )
        else:
            # ALL-OR-NOTHING SENTENCES (live feedback 2026-07-23): displaying the
            # surviving clauses of a partially-dropped sentence produced broken
            # standalone fragments ("It more difficult for competitors to gain
            # traction."). A sentence that lost ANY clause to a gate is withheld
            # entirely — its dropped parts are already in ``dropped``; the safe
            # siblings are recorded there too so the ledger stays complete.
            for clause, _bound in sent_survivors:
                display = _finalize_display(clause)
                if display:
                    dropped.append(
                        {
                            "kind": kind,
                            "claim": display,
                            "reason": "sibling clause dropped — sentence withheld",
                        }
                    )

    if not survivors:
        return _PLACEHOLDER[kind], citations, dropped
    return " ".join(survivors), citations, dropped


def verify_grounding(
    bull: str,
    bear: str,
    thesis: str,
    gathered: Dict[str, Any],
    symbol: str,
    company_tokens: Optional[Set[str]] = None,
) -> GroundedSynthesis:
    """Ground the three prose fields against this symbol's own news corpus.

    Pure and deterministic. Every displayed sentence is bound to a real headline;
    every rejected sentence is recorded in ``dropped`` with a reason. Nothing is
    ever invented — a field with no surviving sentence renders an honest
    placeholder.
    """
    symbol_tokens = set(_tokenize(symbol))
    company_tokens = set(company_tokens or set())
    # The issuer's OWN NAME must never be treated as a foreign entity. The runtime
    # company_cache is unpopulated on the desktop, and headlines often carry only
    # the ticker ("AAPL ... Upgrade Cycle") — without this fallback the entity gate
    # dropped "The cost pressure on Apple stock ..." for AAPL with
    # "entity 'Apple' not in any source" (live finding 2026-07-23).
    company_tokens |= _COMPANY_NAME_FALLBACK.get(symbol.upper(), set())
    # #2249 FIX 2: UNION (symbol|company), NEVER a company-only fallback — a
    # ticker-only headline ("SNDK, MU, AMD, NVDA ...") carries the symbol token but
    # not the company name and must survive; the off-subject Tate headline carries
    # neither and is filtered.
    subject_tokens = symbol_tokens | company_tokens
    # #2249 FIX 3: for an AMBIGUOUS ticker (bare symbol collides with a common word
    # or a non-issuer entity — "DIA" = Dow ETF but also Dia Art Foundation) a bare-
    # ticker headline ("Dia's Jessica Morgan ... Tate") would survive a symbol-token
    # filter and bind an off-subject sentence. WHEN the company name tokens are known
    # require a COMPANY-NAME token so such headlines are filtered out of the corpus
    # AND the [H#] binding targets. Distinctive tickers (and an empty company cache)
    # keep the symbol|company union so ticker-only headlines still survive.
    if company_tokens and _ticker_is_ambiguous(symbol_tokens):
        headline_filter_tokens = company_tokens
    else:
        headline_filter_tokens = subject_tokens
    corpus_tokens, corpus_bigrams, corpus_numbers, headlines = _build_corpus(
        symbol, gathered, headline_filter_tokens
    )

    # No-corpus guard: with ZERO subject headlines nothing is verifiable, so no
    # LLM prose may be displayed (honest empty, never fabrication). Without this
    # guard the rebalanced (D) gate would let entity-/number-free prose survive
    # against an empty corpus.
    if not headlines:
        no_corpus_dropped = [
            {"kind": kind, "claim": text.strip(), "reason": "no sources in corpus"}
            for kind, text in (("bull", bull), ("bear", bear), ("thesis", thesis))
            if text.strip() and text.strip().upper() != "NONE"
        ]
        return GroundedSynthesis(
            bull=_PLACEHOLDER["bull"],
            bear=_PLACEHOLDER["bear"],
            thesis=_PLACEHOLDER["thesis"],
            citations=[],
            dropped=no_corpus_dropped,
            grounding_ok=(no_corpus_dropped == []),
        )

    all_citations: List[Dict[str, Any]] = []
    all_dropped: List[Dict[str, Any]] = []
    out: Dict[str, str] = {}
    for kind, text in (("bull", bull), ("bear", bear), ("thesis", thesis)):
        display, cites, drops = _ground_field(
            kind,
            text,
            corpus_tokens,
            corpus_bigrams,
            corpus_numbers,
            headlines,
            symbol_tokens,
            company_tokens,
        )
        out[kind] = display
        all_citations.extend(cites)
        all_dropped.extend(drops)

    return GroundedSynthesis(
        bull=out["bull"],
        bear=out["bear"],
        thesis=out["thesis"],
        citations=all_citations,
        dropped=all_dropped,
        grounding_ok=(all_dropped == []),
    )
