"""Rank-cache funnel — cap the round-table deep-eval set to holdings ∪ top-K (GPU load, round-table v2).

The round-table graph runs the expensive per-symbol multi-agent evaluation (torch inference + Ollama LLM
agents) over EVERY symbol in `symbols_order` on each ~60s cycle. On the full universe (~505) that
saturates the GPU. This funnel caps the deep-eval set to [held positions ∪ the top-K LSTM-ranked symbols
read from the cached panel (`core/report/lstm_panel_store`)].

Why it is bias-free: the LSTM rank is computed over the FULL universe (unbiased) and only refreshed a few
times a day; the funnel caps the *deep-eval set*, not the ranking — so no symbol is dropped by ordering or
alphabet, only low-ranked non-holdings are deferred until they enter the top-K.

Safety: with the flag off (K<=0) or a cold/thin panel, the funnel is a no-op → the full universe is
evaluated exactly as today. Held positions are always kept so exits are never starved.
"""

from typing import Dict, List, Sequence, Set


def apply_rank_cache_funnel(
    symbols_order: List[str],
    held: Set[str],
    xsec: Dict[str, float],
    top_k: int,
) -> List[str]:
    """Return the capped deep-eval set: [held ∪ top-K], in `symbols_order` order.

    Args:
        symbols_order: this cycle's symbols to deep-evaluate (the round-table set), in loop order.
        held:          currently held position symbols — ALWAYS kept (exits must not be starved).
        xsec:          the cached LSTM cross-section {symbol: score} (`cross_section_at(now)`).
        top_k:         deep-eval cap. <=0 disables the funnel (byte-identical to today).

    No-op (returns the input list object unchanged) when the funnel is disabled OR the panel is not warm
    enough (fewer than `top_k` entries) — the engine must never trade on a cold funnel.
    """
    if top_k <= 0:
        return symbols_order
    if len(xsec) < top_k:  # panel cold/warming — fall back to the full universe (safe)
        return symbols_order
    top = {
        s for s, _ in sorted(xsec.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    }
    keep = set(held) | top
    return [s for s in symbols_order if s in keep]


def should_refresh_rank(
    last_refresh: "float | None", now: float, interval_minutes: float
) -> bool:
    """Whether the LSTM rank producer should re-rank the FULL universe this cycle (increment 2).

    The producer's ranking (an LSTM forward over ~500 symbols) drives the panel the funnel reads. The
    LSTM works on daily bars, so it need not be recomputed every 60s. This gates it to a periodic
    refresh; the panel store retains the last ranking between refreshes (point-in-time reads).

    interval_minutes<=0 → refresh EVERY cycle (byte-identical to running the producer each loop).
    Otherwise refresh on the first cycle (`last_refresh` None) and once per interval thereafter.
    `now`/`last_refresh` are a monotonic clock (seconds); immune to wall-clock jumps.
    """
    if interval_minutes <= 0:
        return True
    if last_refresh is None:
        return True
    return (now - last_refresh) >= interval_minutes * 60.0
