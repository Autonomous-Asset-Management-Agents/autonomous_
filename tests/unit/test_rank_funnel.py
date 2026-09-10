"""Rank-cache funnel: cap the round-table deep-eval set to holdings ∪ top-K (GPU, #round-table-v2).

The round-table graph runs the expensive per-symbol multi-agent eval (torch + Ollama LLM) over every
symbol in `symbols_order` each 60s cycle (~505 on the full universe → GPU saturation). The funnel caps
that to [held positions ∪ the top-K LSTM-ranked symbols from the cached panel]. Because the LSTM rank is
computed over the FULL universe (unbiased) and only the *deep-eval set* is capped, no symbol is dropped
by ordering/alphabet — only low-ranked non-holdings are deferred until they enter the top-K.

Invariants pinned here:
  * K<=0 (default) → symbols_order unchanged → byte-identical to today (funnel OFF).
  * panel thinner than K (cold/warming) → symbols_order unchanged → NEVER trade on a cold funnel.
  * K>0, warm panel → the eval set is exactly [holdings ∪ top-K], order preserved, deterministic.
  * held positions are ALWAYS kept (exits must never be starved).
"""

from core.engine.rank_funnel import apply_rank_cache_funnel


def _xsec(scores):
    return dict(scores)


def test_disabled_when_k_zero_returns_input_unchanged():
    order = ["A", "B", "C", "D"]
    xsec = _xsec({"A": 0.9, "B": 0.8, "C": 0.7, "D": 0.6})
    assert apply_rank_cache_funnel(order, held=set(), xsec=xsec, top_k=0) is order
    assert apply_rank_cache_funnel(order, held=set(), xsec=xsec, top_k=-1) is order


def test_cold_or_thin_panel_falls_back_to_full_universe():
    order = ["A", "B", "C", "D"]
    # panel has only 2 entries but K=3 → not warm enough → full universe (safe, never a cold funnel)
    assert (
        apply_rank_cache_funnel(
            order, held=set(), xsec=_xsec({"A": 0.9, "B": 0.8}), top_k=3
        )
        is order
    )
    # empty panel → full universe
    assert apply_rank_cache_funnel(order, held=set(), xsec={}, top_k=3) is order


def test_caps_to_top_k_by_lstm_score_order_preserved():
    order = ["A", "B", "C", "D", "E"]
    xsec = _xsec(
        {"A": 0.1, "B": 0.9, "C": 0.3, "D": 0.8, "E": 0.2}
    )  # top-2 by score = B, D
    out = apply_rank_cache_funnel(order, held=set(), xsec=xsec, top_k=2)
    assert out == ["B", "D"]  # only the top-2, in symbols_order order (B before D)


def test_holdings_always_kept_even_if_low_ranked():
    order = ["A", "B", "C", "D", "E"]
    xsec = _xsec({"A": 0.1, "B": 0.9, "C": 0.3, "D": 0.8, "E": 0.2})  # top-2 = B, D
    # E is held but ranks last → must survive the funnel
    out = apply_rank_cache_funnel(order, held={"E"}, xsec=xsec, top_k=2)
    assert out == ["B", "D", "E"]
    assert "E" in out


def test_result_is_subset_and_order_preserving():
    order = ["Z", "Y", "X", "W", "V"]
    xsec = _xsec({"Z": 0.5, "Y": 0.4, "X": 0.9, "W": 0.8, "V": 0.7})  # top-3 = X, W, V
    out = apply_rank_cache_funnel(order, held={"Z"}, xsec=xsec, top_k=3)
    assert out == ["Z", "X", "W", "V"]  # keep = {Z(held), X, W, V}, in original order
    assert set(out) <= set(order)


def test_top_k_symbol_absent_from_order_is_ignored():
    # panel ranks a symbol not in this cycle's universe → it consumes a top-K slot but simply is not
    # deep-evaluated (no crash); the remaining in-universe top symbols are kept.
    order = ["A", "B"]
    xsec = _xsec(
        {"A": 0.5, "B": 0.4, "GHOST": 0.99}
    )  # top-2 = GHOST, A → keep = {GHOST, A}
    out = apply_rank_cache_funnel(order, held=set(), xsec=xsec, top_k=2)
    assert out == [
        "A"
    ]  # GHOST silently absent (not in order); B dropped (not in top-2)


# ---- panel staleness guard support (NEW-1) --------------------------------------------------------


def test_latest_snapshot_date_empty_and_populated():
    from datetime import date

    from core.report.lstm_panel_store import LstmPanelStore

    store = LstmPanelStore()
    assert store.latest_snapshot_date() is None  # empty → None (cold start)
    store.record_cycle(date(2024, 1, 10), [("A", 0.9), ("B", 0.1)])
    store.record_cycle(date(2024, 1, 12), [("A", 0.8), ("B", 0.2)])
    assert store.latest_snapshot_date() == date(
        2024, 1, 12
    )  # the freshest snapshot's date


# ---- increment 2: periodic rank refresh cadence ---------------------------------------------------


def test_refresh_every_cycle_when_interval_disabled():
    from core.engine.rank_funnel import should_refresh_rank

    assert should_refresh_rank(last_refresh=None, now=100.0, interval_minutes=0) is True
    assert (
        should_refresh_rank(last_refresh=100.0, now=100.5, interval_minutes=0) is True
    )  # <=0 → always
    assert (
        should_refresh_rank(last_refresh=100.0, now=100.5, interval_minutes=-5) is True
    )


def test_refresh_first_cycle_then_only_per_interval():
    from core.engine.rank_funnel import should_refresh_rank

    # first cycle (no prior refresh) → run the initial full pass
    assert should_refresh_rank(last_refresh=None, now=0.0, interval_minutes=240) is True
    # within the interval (4h = 14400s) → skip
    assert (
        should_refresh_rank(last_refresh=0.0, now=10_000.0, interval_minutes=240)
        is False
    )
    # exactly at / past the interval → refresh
    assert (
        should_refresh_rank(last_refresh=0.0, now=14_400.0, interval_minutes=240)
        is True
    )
    assert (
        should_refresh_rank(last_refresh=0.0, now=20_000.0, interval_minutes=240)
        is True
    )
