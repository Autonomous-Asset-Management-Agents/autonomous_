"""#3361 — regime-beyond-VIX signal: composite, threshold, throttle, cache, fail-open.

The throttle may only ever SHRINK a new BUY, and only on a fresh, credit-led reading.
Everything uncertain (missing lead component, short history, stale or future-dated
cache, unreadable file) must read as "no throttle" (factor 1.0).
"""

import json
from datetime import date, timedelta

import pandas as pd

from core.engine.regime_signal import (
    MIN_HISTORY_DAYS,
    WEIGHTS,
    compute_regime_state,
    is_fresh,
    load_regime_state,
    refresh_regime_cache,
    regime_reading,
    riskoff_composite,
    threshold_value,
    throttle_factor,
)


def test_weights_are_credit_led_and_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
    assert WEIGHTS["credit"] == max(WEIGHTS.values())


def test_composite_weighted_mean():
    comps = {
        "credit": 80,
        "oil": 60,
        "rates": 40,
        "correlation": 20,
        "spy_drawdown": 100,
    }
    expected = 0.4 * 80 + 0.2 * 60 + 0.2 * 40 + 0.1 * 20 + 0.1 * 100
    assert abs(riskoff_composite(comps) - expected) < 1e-9


def test_composite_renormalises_without_a_minor_component():
    comps = {"credit": 80, "oil": 60, "rates": 40, "correlation": None}
    expected = (0.4 * 80 + 0.2 * 60 + 0.2 * 40) / 0.8
    assert abs(riskoff_composite(comps) - expected) < 1e-9


def test_composite_needs_credit_and_three_components():
    assert riskoff_composite({"oil": 90, "rates": 90, "correlation": 90}) is None
    assert riskoff_composite({"credit": 90, "oil": 90}) is None
    assert riskoff_composite({"credit": float("nan"), "oil": 9, "rates": 9}) is None


def test_threshold_needs_enough_history():
    assert threshold_value([50.0] * (MIN_HISTORY_DAYS - 1), 75) is None
    hist = [float(i) for i in range(101)]  # 0..100
    assert abs(threshold_value(hist, 75) - 75.0) < 1e-9


def test_throttle_only_at_or_above_threshold():
    hist = [float(i) for i in range(101)]
    assert throttle_factor(74.9, hist, 75, 0.5) == 1.0
    assert throttle_factor(75.0, hist, 75, 0.5) == 0.5
    assert throttle_factor(99.0, hist, 75, 0.5) == 0.5


def test_throttle_fail_open_and_clamped():
    hist = [float(i) for i in range(101)]
    assert throttle_factor(None, hist, 75, 0.5) == 1.0
    assert throttle_factor(float("nan"), hist, 75, 0.5) == 1.0
    assert throttle_factor(99.0, [50.0] * 10, 75, 0.5) == 1.0  # short history
    assert throttle_factor(99.0, hist, 75, 0.01) == 0.1  # never below 0.1
    assert throttle_factor(99.0, hist, 75, 3.0) == 1.0  # never an up-size


def _synthetic_closes(days=160):
    idx = pd.bdate_range("2026-01-05", periods=days)
    base = pd.Series(range(days), index=idx, dtype=float)
    closes = {
        "HYG": 80 + base * 0.01,
        "TLT": 90 + base * 0.01,
        "USO": 70 + base * 0.01,
        "SPY": 600 + base * 0.5,
    }
    # last week: credit and treasuries sell off, oil spikes, SPY dips => risk-off
    for sym, shock in (("HYG", -3.0), ("TLT", -4.0), ("USO", 8.0), ("SPY", -25.0)):
        s = closes[sym].copy()
        s.iloc[-5:] = s.iloc[-5:] + shock
        closes[sym] = s
    return closes


def test_compute_state_flags_a_credit_rates_oil_shock():
    state = compute_regime_state(_synthetic_closes())
    assert state["score"] is not None and state["score"] > 80
    assert state["asof"] is not None
    assert len(state["history"]) >= MIN_HISTORY_DAYS
    assert set(state["components"]) >= {"credit", "rates", "oil", "spy_drawdown"}


def test_compute_state_without_credit_has_no_reading():
    closes = _synthetic_closes()
    closes.pop("HYG")
    assert compute_regime_state(closes)["score"] is None


def test_freshness_is_sim_safe():
    state = {"asof": "2026-09-10"}
    assert is_fresh(state, date(2026, 9, 10))
    assert is_fresh(state, date(2026, 9, 14))  # long weekend
    assert not is_fresh(state, date(2026, 9, 15))  # too old
    assert not is_fresh(state, date(2024, 5, 1))  # simulated past: cache is "future"
    assert not is_fresh(None, date(2026, 9, 10))
    assert not is_fresh({"asof": "garbage"}, date(2026, 9, 10))


def test_refresh_writes_cache_and_survives_fetch_errors(tmp_path):
    closes = _synthetic_closes()

    def fetch(sym):
        if sym == "XLK":
            raise RuntimeError("feed down")
        return closes.get(sym)

    out = tmp_path / "regime_signal.json"
    state = refresh_regime_cache(fetch, str(out))
    assert state["score"] is not None
    assert json.loads(out.read_text(encoding="utf-8"))["asof"] == state["asof"]
    assert load_regime_state(str(out))["score"] == state["score"]


def test_reading_reports_would_throttle_and_fails_open_when_stale(tmp_path):
    state = compute_regime_state(_synthetic_closes())
    asof = date.fromisoformat(state["asof"])
    live = regime_reading(asof, 75, 0.5, state=state)
    assert live["available"] and live["would_throttle"] and live["factor"] == 0.5
    stale = regime_reading(asof + timedelta(days=30), 75, 0.5, state=state)
    assert not stale["available"] and stale["factor"] == 1.0
    cold = regime_reading(asof, 75, 0.5, cache_file=str(tmp_path / "missing.json"))
    assert not cold["available"] and cold["factor"] == 1.0


# --- Review zu #3477 ---------------------------------------------------------


def test_finding_01_write_is_atomic_and_leaves_no_temp_files(tmp_path, monkeypatch):
    """A reader must never see a half-written file: the JSON goes to a temp file in
    the same directory and is swapped in with os.replace."""
    import core.engine.regime_signal as rs

    closes = _synthetic_closes()
    out = tmp_path / "regime_signal.json"
    out.write_text(json.dumps({"asof": "2026-01-01", "score": 1.0}), encoding="utf-8")
    replaced = []
    real_replace = rs.os.replace

    def spy(src, dst):
        # at swap time the OLD file is still complete and readable
        assert json.loads(out.read_text(encoding="utf-8"))["score"] == 1.0
        replaced.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(rs.os, "replace", spy)
    refresh_regime_cache(closes.get, str(out))
    assert len(replaced) == 1 and replaced[0][1] == str(out)
    assert [p.name for p in tmp_path.iterdir()] == ["regime_signal.json"]


def test_finding_01_a_failed_write_keeps_the_old_cache_and_cleans_up(
    tmp_path, monkeypatch
):
    import core.engine.regime_signal as rs

    out = tmp_path / "regime_signal.json"
    out.write_text(json.dumps({"asof": "2026-01-01", "score": 1.0}), encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(rs.os, "replace", boom)
    refresh_regime_cache(_synthetic_closes().get, str(out))  # must not raise
    assert json.loads(out.read_text(encoding="utf-8"))["score"] == 1.0
    assert [p.name for p in tmp_path.iterdir()] == ["regime_signal.json"]


def test_finding_01_concurrent_refreshes_never_corrupt_the_cache(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    closes = _synthetic_closes()
    out = str(tmp_path / "regime_signal.json")
    broken = []

    def writer(_):
        refresh_regime_cache(closes.get, out)

    def reader(_):
        for _i in range(40):
            try:
                with open(out, encoding="utf-8") as fh:
                    json.load(fh)
            except FileNotFoundError:
                pass
            except PermissionError:
                pass  # Windows: a swap in flight — load_regime_state reads as cold
            except ValueError as exc:
                broken.append(exc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(writer, range(3)))
        futures = [pool.submit(writer, i) for i in range(3)]
        futures += [pool.submit(reader, i) for i in range(3)]
        for f in futures:
            f.result()
    assert not broken, f"reader saw malformed JSON: {broken[:1]}"
    assert load_regime_state(out)["score"] is not None


def test_finding_02_every_fallback_log_carries_the_stack_trace(tmp_path, caplog):
    import logging

    def fetch(sym):
        raise RuntimeError("feed down")

    bad = tmp_path / "regime_signal.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="core.engine.regime_signal"):
        load_regime_state(str(bad))
        refresh_regime_cache(fetch, str(tmp_path / "sub" / "x.json"))
    records = [r for r in caplog.records if r.name == "core.engine.regime_signal"]
    assert records, "fallbacks must be logged"
    # "no reading computed" is a STATE line (no exception exists there); every other
    # warning here is an except-branch and must keep its stack trace.
    missing = [
        r.getMessage()
        for r in records
        if not r.exc_info and "no reading computed" not in r.getMessage()
    ]
    assert len(records) - 1 >= 2  # per-symbol fetch failures + the unreadable cache
    assert not missing, f"fallback logged without exc_info: {missing}"


def test_a_failed_compute_never_overwrites_a_good_cache_or_blocks_the_day(tmp_path):
    """One transient data gap must not stamp the day as done with an EMPTY reading —
    that would switch the throttle off until tomorrow."""
    from core.engine.regime_signal import ensure_fresh_state

    out = str(tmp_path / "regime_signal.json")
    today = date(2026, 9, 18)
    good = refresh_regime_cache(_synthetic_closes().get, out)

    state = ensure_fresh_state(lambda sym: None, today, out)  # feed down
    assert state["score"] is None
    assert load_regime_state(out)["score"] == good["score"]  # old reading kept

    closes = _synthetic_closes()
    state = ensure_fresh_state(closes.get, today, out, retry_after_sec=0)
    assert state["score"] is not None and state["computed_on"] == today.isoformat()


def test_swap_is_retried_when_windows_holds_the_file(tmp_path, monkeypatch):
    """PermissionError (WinError 32) from ANOTHER process holding the target must not
    cost the day's reading."""
    import core.engine.regime_signal as rs

    real = rs.os.replace
    attempts = []

    def flaky(src, dst):
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(32, "used by another process")
        return real(src, dst)

    monkeypatch.setattr(rs.os, "replace", flaky)
    monkeypatch.setattr(rs.time, "sleep", lambda s: None)
    out = tmp_path / "regime_signal.json"
    state = refresh_regime_cache(_synthetic_closes().get, str(out))
    assert len(attempts) == 3
    assert load_regime_state(str(out))["score"] == state["score"]
    assert [p.name for p in tmp_path.iterdir()] == ["regime_signal.json"]


def test_reader_and_swap_share_one_lock_and_it_is_not_the_refresh_lock():
    import inspect

    import core.engine.regime_signal as rs

    assert "_FILE_LOCK" in inspect.getsource(rs.load_regime_state)
    assert "_FILE_LOCK" in inspect.getsource(rs._atomic_write_json)
    # ensure_fresh_state holds _REFRESH_LOCK while it loads and writes: a shared,
    # non-reentrant lock would deadlock the first refresh of the day.
    assert rs._FILE_LOCK is not rs._REFRESH_LOCK


def test_finding_03_only_real_finite_numbers_count():
    """Explicit number check instead of ``v == v``: NaN, inf, bool, text and None are
    all 'no value' — for a component, for the history and for the score itself."""
    inf = float("inf")
    bad = {"credit": 80, "oil": inf, "rates": "n/a", "correlation": True}
    assert riskoff_composite(bad) is None  # only credit is usable -> fewer than 3
    ok = {"credit": 80, "oil": 60, "rates": 40, "correlation": inf}
    assert abs(riskoff_composite(ok) - (0.4 * 80 + 0.2 * 60 + 0.2 * 40) / 0.8) < 1e-9
    hist = [float(i) for i in range(101)] + [inf, float("nan"), None]
    assert abs(threshold_value(hist, 75) - 75.0) < 1e-9
    assert throttle_factor(inf, hist, 75, 0.5) == 1.0
    assert throttle_factor("85", hist, 75, 0.5) == 0.5  # numeric text is a number
