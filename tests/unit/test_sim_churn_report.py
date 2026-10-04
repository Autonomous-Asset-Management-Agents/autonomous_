# tests/unit/test_sim_churn_report.py
"""Sim churn A/B report (Epic #1957 guard-hardening evidence, prove-it-or-kill-it).

Pure metrics over sim_result.json orders quantifying EXACTLY the forensic patterns of
04.-07.08: same-minute opposite-side pairs (wash class), exact-qty roundtrips < 1 day
(rotation churn), dust orders, turnover. Run the same replay day with legacy flags (A)
vs the new guard defaults (B) and the diff is the merge evidence for #2711/#2712/#2714.
"""

from core.sim.churn_report import churn_metrics, render_compare


def _o(sym, side, qty, price, ts, status="filled"):
    return {
        "symbol": sym,
        "side": side,
        "qty": qty,
        "status": status,
        "fill_price": price,
        "filled_at": ts,
        "blocked_reason": None,
    }


FORENSIC = [
    # XOM wash pair - identical qty, same minute, opposite sides
    _o("XOM", "sell", 0.4429, 152.94, "2026-08-07T17:02:10+00:00"),
    _o("XOM", "buy", 0.4429, 152.95, "2026-08-07T17:02:30+00:00"),
    # HPE exact-qty roundtrip < 1 day (buy evening, sell next morning)
    _o("HPE", "buy", 0.3136, 53.08, "2026-08-06T21:20:00+00:00"),
    _o("HPE", "sell", 0.3136, 51.63, "2026-08-07T16:01:00+00:00"),
    # dust order
    _o("WY", "buy", 0.0655, 25.42, "2026-08-06T21:20:00+00:00"),
    # a healthy fill (no pair, above dust)
    _o("DHR", "buy", 1.0, 198.55, "2026-08-06T15:00:00+00:00"),
    # blocked order must not count anywhere
    _o("NVDA", "buy", 1.0, None, None, status="blocked"),
]


def test_metrics_quantify_the_forensic_patterns():
    m = churn_metrics(FORENSIC, min_value=50.0)
    assert m["n_filled"] == 6
    assert m["same_minute_pairs"] == 1  # the XOM pair (60s window)
    assert m["roundtrips_lt_1d"] == 2  # XOM (same minute) + HPE (overnight <24h)
    assert m["turnover"] > 0


def test_dust_counts_only_filled_below_min_value():
    m = churn_metrics(FORENSIC, min_value=50.0)
    # WY $1.67 and HPE legs ($16.6/$16.2)? HPE buy = 0.3136*53.08 = $16.65 -> dust too.
    # Deterministic expectation: WY(1.67), HPE buy(16.65), HPE sell(16.19), XOM legs 67.7 (no),
    # DHR 198 (no) => 3 dust fills.
    assert m["dust_orders"] == 3


def test_clean_run_has_zero_churn_flags():
    clean = [
        _o("AAPL", "buy", 2.0, 200.0, "2026-08-06T15:00:00+00:00"),
        _o("MSFT", "buy", 1.0, 400.0, "2026-08-06T15:30:00+00:00"),
        _o("AAPL", "sell", 2.0, 210.0, "2026-08-08T15:00:00+00:00"),  # >1d hold
    ]
    m = churn_metrics(clean, min_value=50.0)
    assert m["same_minute_pairs"] == 0
    assert m["roundtrips_lt_1d"] == 0
    assert m["dust_orders"] == 0


def test_missing_timestamps_fail_safe():
    legacy = [_o("X", "buy", 1.0, 100.0, None), _o("X", "sell", 1.0, 100.0, None)]
    m = churn_metrics(legacy, min_value=50.0)
    assert m["n_filled"] == 2  # never crashes; time-based metrics simply see no pairs
    assert m["same_minute_pairs"] == 0


def test_render_compare_shows_deltas():
    a = churn_metrics(FORENSIC, min_value=50.0)
    b = churn_metrics([], min_value=50.0)
    out = render_compare(a, b, label_a="legacy", label_b="hardened")
    assert "same_minute_pairs" in out and "legacy" in out and "hardened" in out


def test_uppercase_sides_from_the_real_runner_are_counted():
    # The runner emits "BUY"/"SELL" (broker enum casing) - the report must normalize.
    ups = [
        _o("CSCO", "BUY", 87.57, 115.61, "2026-07-28T09:30:00-04:00"),
        _o("CSCO", "SELL", 87.57, 115.70, "2026-07-28T09:30:40-04:00"),
    ]
    m = churn_metrics(ups, min_value=50.0)
    assert m["n_filled"] == 2
    assert m["same_minute_pairs"] == 1
    assert m["roundtrips_lt_1d"] == 1
