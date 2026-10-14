"""#3096 Step-2 — benchmark-relative Metriken im Sweep-Evaluator (pure Ausrichtung).

Richtet die Strategie-Intervallrenditen an SPY über DIESELBEN Datumspaare aus und
rechnet IR/TE; ehrliches None ohne SPY oder bei < 2 ausgerichteten Paaren.
"""

from research.evaluate import benchmark_relative_metrics, evaluate_run


def _equity():
    return [
        {"date": "2026-01-02", "equity": 100.0},
        {"date": "2026-01-03", "equity": 102.0},
        {"date": "2026-01-04", "equity": 101.5},
    ]


def _spy():
    return {"2026-01-02": 400.0, "2026-01-03": 404.0, "2026-01-04": 400.0}


def test_benchmark_relative_beats_spy_positive_ir():
    m = benchmark_relative_metrics(_equity(), _spy())
    # strategy beat SPY on BOTH intervals -> positive active return + IR > 0
    assert m["active_return_pct"] > 0
    assert m["information_ratio"] > 0
    assert m["tracking_error"] is not None


def test_no_spy_map_yields_none():
    m = benchmark_relative_metrics(_equity(), None)
    assert m == {
        "active_return_pct": None,
        "tracking_error": None,
        "information_ratio": None,
    }


def test_fewer_than_two_pairs_yields_none():
    one = [
        {"date": "2026-01-02", "equity": 100.0},
        {"date": "2026-01-03", "equity": 101.0},
    ]
    # only 1 aligned pair -> IR/TE undefined
    m = benchmark_relative_metrics(one, {"2026-01-02": 400.0, "2026-01-03": 404.0})
    assert m["information_ratio"] is None
    assert m["tracking_error"] is None


def test_evaluate_run_passes_through_benchmark():
    result = {"daily_equity": _equity(), "orders": [], "starting_equity": 100.0}
    out = evaluate_run(result, cost_bps=5.0, spy_close_by_date=_spy())
    assert "information_ratio" in out and "tracking_error" in out
    assert out["information_ratio"] > 0


def test_evaluate_run_without_spy_has_none_benchmark():
    result = {"daily_equity": _equity(), "orders": [], "starting_equity": 100.0}
    out = evaluate_run(result, cost_bps=5.0)
    assert out["information_ratio"] is None
    assert out["active_return_pct"] is None
