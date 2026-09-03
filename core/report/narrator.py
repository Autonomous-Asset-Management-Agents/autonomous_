# core/report/narrator.py
# RPT-5 (#2004, Epic #1998) — the LLM narrator: fills the three prose slots the
# deterministic renderer (RPT-3) leaves as placeholders, gated by the mechanical
# auditor (RPT-4). This is the ONLY LLM-touching module in the report pipeline.
"""LLM narrator for the report's prose slots (thesis / catalysts / risks).

This is the *narration* layer of the auditable report pipeline (Epic #1998).
The renderer (RPT-3) emits the mechanical sections verbatim and leaves three
narrow prose slots as clearly-marked ``<!-- SLOT:... -->`` placeholders; this
module fills them. It is the deliberate seam where — and only where — a language
model touches the report, so every guardrail lives here.

Design contract:

* **Deterministic-first prompting.** The *serialized FactSet* is the ONLY
  sanctioned source of facts handed to the model ("no number, no event that is
  not in the FACT SET"). Each slot is narrow (2-3 sentences) and carries explicit
  VERBOTE (bans): no fabricated number, no directional forecast, and no
  characterization of the model score as reliable/honest (it is a dimensionless
  rank). Proven locally against Ollama in ``scratchpad/step0c_polished.py`` /
  ``step0b_template_test.py`` (0 fabricated numbers) — that real run is the
  evidence anchor and is deliberately NOT invoked in CI (non-deterministic).

* **Guardrails (Step-0c-proven).** A no-preamble instruction plus a post-strip
  removes model chatter ("Hier sind..."). A banned-word filter rejects the score
  characterizations above: a hit triggers ONE nachbesserung; if it persists the
  slot abstains ("") rather than emit a banned adjective.

* **Re-narrate loop (uses the RPT-4 auditor).** After narration the composed
  report ``render(fact_set, narration)`` is passed through ``audit(...)``. A
  STRIKE (a fabricated number/entity/date the model slipped in) triggers ONE
  re-narrate of exactly the offending slot(s) with the auditor's critique; if the
  strike persists that slot is set to "" — a **deterministic abstain**, never an
  invented claim. Because an abstained slot renders as a deterministic
  placeholder, the final report always audits clean.

BORA / edition-agnostic. The model is reached ONLY through the sanctioned
``core.llm.provider.get_llm_provider()`` seam (desktop: Ollama opt-in / Gemini;
cloud: Gemini / OpenAI / Anthropic per ``LLM_PROVIDER``) — no provider is named
or configured here. No Redis / SQLite / ``DEPLOYMENT_MODE`` / edition
special-casing; no clock, no randomness of our own (the provider owns decoding,
and the sanctioned providers decode at temperature 0.0). Report-only and
DORMANT: nothing here is read by the decision path; ``SPECIALIST_ALPHA_WEIGHT``
stays 0.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Mapping, Optional

from core.report.auditor import audit as _default_audit
from core.report.fact_set import FactSet, NewsItem
from core.report.insight_patterns import (
    _EVENT_KEYWORDS,
    _STRUCTURAL_KEYWORDS,
    _classify,
)
from core.report.render import _rec_parts, _rel, render

# ---------------------------------------------------------------------------
# Tunables (all display/heuristic — no order or capital path reads them).
# ---------------------------------------------------------------------------
# The three narration slots, in the renderer's own order (render._section_*).
SLOT_NAMES: tuple = ("thesis", "catalysts", "risks")

# Cost guard on the narration call (narrow slots need little; step0c used 220).
_SLOT_MAX_TOKENS = 220

# Banned substrings: characterizations that dress the dimensionless rank up as a
# quality/trustworthiness signal. Checked lower-cased against the slot prose, in
# both umlaut and ASCII-fold spellings so a real German reply is caught too.
_BANNED: tuple = (
    "zuverläss",
    "zuverlaess",
    "verlässlich",
    "verlaesslich",
    "ehrlich",
    "vertrauens",
    "beeindruck",
    "hervorrag",
)

# Lower-cased line prefixes that mark model preamble / scaffolding to drop.
_PREAMBLE_PREFIXES: tuple = (
    "hier ",
    "hier sind",
    "hier ist",
    "basierend",
    "zusammenfassung",
    "die folgenden",
    "folgende",
    "natuerlich",
    "natürlich",
    "gerne",
    "klar",
    "sicher",
    "selbstverständlich",
    "selbstverstaendlich",
)

_LEADING_BULLET_RE = re.compile(r"^[\-\*\d\.\)\s]+")

# The no-preamble output contract appended to every slot prompt (Step-0c NO_PRE).
_NO_PRE = (
    "\n\nAUSGABE-FORMAT: Antworte NUR mit dem fertigen Fliesstext selbst. KEINE "
    "Einleitung ('Hier sind...', 'Basierend auf...', 'Zusammenfassung:'), keine "
    "Ueberschrift, keine Doppelpunkt-Zeile, keine Aufzaehlungszeichen, keine "
    "Instruktions-Wiederholung. Beginne direkt mit dem ersten inhaltlichen Wort. "
    "Deutsch."
)


# ---------------------------------------------------------------------------
# FactSet serialization — the single allowed fact source for the prompt.
# ---------------------------------------------------------------------------
def _num(value: Any, nd: int = 2) -> str:
    """Format a numeric fact to ``nd`` decimals, or an honest gap marker."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _trend(values: Optional[List[float]]) -> str:
    if not values:
        return "n/a"
    return " -> ".join(f"{float(v):.2f}" for v in values)


def _mc(card: Mapping[str, Any], key: str, nd: Optional[int] = None) -> str:
    val = card.get(key)
    if val is None:
        return "n/a"
    if nd is None:
        return str(val)
    try:
        return f"{float(val):.{nd}f}"
    except (TypeError, ValueError):
        return str(val)


def _pct(value: Any, nd: int = 1) -> str:
    """A fraction fact (e.g. realized vol 0.349) rendered as a percent."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _band_phrase(fact_set: FactSet) -> Optional[str]:
    """This symbol's REAL rank band as plain prose — or ``None`` for no band.

    ⚠ Reuses the renderer's own :func:`core.report.render._rec_parts`, deliberately.
    A second band system here would drift, and a drifted one would let the prompt
    order a band the report itself does not print. ``_rec_parts`` is already
    fail-CLOSED — it returns ``(None, None, None)`` when there is no rank, and no
    rank means no band, not a neutral one.

    The bucket carries the renderer's Markdown emphasis; the prompt is plain text,
    so the ``**`` is stripped rather than re-worded (re-wording would be a second
    definition again).
    """
    _verb, _handlung, bucket = _rec_parts(fact_set.lstm_xsec_percentile.value)
    if bucket is None:
        return None
    return bucket.replace("**", "")


def _score_direction(fact_set: FactSet) -> Optional[str]:
    """'stieg' / 'fiel' from the REAL score trend — ``None`` when undecidable.

    A flat trend, a single point or no trend at all yields ``None``: the slot then
    omits the claim entirely rather than assert a direction the FactSet disproves.
    """
    trend = fact_set.lstm_score_trend.value
    if not trend or len(trend) < 2:
        return None
    first, last = float(trend[0]), float(trend[-1])
    if last > first:
        return "stieg"
    if last < first:
        return "fiel"
    return None


def _band_suffix(fact_set: FactSet) -> str:
    band = _band_phrase(fact_set)
    return f", {band}" if band else ""


def _news_lines(items: Optional[List[NewsItem]]) -> str:
    if not items:
        return "- (keine PIT-validen Katalysatoren im Fenster)"
    return "\n".join(
        f"- {ni.title} ({ni.published_utc[:10]}, {ni.publisher})" for ni in items
    )


def serialize_fact_set(fact_set: FactSet) -> str:
    """Render the FactSet into the compact, deterministic FACT SET block.

    This is the ONLY fact source the narrator is permitted to reference. Every
    number here is a rounded view of a real ``Fact`` (within the auditor's
    tolerance), so anything the model copies verbatim survives ``audit``.
    """
    fs = fact_set
    card = fs.model_card.value or {}
    lines = [
        f"Symbol: {fs.symbol}",
        f"Stichtag (as-of): {fs.as_of.isoformat()}",
        f"Schlusskurs: {_num(fs.close.value, 2)} USD",
        f"Pullback: {_num(fs.pullback_pct.value, 1)}% ueber die letzten Schlusskurse",
        (
            f"Gleitende Mittel: MA20 {_num(fs.ma20.value, 2)}, "
            f"MA50 {_num(fs.ma50.value, 2)}, MA100 {_num(fs.ma100.value, 2)}, "
            f"MA200 {_num(fs.ma200.value, 2)}"
        ),
        # ⚠ The literal ", Top-Quintile;" used to sit here UNCONDITIONALLY, in the
        # block this module's own docstring calls the "ONLY fact source". Every
        # symbol was handed NVDA's band as a fact: LYB at percentile 25.5 was
        # written up as "Top-Quintil" — ordered, not hallucinated. The band is now
        # derived per symbol, and omitted entirely when there is no rank.
        (
            f"LSTM-Score: {_num(fs.lstm_score.value, 2)} "
            f"(Querschnitts-Perzentil {_num(fs.lstm_xsec_percentile.value, 1)} "
            f"unter {fs.lstm_xsec_n.value} Symbolen{_band_suffix(fs)}; "
            f"Eigen-Perzentil {_num(fs.lstm_own_percentile.value, 1)})"
        ),
        f"Score-Trend (letzte Werte): {_trend(fs.lstm_score_trend.value)}",
        (
            "Zuverlaessigkeit (Modellkarte, dimensionsloser Rang, KEINE "
            f"Return-Prognose): cross-sectional IC {_mc(card, 'cs_ic_mean')}, "
            f"t {_mc(card, 'cs_ic_tstat')}; Brutto-Sharpe Top-Quintile "
            f"{_mc(card, 'sharpe_topq_gross')} vs EqualWeight "
            f"{_mc(card, 'sharpe_equalweight_gross')}; NETTO "
            f"{_mc(card, 'sharpe_topq_net_10bps')} UNTER EqualWeight "
            f"{_mc(card, 'sharpe_equalweight_net_10bps')}; "
            f"Turnover ~{_mc(card, 'turnover_daily', 2)}/Tag"
        ),
        (
            f"Realisierte Vol: 20d {_pct(fs.rvol_20d.value)}%, "
            f"63d {_pct(fs.rvol_63d.value)}%"
        ),
        "Nachrichten (PIT, nur Kontext, keine Richtungsableitung):",
        _news_lines(fs.news_items.value),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Slot prompts (narrow; deterministic-first; Step-0c VERBOTE).
# ---------------------------------------------------------------------------
# The VERBOTE are symbol-independent and stay constant; only the KERNPUNKTE — the
# claims the model is told to make — are derived per symbol.
_THESIS_VERBOTE = (
    "VERBOTE: den Modell-Score NICHT als "
    "zuverlaessig/ehrlich/vertrauenswuerdig/beeindruckend/stark charakterisieren "
    "(er ist ein dimensionsloser Rang); KEINE Zahl, die nicht woertlich im FACT "
    "SET steht; keine Return- oder Kurszielprognose; KEINEN Kernpunkt ergaenzen, "
    "der oben nicht aufgefuehrt ist."
)
_CATALYSTS_VERBOTE = (
    "VERBOTE: keine Richtungs-Prognose ableiten; kein Ereignis und keine Zahl, "
    "die nicht im FACT SET stehen; keine Wertung ueber das Gegebene hinaus."
)
_RISKS_VERBOTE = "VERBOTE: keine neue Zahl, kein erfundenes Risiko."


def _thesis_instruction(fs: FactSet) -> Optional[str]:
    """The thesis AUFGABE, built from what is TRUE for THIS symbol.

    ⚠ This dict entry used to be a CONSTANT STRING mandating three claims of NVDA's
    story for every symbol: top-quintile, a rising-score-vs-falling-price divergence,
    and a price above the moving averages. The model obeyed. Each Kernpunkt is now
    derived, and a Kernpunkt that is not true is OMITTED — never inverted into a
    counter-claim, and never padded with filler.

    Returns ``None`` when nothing honest remains to say; the caller abstains and the
    renderer emits its existing deterministic placeholder.
    """
    points: List[str] = []

    band = _band_phrase(fs)
    if band is not None:
        points.append(f"das Modell ordnet {fs.symbol} relativ zum Universum {band}")

    # The divergence is a CONJUNCTION: a rising score AND a price that came back off
    # the high. Ordering it whenever the score rose is how a falling stock's rally
    # became a "Divergenz". Both halves must hold, or the point is dropped.
    direction = _score_direction(fs)
    pullback = fs.pullback_pct.value
    if direction == "stieg" and pullback is not None and float(pullback) < 0:
        points.append(
            "der Modell-Score stieg, waehrend der Kurs vom Hoch zurueckkam — eine "
            "Divergenz (reine Beobachtung, KEINE Kausaldeutung)"
        )
    elif direction is not None:
        points.append(f"der Modell-Score {direction} zuletzt (reine Beobachtung)")

    rel_short = _rel(fs.close.value, fs.ma20.value)
    rel_long = _rel(fs.close.value, fs.ma200.value)
    if rel_short != "n/a" and rel_long != "n/a":
        points.append(
            f"der Kurs liegt {rel_short} dem kurzfristigen und {rel_long} dem "
            f"langfristigen gleitenden Mittel"
        )

    if not points:
        return None
    numbered = "; ".join(f"({i}) {p}" for i, p in enumerate(points, start=1))
    return (
        f"Schreibe 2 knappe, professionelle Saetze Investment-These fuer "
        f"{fs.symbol}. Kernpunkte (nur was im FACT SET belegt ist): {numbered}. "
        f"{_THESIS_VERBOTE}"
    )


def _catalysts_instruction(fs: FactSet) -> Optional[str]:
    """The catalysts AUFGABE — the event mandate only when a headline proves one.

    ⚠ The constant ordered: "Erwaehne, dass der Ergebnistermin KURZ NACH dem Stichtag
    liegt". The FactSet has NO earnings-date field — there is nothing it could ever
    source that claim from, for any symbol. So the model manufactured a date every
    time. The event is now asserted only when ``_EVENT_KEYWORDS`` matches a REAL PIT
    headline, reusing the detector ``horizon_separation_pattern`` already relies on.
    """
    news = fs.news_items.value or []
    if not news:
        return None
    instruction = (
        "Schreibe 2-3 fluessige Saetze Markt-Kontext AUSSCHLIESSLICH aus den "
        "Nachrichten im FACT SET. "
    )
    if _classify(news, _EVENT_KEYWORDS):
        instruction += (
            "Eine der Meldungen nennt einen Ergebnistermin — erwaehne die daraus "
            "folgende Event-Unsicherheit, ohne ein Datum zu erfinden. "
        )
    return instruction + _CATALYSTS_VERBOTE


def _risks_instruction(fs: FactSet) -> Optional[str]:
    """The risks AUFGABE — each risk named only where the FactSet evidences it.

    ⚠ The constant ordered "die belegten Wettbewerbs-/Wachstumssorgen" for every
    symbol. A real GD note obeyed with: "der Konkurrent Global X's SHLD" — an ETF,
    not a competitor. No competition evidence existed, so the model built one out of
    the nearest headline. Exactly as instructed.

    The execution risk stays unconditional: the model card evidences it directly
    (``sharpe_topq_net_10bps`` UNDER ``sharpe_equalweight_net_10bps``), and that line
    is in the serialized FACT SET for every symbol.
    """
    news = fs.news_items.value or []
    risks: List[str] = []
    if _classify(news, _EVENT_KEYWORDS):
        risks.append("das in den Meldungen belegte binaere Ergebnis-Ereignis")
    if _classify(news, _STRUCTURAL_KEYWORDS) or (fs.cons.value or []):
        risks.append("die belegten Wettbewerbs-/Wachstumssorgen")
    risks.append(
        "das Ausfuehrungsrisiko (netto schlaegt die Strategie EqualWeight bei "
        "taeglichem Rebalancing nicht)"
    )
    return (
        f"Schreibe 2 praezise Saetze zu den Hauptrisiken, nur aus dem FACT SET. "
        f"Nenne AUSSCHLIESSLICH: {', '.join(risks)}. {_RISKS_VERBOTE}"
    )


# Per-symbol instruction builders, one per slot. Each returns ``None`` when the
# FactSet proves nothing the slot could honestly say -> deterministic abstain.
_SLOT_INSTRUCTION_BUILDERS: Dict[str, Callable[[FactSet], Optional[str]]] = {
    "thesis": _thesis_instruction,
    "catalysts": _catalysts_instruction,
    "risks": _risks_instruction,
}


def _build_prompt(
    fact_set: FactSet, slot: str, serialized: str, critique: Optional[str]
) -> str:
    """Assemble the deterministic-first prompt for one slot.

    Returns ``""`` when the slot has no honest instruction to give — the caller
    turns that into an abstain rather than prompting for filler.
    """
    instruction = _SLOT_INSTRUCTION_BUILDERS[slot](fact_set)
    if instruction is None:
        return ""
    correction = f"\n\nKORREKTUR (Nachbesserung): {critique}" if critique else ""
    return (
        f"SLOT: {slot}\n"
        "Du bist ein nuechterner Equity-Research-Autor. Das FACT SET unten ist die "
        "EINZIGE erlaubte Faktenquelle — verwende KEINE Zahl und KEIN Ereignis, "
        "das nicht darin steht.\n\n"
        f"FACT SET:\n{serialized}\n\n"
        f"AUFGABE: {instruction}"
        f"{correction}"
        f"{_NO_PRE}"
    )


# ---------------------------------------------------------------------------
# Guardrails.
# ---------------------------------------------------------------------------
def _strip_preamble(text: str) -> str:
    """Drop leading model preamble/headings/bullets; join content lines.

    Port of ``scratchpad/step0c_polished.py`` ``strip_pre``: skip preamble lines
    until the first content line, then strip leading bullet/number markers.
    """
    lines = [ln.strip() for ln in text.strip().split("\n") if ln.strip()]
    kept: List[str] = []
    started = False
    for ln in lines:
        low = ln.lower()
        if not started and (
            low.startswith(_PREAMBLE_PREFIXES)
            or ln.endswith(":")
            or low.startswith(("**", "- ", "* ", "#"))
        ):
            continue
        started = True
        kept.append(_LEADING_BULLET_RE.sub("", ln))
    return " ".join(kept).strip()


def _has_banned(text: str) -> bool:
    low = text.lower()
    return any(b in low for b in _BANNED)


# ---------------------------------------------------------------------------
# LLM access (ONLY via the injected provider or the sanctioned seam).
# ---------------------------------------------------------------------------
def _resolve_llm(llm: Optional[Any]) -> Optional[Any]:
    """Return the caller's provider, else the sanctioned process-wide seam.

    Lazy import keeps ``import core.report`` free of the provider import chain
    and mirrors the in-function seam resolution used across the consumers
    (e.g. ``core/round_table/agents.py``).
    """
    if llm is not None:
        return llm
    from core.llm.provider import get_llm_provider

    return get_llm_provider()


def _generate(llm: Any, prompt: str) -> str:
    """Call a provider-shaped object or a bare callable; "" on any failure."""
    try:
        gen = getattr(llm, "generate_content", None)
        if callable(gen):
            return gen(prompt, max_output_tokens=_SLOT_MAX_TOKENS) or ""
        if callable(llm):
            return llm(prompt) or ""
    except Exception:  # pragma: no cover - provider owns its own "" contract
        return ""
    return ""


def _narrate_slot(
    fact_set: FactSet,
    slot: str,
    serialized: str,
    llm: Any,
    critique: Optional[str],
) -> str:
    """Produce one clean slot: generate, strip preamble, enforce the word ban.

    A banned-word hit triggers ONE nachbesserung; a persisting hit abstains ("").

    An empty prompt means the FactSet proves nothing this slot could honestly say:
    abstain WITHOUT calling the model. Prompting anyway is what produced filler —
    an instruction with no evidence behind it is an invitation to invent.
    """
    prompt = _build_prompt(fact_set, slot, serialized, critique)
    if not prompt:
        return ""
    text = _strip_preamble(_generate(llm, prompt))
    if not _has_banned(text):
        return text
    ban_critique = (
        "Der vorige Entwurf charakterisierte den Modell-Score wertend "
        "(z. B. zuverlaessig/ehrlich). Der Score ist NUR ein dimensionsloser "
        "Rang. Schreibe neu, OHNE jede solche Wertung."
    )
    retry = _strip_preamble(
        _generate(llm, _build_prompt(fact_set, slot, serialized, ban_critique))
    )
    return "" if _has_banned(retry) else retry


# ---------------------------------------------------------------------------
# Re-narrate loop attribution helpers.
# ---------------------------------------------------------------------------
def _slots_with_flags(slots: Mapping[str, str], flags) -> set:
    """Which slots introduced a flagged token (deterministic sections are clean).

    A strike can only come from slot prose — the renderer's mechanical sections
    reference FactSet values only. Attribute each flag to the slot whose text
    contains its token; a flag that matches no slot (defensive) implicates every
    non-empty slot so the loop can never silently pass a strike.
    """
    culprits: set = set()
    for flag in flags:
        token = flag.token
        matched = False
        for name, text in slots.items():
            if text and (token in text or token.lstrip("$") in text):
                culprits.add(name)
                matched = True
        if not matched:
            culprits.update(n for n, t in slots.items() if t)
    return culprits


def _critique_for(slots: Mapping[str, str], name: str, flags) -> str:
    """Build the auditor-driven correction listing the unsourced tokens."""
    text = slots[name]
    mine = [
        f for f in flags if text and (f.token in text or f.token.lstrip("$") in text)
    ]

    # ADR-AUDIT-BAND-01: a band flag needs a DIFFERENT reason than a fabricated
    # number. The generic critique below says the token "steht NICHT im FACT SET"
    # and "im Zweifel lasse die Zahl weg" — both false for a band claim: the
    # percentile IS in the fact set, and there is no number to drop. Handing the
    # model a wrong reason invites a wrong rewrite (drop the digit, keep "starke
    # Position"), which strikes again and blanks the slot. So state the true
    # standing and let it write honest prose. This is the SAME retry loop — only
    # the critique text is band-aware; no second mechanism is introduced.
    band = [f for f in mine if getattr(f, "kind", "") == "band"]
    if band:
        listed = ", ".join(sorted({f.token for f in band}))
        pct = next((f.value for f in band if f.value is not None), None)
        standing = (
            "Fuer diesen Stichtag gibt es UEBERHAUPT KEINEN Rang im "
            "Aktien-Vergleich. Jede Aussage ueber eine Rangposition ist damit "
            "erfunden — beschreibe gar keine."
            if pct is None
            else f"Tatsaechlich liegt der Titel im {pct:.1f}. Perzentil seines "
            f"Universums (100 = bester Rang, 0 = schlechtester)."
        )
        return (
            f"Der vorige Entwurf behauptete eine Rangposition, die den Daten "
            f"WIDERSPRICHT ({listed}). {standing} Schreibe den Slot neu und "
            "beschreibe die Rangposition entweder korrekt oder gar nicht."
        )

    tokens = sorted({f.token for f in mine})
    listed = ", ".join(tokens) if tokens else "eine nicht belegte Angabe"
    return (
        f"Der vorige Entwurf enthielt nicht belegte Angaben ({listed}). Diese "
        "stehen NICHT im FACT SET. Schreibe den Slot neu und verwende "
        "ausschliesslich Angaben aus dem FACT SET; im Zweifel lasse die Zahl weg."
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def narrate(
    fact_set: FactSet,
    *,
    llm: Optional[Any] = None,
    auditor: Optional[Callable[[str, FactSet], Any]] = None,
) -> Dict[str, str]:
    """Fill the report's three prose slots from ``fact_set``, gated by the auditor.

    Parameters
    ----------
    fact_set:
        The frozen, PIT-filtered fact foundation (RPT-1). Its serialization is the
        ONLY fact source the model is allowed to reference.
    llm:
        Optional provider. Either a provider-shaped object exposing
        ``generate_content(prompt, max_output_tokens) -> str`` (the shape of the
        sanctioned :func:`core.llm.provider.get_llm_provider` result) or a bare
        ``callable(prompt) -> str``. When ``None`` the sanctioned process-wide
        seam is resolved — no provider is ever named or configured here (BORA).
        When the seam yields ``None`` (no key / SDK unavailable) every slot
        abstains.
    auditor:
        Optional ``callable(report_md, fact_set) -> AuditResult`` gate for the
        re-narrate loop. Defaults to :func:`core.report.auditor.audit`.

    Returns
    -------
    dict[str, str]
        ``{"thesis": ..., "catalysts": ..., "risks": ...}`` — always all three
        keys. A slot that could not be narrated cleanly (no provider, a persisting
        banned word, or a persisting fabrication) is ``""`` — a deterministic
        abstain that the renderer emits as its honest placeholder. Feed this
        straight to :func:`core.report.render.render` via ``narration=``.
    """
    gate = auditor or _default_audit
    provider = _resolve_llm(llm)
    if provider is None:
        return dict.fromkeys(SLOT_NAMES, "")

    serialized = serialize_fact_set(fact_set)
    slots: Dict[str, str] = {
        name: _narrate_slot(fact_set, name, serialized, provider, None)
        for name in SLOT_NAMES
    }

    # Re-narrate loop: audit the COMPOSED report; a strike can only be slot prose.
    result = gate(render(fact_set, narration=slots), fact_set)
    if not result.integrity_ok:
        for name in _slots_with_flags(slots, result.flags):
            critique = _critique_for(slots, name, result.flags)
            slots[name] = _narrate_slot(fact_set, name, serialized, provider, critique)
        # A persisting strike -> deterministic abstain (never an invented claim).
        result = gate(render(fact_set, narration=slots), fact_set)
        if not result.integrity_ok:
            for name in _slots_with_flags(slots, result.flags):
                slots[name] = ""

    return slots


__all__ = ["narrate", "serialize_fact_set", "SLOT_NAMES"]
