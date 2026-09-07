# core/nlp/news_sentiment.py
# RTR-1 (#1948, Epic #1958) — local NLP headline sentiment for NewsSentimentAgent.
#
# Scoring cascade (plan §5.2, fail-transparent — WARNING never DEBUG, §5.6):
#   1. FinBERT (ProsusAI/finbert via `transformers`, LOCAL offline cache only —
#      no runtime download) — the real finance-domain NLP model (plan Option B).
#   2. Vendored VADER lexicon (data/vader_lexicon.txt, MIT — see
#      data/VADER_LICENSE.txt) — the honest stage-1 fallback when the FinBERT
#      assets / the `transformers` dependency are absent (plan Option C).
#   3. None — no backend available: the agent must ABSTAIN (weight 0.0), never
#      guess (RT-BUG-5 #1971 posture).
#
# Everything here is deterministic (argmax classification / pure lexicon math,
# no sampling) and 100% local/free (feedback_no_paid_apis). Edition-neutral
# finance-core: NO os.environ reads (CODING_POLICY §2.10) — the backend choice
# arrives as the `model` argument, resolved from config by the caller.

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_VADER_LEXICON_PATH = _DATA_DIR / "vader_lexicon.txt"

# ADR-NS-03 (#1948): FinBERT model ids per config NEWS_SENTIMENT_MODEL value.
# "finbert" = ProsusAI/finbert (finance-domain BERT, the plan's Option-B model);
# "finbert-tone" = yiyanghkust/finbert-tone (alternative named in the plan).
# Loaded with local_files_only=True — the weights ship with the build/bundle,
# NEVER downloaded at runtime (offline-cache posture, plan §12.1). Annual review.
_FINBERT_MODEL_IDS: Dict[str, str] = {
    "finbert": "ProsusAI/finbert",
    "finbert-tone": "yiyanghkust/finbert-tone",
}

# ADR-NS-04 (#1948): VADER constants, taken verbatim from the upstream reference
# implementation (vaderSentiment 3.3.2, MIT) so the vendored-lexicon fallback
# stays comparable to the published model: valence normalisation alpha=15,
# negation flip factor -0.74 (N_SCALAR), booster increment 0.293 (B_INCR).
# Annual review together with the lexicon snapshot.
_VADER_ALPHA = 15.0
_VADER_N_SCALAR = -0.74
_VADER_B_INCR = 0.293

# Small negation/booster sets for HEADLINE text (headlines are short, declarative
# strings — the full VADER idiom machinery targets social-media prose and would
# be dead weight here). Kept intentionally minimal + documented (plan Option C).
_NEGATIONS = frozenset(
    {"not", "no", "never", "none", "neither", "nor", "cannot", "without"}
)
_BOOSTERS = frozenset(
    {"very", "extremely", "hugely", "massively", "strongly", "sharply", "record"}
)

_TOKEN_RE = re.compile(r"[a-z0-9$%'’-]+")

# Neutral band for the per-headline label (upstream VADER convention: compound
# within ±0.05 of 0 reads as neutral).
_NEUTRAL_BAND = 0.05


class SentimentResult(NamedTuple):
    """Aggregated headline sentiment + per-headline audit details.

    score:   consensus-scale float ∈ [0, 1] (0.5 = neutral — same semantics as
             every Round-Table vote).
    details: one dict per scored headline: {title, source, label, prob} — the
             MiFID audit trail (which headline → which label drove the score).
    backend: "FinBERT" | "VADER-Lexikon" — which model actually scored, so the
             Senate log shows any degradation honestly.
    """

    score: float
    details: List[Dict[str, Any]]
    backend: str


# ---------------------------------------------------------------------------
# Backend 1 — FinBERT (transformers, offline cache only)
# ---------------------------------------------------------------------------

# Lazy singletons: None = not tried yet, False = tried and unavailable.
_finbert_pipe: Any = None
_vader_lexicon: Any = None


def _load_finbert_pipeline(model: str) -> Callable[[List[str]], List[Dict[str, Any]]]:
    """Build the FinBERT text-classification pipeline from the LOCAL cache.

    Raises ImportError/OSError when `transformers` or the model assets are
    missing — the caller degrades to the vendored lexicon (WARNING, §5.6).
    local_files_only=True is load-bearing: the engine must NEVER download
    ~400MB of weights at runtime (offline-cache posture, plan §12.1).
    """
    from transformers import (  # noqa: WPS433 — optional dep, lazy by design
        AutoModelForSequenceClassification,
        AutoTokenizer,
        pipeline,
    )

    model_id = _FINBERT_MODEL_IDS.get(model, _FINBERT_MODEL_IDS["finbert"])
    tokenizer = AutoTokenizer.from_pretrained(model_id, local_files_only=True)
    net = AutoModelForSequenceClassification.from_pretrained(
        model_id, local_files_only=True
    )
    return pipeline(
        "text-classification", model=net, tokenizer=tokenizer, top_k=None, device=-1
    )


def _get_finbert(model: str) -> Optional[Callable]:
    global _finbert_pipe
    if _finbert_pipe is None:
        try:
            _finbert_pipe = _load_finbert_pipeline(model)
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the vote
            _finbert_pipe = False
            logger.warning(
                "news_sentiment: FinBERT unavailable (%s: %.120s) — degrading to "
                "the vendored VADER lexicon (stage-1 fallback).",
                type(exc).__name__,
                exc,
            )
    return _finbert_pipe or None


def _finbert_signed(raw: Any) -> float:
    """Map one pipeline output to a signed polarity ∈ [-1, 1].

    Accepts both pipeline shapes: a full distribution (top_k=None → list of
    {label, score}) → P(pos) - P(neg); or a single top-label dict → ±prob.
    Deterministic: probabilities come from a softmax forward pass, no sampling.
    """
    if isinstance(raw, dict):
        raw = [raw]
    probs = {str(d.get("label", "")).lower(): float(d.get("score", 0.0)) for d in raw}
    pos = probs.get("positive", 0.0)
    neg = probs.get("negative", 0.0)
    return max(-1.0, min(1.0, pos - neg))


# ---------------------------------------------------------------------------
# Backend 2 — vendored VADER lexicon (stage-1 fallback, dependency-free)
# ---------------------------------------------------------------------------


def _load_vader_lexicon() -> Dict[str, float]:
    """Parse the vendored lexicon (token \t mean-valence[-4..4] \t ...)."""
    lexicon: Dict[str, float] = {}
    with open(_VADER_LEXICON_PATH, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\r\n").split("\t")
            if len(parts) >= 2:
                try:
                    lexicon[parts[0]] = float(parts[1])
                except ValueError:
                    continue
    return lexicon


def _get_vader() -> Optional[Dict[str, float]]:
    global _vader_lexicon
    if _vader_lexicon is None:
        try:
            _vader_lexicon = _load_vader_lexicon()
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the vote
            _vader_lexicon = False
            logger.warning(
                "news_sentiment: vendored VADER lexicon unreadable (%s: %.120s) — "
                "NO NLP backend left, agent will abstain.",
                type(exc).__name__,
                exc,
            )
    return _vader_lexicon or None


def _vader_signed(title: str, lexicon: Dict[str, float]) -> float:
    """Deterministic lexicon polarity for one headline, ∈ [-1, 1].

    Simplified VADER for short declarative headlines: per matched token take the
    lexicon mean valence, flip by N_SCALAR when one of the 2 preceding tokens
    negates, add B_INCR (sign-aligned) when the preceding token boosts; then
    normalise the valence sum with the upstream alpha=15 formula.
    """
    tokens = _TOKEN_RE.findall(title.lower())
    total = 0.0
    for idx, tok in enumerate(tokens):
        valence = lexicon.get(tok)
        if valence is None:
            continue
        window = tokens[max(0, idx - 2) : idx]
        if any(w in _NEGATIONS or w.endswith("n't") for w in window):
            valence *= _VADER_N_SCALAR
        if window and window[-1] in _BOOSTERS:
            valence += math.copysign(_VADER_B_INCR, valence)
        total += valence
    if total == 0.0:
        return 0.0
    return max(-1.0, min(1.0, total / math.sqrt(total * total + _VADER_ALPHA)))


# ---------------------------------------------------------------------------
# Public API — the cascade
# ---------------------------------------------------------------------------


def _label(signed: float) -> str:
    if signed > _NEUTRAL_BAND:
        return "pos"
    if signed < -_NEUTRAL_BAND:
        return "neg"
    return "neu"


def _titles(headlines: Sequence[Any]) -> List[Dict[str, str]]:
    """Normalise headline items to {title, source} (tolerates bare strings)."""
    out: List[Dict[str, str]] = []
    for item in headlines:
        if isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            source = str(item.get("source", "?")).strip() or "?"
        else:
            title, source = str(item).strip(), "?"
        if title:
            out.append({"title": title, "source": source})
    return out


def score_headlines(
    headlines: Optional[Sequence[Any]], model: str = "finbert"
) -> Optional[SentimentResult]:
    """Score point-in-time headlines with the best available LOCAL backend.

    Returns None when there is no signal (no headlines / no usable backend) —
    the caller must then ABSTAIN (weight 0.0), exactly like today's no-LLM path.
    Aggregation: mean signed polarity over all headlines (neutral headlines
    weigh in at 0 — plan §5.2 "neutral-gewichtet"), mapped to the Round-Table
    consensus scale via (1 + mean) / 2 ∈ [0, 1].
    """
    items = _titles(headlines or [])
    if not items:
        return None

    signed: Optional[List[float]] = None
    backend = ""
    if model != "vader":  # "vader" forces the stage-1 lexicon path (ADR-NS-02)
        pipe = _get_finbert(model)
        if pipe is not None:
            try:
                raw = pipe([it["title"] for it in items])
                signed = [_finbert_signed(r) for r in raw]
                backend = "FinBERT"
            except Exception as exc:  # noqa: BLE001 — degrade, never crash
                logger.warning(
                    "news_sentiment: FinBERT inference failed (%s: %.120s) — "
                    "degrading to the vendored VADER lexicon.",
                    type(exc).__name__,
                    exc,
                )
    if signed is None:
        lexicon = _get_vader()
        if lexicon is None:
            return None
        signed = [_vader_signed(it["title"], lexicon) for it in items]
        backend = "VADER-Lexikon"

    details = [
        {
            "title": it["title"],
            "source": it["source"],
            "label": _label(s),
            "prob": round(min(1.0, abs(s)), 4),
        }
        for it, s in zip(items, signed)
    ]
    mean_signed = sum(signed) / len(signed)
    score = max(0.0, min(1.0, (1.0 + mean_signed) / 2.0))
    return SentimentResult(score=score, details=details, backend=backend)


def _reset_backends_for_tests() -> None:
    """Reset the lazy backend singletons (test isolation only)."""
    global _finbert_pipe, _vader_lexicon
    _finbert_pipe = None
    _vader_lexicon = None
